"""Fetch package metadata from the official Arch Linux repositories and the AUR.

Three sources, all public and unauthenticated:

``core`` / ``extra``
    The official repositories are published as gzipped tarballs of per-package
    ``<name>-<version>/desc`` files. Each ``desc`` is a simple ``%KEY%`` +
    value-lines format. Downloading the database is far cheaper than walking
    the web UI: one request yields every package.

``aur``
    The AUR exposes an RPC interface. ``/rpc/v5/info`` is used for a bounded
    set of names, and the bulk ``packages.gz`` listing (name, version,
    description, maintainer, votes, ...) for the full set.

Only the standard library is used, so the scraper has no import-time
dependencies beyond what ``requirements.txt`` already pins.

Status — what is and is not collected
-------------------------------------

======================  ========  ==========================================
source                  status    notes
======================  ========  ==========================================
``core``                complete  299 packages
``extra``               complete  15,025 packages
``AUR``                 PARTIAL   ~14,200 of ~121,445 before throttling
======================  ========  ==========================================

TODO(aur-full-crawl): the AUR sweep is incomplete. The AUR began refusing
connections partway through (a fixed 10s connect timeout in the HTTP client)
and then throttled the host. ``fetch_aur_bulk`` documents the plan for
finishing it in resumable chunks. The committed dataset holds core + extra
only; README.md carries the same status.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import subprocess
import sys
import tarfile
import time
import urllib.parse
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "AUR_RPC_URL",
    "AUR_PACKAGES_URL",
    "DEFAULT_MIRROR",
    "MIRRORS",
    "ArchPackage",
    "parse_desc",
    "parse_aur_packages_gz",
    "parse_aur_rpc",
    "fetch_official_repo",
    "fetch_aur_names",
    "fetch_aur_bulk",
    "fetch_aur_search",
    "fetch_aur_info",
    "scrape",
    "write_arch_jsonl",
]

#: Official Arch mirrors, tried in order. The public mirrors are intermittently
#: unreachable, so more than one is needed for a reliable scrape.
MIRRORS: tuple[str, ...] = (
    "https://geo.mirror.pkgbuild.com",
    "https://fastly.mirror.pkgbuild.com",
    "https://mirror.rackspace.com/archlinux",
    "https://mirrors.kernel.org/archlinux",
)

DEFAULT_MIRROR = MIRRORS[0]
AUR_RPC_URL = "https://aur.archlinux.org/rpc/v5/info"
AUR_PACKAGES_URL = "https://aur.archlinux.org/packages.gz"

#: Repository root, used to locate the Node fetch helper.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FETCH_SCRIPT = _REPO_ROOT / "tools" / "fetch.js"

USER_AGENT = "msys2-dataset/0.1 (+https://github.com/fufurobot/msys2-dataset)"

#: Official repositories to scrape by default.
DEFAULT_REPOS = ("core", "extra")

#: ``desc`` sections whose values are single scalars rather than lists.
_SCALAR_KEYS = {"FILENAME", "NAME", "VERSION", "DESC", "BASE", "URL", "PACKAGER"}


@dataclass
class ArchPackage:
    """One package from an official Arch repository or the AUR."""

    name: str
    version: str = ""
    repo: str = ""
    description: str = ""
    url: str = ""
    license: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    depends: list[str] = field(default_factory=list)
    makedepends: list[str] = field(default_factory=list)
    optdepends: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    replaces: list[str] = field(default_factory=list)
    base: str = ""
    packager: str = ""
    # AUR-only fields.
    votes: int = 0
    popularity: float = 0.0
    maintainer: str | None = None
    out_of_date: int | None = None
    num_depends: int = 0

    @property
    def key(self) -> str:
        """Dependency name, stripped of version constraints."""
        return dependency_name(self.name)

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


#: Matches a leading dependency name, dropping ``>=``/``=``/``<`` constraints.
_DEP_SPLIT = re.compile(r"^([^<>=]+)")


def dependency_name(spec: str) -> str:
    """``glibc>=2.38`` -> ``glibc``; ``libfoo.so=1-64`` -> ``libfoo.so``."""
    return _DEP_SPLIT.match(spec.strip()).group(1).strip().lower()  # type: ignore[union-attr]


def _request(
    url: str,
    timeout: int = 60,
    mirrors: Sequence[str] | None = None,
    method: str = "GET",
    form: dict[str, Any] | None = None,
) -> bytes:
    """Fetch ``url``, with optional mirror fallbacks and form-encoded POST.

    Network access is delegated to ``tools/fetch.js``: Python's `urllib` cannot
    open sockets to the Arch hosts in some confined environments (it fails with
    ``[Errno 2] No such file or directory``), while Node's TLS stack can. The
    retry/mirror-rotation logic lives there too, so this stays a thin call.

    POST is used for the AUR RPC because a GET query string with hundreds of
    repeated ``arg[]`` parameters is rejected as too long.
    """
    if mirrors:
        urls = list(mirrors) + [url]
    else:
        urls = [url]

    # The system temp directory is not writable in some confined environments
    # (including the DSH sandbox), so stage the response inside the repository.
    staging = _REPO_ROOT / ".fetch-cache"
    staging.mkdir(parents=True, exist_ok=True)
    out = staging / f"body-{abs(hash((url, method))) & 0xFFFFFFFF:08x}.bin"
    try:
        payload: dict[str, Any] = {"urls": urls, "out": str(out), "timeout_ms": timeout * 1000}
        if method.upper() == "POST":
            payload["method"] = "POST"
            payload["form"] = form or {}
        result = subprocess.run(
            ["node", str(_FETCH_SCRIPT), json.dumps(payload)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not out.exists():
            raise OSError(
                f"fetch failed for {url}:\n{result.stderr.strip() or result.stdout.strip()}"
            )
        return out.read_bytes()
    finally:
        out.unlink(missing_ok=True)


def parse_desc(text: str) -> dict[str, Any]:
    """Parse one Arch ``desc`` file into a dict.

    Format::

        %NAME%
        zlib

        %DEPENDS%
        glibc
        bash

    Keys are lowercased. Multi-value sections become lists; the known scalar
    sections become plain strings.
    """
    sections: dict[str, Any] = {}
    current: str | None = None
    values: list[str] = []

    def flush() -> None:
        if current is None:
            return
        if current in {k.lower() for k in _SCALAR_KEYS}:
            sections[current] = values[0] if values else ""
        else:
            sections[current] = list(values)

    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("%") and line.endswith("%"):
            flush()
            current = line.strip("%").lower()
            values = []
        elif current is not None:
            values.append(line)
    flush()
    return sections


def _iter_desc_members(blob: bytes) -> Iterator[tuple[str, str]]:
    """Yield ``(package_name, desc_text)`` from a repo db tarball."""
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Members are "<name>-<version>/desc"; match the component exactly
            # so a sibling like "zdesc" is not mistaken for a desc file.
            if member.name.rsplit("/", 1)[-1] != "desc":
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            text = handle.read().decode("utf-8", errors="replace")
            # Directory component is "<name>-<version>"; prefer %NAME% inside.
            fallback = member.name.split("/", 1)[0]
            yield fallback, text


def fetch_official_repo(
    repo: str = "core",
    mirror: str | None = None,
    arch: str = "x86_64",
) -> list[ArchPackage]:
    """Download and parse one official repository database.

    ``mirror`` may be a single base URL or a sequence of them; when omitted,
    every entry in :data:`MIRRORS` is tried in order.
    """
    if mirror is None:
        bases: Sequence[str] = MIRRORS
    elif isinstance(mirror, str):
        bases = (mirror,)
    else:
        bases = tuple(mirror)

    primary = f"{bases[0]}/{repo}/os/{arch}/{repo}.db"
    candidates = [f"{base}/{repo}/os/{arch}/{repo}.db" for base in bases]
    blob = _request(primary, mirrors=candidates)
    raw = gzip.decompress(blob)

    packages: list[ArchPackage] = []
    for fallback, text in _iter_desc_members(raw):
        fields = parse_desc(text)
        name = fields.get("name") or fallback.rsplit("-", 1)[0]
        packages.append(
            ArchPackage(
                name=name,
                version=fields.get("version", ""),
                repo=repo,
                description=fields.get("desc", ""),
                url=fields.get("url", ""),
                license=_as_list(fields.get("license")),
                groups=_as_list(fields.get("groups")),
                provides=_as_list(fields.get("provides")),
                depends=_as_list(fields.get("depends")),
                makedepends=_as_list(fields.get("makedepends")),
                optdepends=_as_list(fields.get("optdepends")),
                conflicts=_as_list(fields.get("conflicts")),
                replaces=_as_list(fields.get("replaces")),
                base=fields.get("base", ""),
                packager=fields.get("packager", ""),
            )
        )
    return packages


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return list(value)


def parse_aur_packages_gz(blob: bytes) -> list[str]:
    """Parse the AUR bulk listing into package **names**.

    Despite the name, ``packages.gz`` is a gzipped newline-separated list of
    package names — it carries no metadata. It is used to discover the full set
    of AUR package names, which are then enriched through the RPC.
    """
    text = gzip.decompress(blob).decode("utf-8", errors="replace")
    return [line.strip() for line in text.split("\n") if line.strip()]


def fetch_aur_names() -> list[str]:
    """Download the full list of AUR package names."""
    return parse_aur_packages_gz(_request(AUR_PACKAGES_URL, timeout=120))


def parse_aur_rpc(payload: dict[str, Any]) -> list[ArchPackage]:
    """Convert one AUR RPC response body into :class:`ArchPackage` objects."""
    packages: list[ArchPackage] = []
    for item in payload.get("results", []):
        packages.append(
            ArchPackage(
                name=item.get("Name", ""),
                version=item.get("Version", ""),
                repo="aur",
                description=item.get("Description") or "",
                url=item.get("URL") or "",
                license=_as_list(item.get("License")),
                provides=_as_list(item.get("Provides")),
                depends=_as_list(item.get("Depends")),
                makedepends=_as_list(item.get("MakeDepends")),
                optdepends=_as_list(item.get("OptDepends")),
                conflicts=_as_list(item.get("Conflicts")),
                replaces=_as_list(item.get("Replaces")),
                base=item.get("PackageBase") or "",
                maintainer=item.get("Maintainer"),
                votes=_to_int(str(item.get("NumVotes", 0))),
                popularity=_to_float(str(item.get("Popularity", 0.0))),
                out_of_date=item.get("OutOfDate"),
                num_depends=len(_as_list(item.get("Depends"))),
            )
        )
    return packages


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fetch_aur_listing() -> list[ArchPackage]:
    """Download and parse the full AUR package listing."""
    return parse_aur_packages_gz(_request(AUR_PACKAGES_URL, timeout=120))


def fetch_aur_info(
    names: Sequence[str],
    batch: int = 100,
    on_error: str = "skip",
    pause: float = 0.25,
) -> list[ArchPackage]:
    """Query the AUR RPC for specific package names, in batches.

    Uses POST rather than GET: with 100+ names a GET query string runs to
    several kilobytes, which the AUR rejects. Returns full metadata
    (dependencies, votes, maintainer). An empty ``names`` list makes no request.

    ``pause`` sleeps briefly between batches. The AUR throttles sustained
    request rates, and without this a long sweep starts failing to connect
    partway through.

    ``on_error`` controls what happens when a batch fails after retries:
    ``skip`` (default) logs and continues, so one bad batch cannot lose an
    entire sweep; ``raise`` propagates.
    """
    if not names:
        return []

    results: list[ArchPackage] = []
    for start in range(0, len(names), batch):
        chunk = list(names[start : start + batch])
        if start and pause:
            time.sleep(pause)
        try:
            payload = json.loads(
                _request(AUR_RPC_URL, method="POST", form={"arg[]": chunk}, timeout=120).decode(
                    "utf-8"
                )
            )
        except OSError as exc:
            if on_error == "raise":
                raise
            print(
                f"    warning: skipping {len(chunk)} names at offset {start}: {exc}",
                file=sys.stderr,
            )
            continue
        results.extend(parse_aur_rpc(payload))
    return results


def fetch_aur_search(
    term: str,
    by: str = "name-desc",
    max_pages: int = 25,
) -> list[ArchPackage]:
    """Search the AUR and return full metadata for the matches.

    The RPC rejects very short terms, so ``term`` must be at least a couple of
    characters. Pagination advances by 100 results per request.
    """
    packages: list[ArchPackage] = []
    offset = 0
    for _ in range(max_pages):
        url = (
            f"https://aur.archlinux.org/rpc/v5/search/{urllib.parse.quote(term)}"
            f"?by={by}&offset={offset}"
        )
        payload = json.loads(_request(url, timeout=120).decode("utf-8"))
        batch = parse_aur_rpc(payload)
        packages.extend(batch)
        total = int(payload.get("resultcount", 0))
        offset += 100
        if not batch or offset >= total:
            break
    return packages


def fetch_aur_bulk(
    names: Sequence[str] | None = None,
    batch: int = 100,
    limit: int | None = None,
    progress: bool = False,
    pause: float = 0.25,
) -> list[ArchPackage]:
    """Fetch AUR metadata for many packages by name, in batched RPC calls.

    The AUR has no "list everything" RPC: its search endpoint rejects short
    terms and paging it does not enumerate the namespace. The reliable route is
    the name list from ``packages.gz`` fed through the batched ``info``
    endpoint, which is what this does.

    ``limit`` caps how many names are queried. The namespace is ~121k packages,
    so an unbounded sweep is ~1200 requests and takes well over 20 minutes.

    TODO(aur-full-crawl): the full 121k-package AUR sweep has NOT been
        completed. A run reached ~14,200 packages before the AUR began
        refusing connections (undici's fixed 10s connect timeout), and the
        host then throttled this IP for a sustained period. To finish it:

          * crawl in resumable chunks with ``--aur-pages``/``limit`` and
            checkpoint the partial result, rather than one long request loop;
          * raise the connect timeout (undici ships no standalone ``Agent``
            here, so this likely means a different HTTP client or a custom
            dispatcher bundled with the tooling);
          * back off harder on connect failures and resume from the last
            successful offset instead of restarting.
        The committed data currently contains core + extra only; see the
        status table in README.md.
    """
    if names is None:
        names = fetch_aur_names()
    selected = list(names)[: limit if limit is not None else None]

    packages: list[ArchPackage] = []
    for start in range(0, len(selected), batch):
        chunk = selected[start : start + batch]
        packages.extend(fetch_aur_info(chunk, batch=batch, pause=pause))
        if progress and (start // batch) % 10 == 0:
            print(f"    aur {len(packages)}/{len(selected)}", flush=True)
    return packages


def scrape(
    repos: Iterable[str] = DEFAULT_REPOS,
    mirror: str | None = None,
    include_aur: bool = True,
    arch: str = "x86_64",
    aur_limit: int | None = None,
    progress: bool = True,
) -> list[ArchPackage]:
    """Scrape the requested official repositories and optionally the AUR.

    ``aur_limit`` caps how many AUR packages to pull (by RPC page); ``None``
    fetches the full namespace, which is several minutes of requests.
    """
    packages: list[ArchPackage] = []
    for repo in repos:
        if progress:
            print(f"  fetching {repo}...", flush=True)
        found = fetch_official_repo(repo, mirror=mirror, arch=arch)
        if progress:
            print(f"    {len(found)} packages", flush=True)
        packages.extend(found)

    if include_aur:
        if progress:
            print("  fetching aur name list...", flush=True)
        names = fetch_aur_names()
        if progress:
            print(f"    {len(names)} aur package names", flush=True)
        aur = fetch_aur_bulk(names=names, limit=aur_limit, progress=progress)
        if progress:
            print(f"    {len(aur)} aur packages with metadata", flush=True)
        packages.extend(aur)
    return packages


def write_arch_jsonl(packages: Sequence[ArchPackage], path: str | Path) -> Path:
    """Write packages as JSON Lines, sorted by (repo, name)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for package in sorted(packages, key=lambda p: (p.repo, p.name)):
            handle.write(json.dumps(package.as_row(), ensure_ascii=False))
            handle.write("\n")
    return target

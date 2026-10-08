"""Parse MSYS2 / MINGW package listings and pair them.

``data/msys2-pkgs.tsv`` and ``data/mingw-pkgs.tsv`` are ``ls -la`` listings of
the two upstream package repositories. Only directories are packages; the
``total`` line and plain files (``LICENSE``, ``README.md``) are metadata.

Pairing is by normalised name, so ``mingw-w64-zlib`` pairs with the MSYS2
``zlib`` package. Rows present on only one side are kept, because a MINGW-only
package is itself a meaningful observation for this dataset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "PackageEntry",
    "PackagePair",
    "normalise_name",
    "pair_packages",
    "parse_ls_listing",
    "parse_ls_listing_file",
    "summarise",
]

# ``ls -la`` long-format line, e.g.
#   drwxr-xr-x 1 fufu fufu    0 Sep 24 04:49 bash
_LS_RE = re.compile(
    r"""^(?P<perms>[-dlbcps][rwxSsTt-]{9})\s+   # mode
        (?P<links>\d+)\s+                       # hard links
        (?P<owner>\S+)\s+                       # owner
        (?P<group>\S+)\s+                       # group
        (?P<size>\d+)\s+                        # size
        (?P<date>[A-Z][a-z]{2}\s+\d{1,2}\s+(?:\d{2}:\d{2}|\d{4}))\s+  # mtime
        (?P<name>.+?)\s*$                       # name (may contain spaces)
    """,
    re.VERBOSE,
)

#: Prefixes that MINGW package directories carry but MSYS2 ones do not.
_MINGW_PREFIX_RE = re.compile(
    r"^(?:mingw-w64-)(?:(?:ucrt|clang|clangarm64|clang-aarch64)-)?(?:x86_64-|i686-|aarch64-)?"
)


@dataclass(frozen=True)
class PackageEntry:
    """One package directory parsed from an ``ls -la`` listing."""

    name: str
    size: int = 0
    date: str = ""
    owner: str = ""
    group: str = ""

    @property
    def key(self) -> str:
        """The normalised name used for cross-repo pairing."""
        return normalise_name(self.name)


@dataclass(frozen=True)
class PackagePair:
    """A pairing between an MSYS2 package and a MINGW package.

    Either side may be ``None`` when the package exists in only one repository.
    """

    name: str
    msys2: PackageEntry | None = None
    mingw: PackageEntry | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def paired(self) -> bool:
        return self.msys2 is not None and self.mingw is not None

    def as_row(self) -> dict[str, object]:
        """Flatten to a JSONL/CSV-friendly row."""
        return {
            "name": self.name,
            "msys2_name": self.msys2.name if self.msys2 else None,
            "mingw_name": self.mingw.name if self.mingw else None,
            "paired": self.paired,
            "aliases": list(self.aliases),
        }


def parse_ls_listing(text: str) -> list[PackageEntry]:
    """Parse ``ls -la`` output, returning only directory entries."""
    entries: list[PackageEntry] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line:
            continue
        match = _LS_RE.match(line)
        if match is None:
            continue
        if not match.group("perms").startswith("d"):
            continue
        entries.append(
            PackageEntry(
                name=match.group("name").strip(),
                size=int(match.group("size")),
                date=match.group("date"),
                owner=match.group("owner"),
                group=match.group("group"),
            )
        )
    return entries


def parse_ls_listing_file(path: str | Path) -> list[PackageEntry]:
    """Parse an ``ls -la`` listing stored on disk."""
    return parse_ls_listing(Path(path).read_text(encoding="utf-8"))


def normalise_name(name: str) -> str:
    """Normalise a package name for cross-repository comparison.

    Strips the MINGW toolchain prefix and lowercases::

        mingw-w64-ucrt-x86_64-zlib -> zlib
        mingw-w64-MinHook          -> minhook
        bash                       -> bash
    """
    result = name.strip()
    stripped = _MINGW_PREFIX_RE.sub("", result)
    # Only apply when something actually matched, so a plain MSYS2 package
    # whose name merely starts with "mingw" is left intact.
    if stripped and stripped != result:
        result = stripped
    return result.lower()


def pair_packages(
    msys2: Iterable[PackageEntry],
    mingw: Iterable[PackageEntry],
) -> list[PackagePair]:
    """Pair MSYS2 and MINGW packages by normalised name, sorted by name."""
    by_key: dict[str, PackagePair] = {}
    aliases: dict[str, set[str]] = {}

    for entry in msys2:
        key = entry.key
        aliases.setdefault(key, set()).add(entry.name)
        by_key[key] = PackagePair(name=key, msys2=entry)

    for entry in mingw:
        key = entry.key
        aliases.setdefault(key, set()).add(entry.name)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = PackagePair(name=key, mingw=entry)
        else:
            by_key[key] = PackagePair(
                name=key, msys2=existing.msys2, mingw=entry
            )

    pairs: list[PackagePair] = []
    for key in sorted(by_key):
        pair = by_key[key]
        extra = tuple(sorted(aliases.get(key, set())))
        pairs.append(
            PackagePair(
                name=pair.name,
                msys2=pair.msys2,
                mingw=pair.mingw,
                aliases=extra,
            )
        )
    return pairs


def summarise(pairs: Sequence[PackagePair]) -> dict[str, int]:
    """Count paired / MSYS2-only / MINGW-only packages."""
    paired = sum(1 for p in pairs if p.paired)
    msys2_only = sum(1 for p in pairs if p.msys2 is not None and p.mingw is None)
    mingw_only = sum(1 for p in pairs if p.mingw is not None and p.msys2 is None)
    return {
        "total": len(pairs),
        "paired": paired,
        "msys2_only": msys2_only,
        "mingw_only": mingw_only,
    }

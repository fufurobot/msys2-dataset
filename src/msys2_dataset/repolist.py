"""Parse ``repo-list.txt`` and plan the ``data/repo/`` git submodules.

The ``data/repo/`` directory is *not* a plain directory of checkouts: every
entry is a real git submodule registered in ``.gitmodules`` and pinned to a
commit, so a fresh ``git clone --recurse-submodules`` reproduces the exact
upstream revisions the dataset was built from.

``repo-list.txt`` is the single source of truth for which submodules exist::

    # one URL per line; optional second column overrides the directory name
    https://github.com/msys2/msys2-packages.git
    https://github.com/msys2/MINGW-packages.git
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "RepoEntry",
    "SubmoduleSpec",
    "parse_repo_list",
    "parse_repo_list_text",
    "read_repo_list",
    "repo_dir_name",
    "build_submodule_plan",
    "SUBMODULE_ROOT",
]

#: Directory (POSIX-style, relative to the repo root) holding the submodules.
SUBMODULE_ROOT = "data/repo"

_URL_RE = re.compile(
    r"""^(?:
        https?://[^\s/]+/.+          # https://host/path
      | ssh://[^\s/]+/.+             # ssh://host/path
      | git://[^\s/]+/.+             # git://host/path
      | [^\s@]+@[^\s:]+:.+           # git@host:path
    )$""",
    re.VERBOSE,
)


@dataclass(frozen=True, order=True)
class RepoEntry:
    """A single remote repository declared in ``repo-list.txt``."""

    url: str
    name: str


@dataclass(frozen=True)
class SubmoduleSpec:
    """Where a :class:`RepoEntry` must be mounted and under which path."""

    url: str
    path: str  # POSIX-style relative path, e.g. "data/repo/msys2-packages"

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def repo_dir_name(url: str) -> str:
    """Derive a directory name from a repository URL.

    ``https://github.com/msys2/msys2-packages.git`` -> ``msys2-packages``
    """
    trimmed = url.strip().rstrip("/")
    # Strip a trailing .git, then take the last path/URL segment.
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    tail = re.split(r"[/\\]", trimmed)[-1]
    # ``git@host:path`` form may leave the colon attached.
    tail = tail.rsplit(":", 1)[-1]
    return tail


def _parse_line(line: str) -> RepoEntry | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parts = line.split()
    if len(parts) > 2:
        raise ValueError(f"repo-list entry has too many fields: {line!r}")

    url = parts[0]
    name = parts[1] if len(parts) == 2 else repo_dir_name(url)

    if not _URL_RE.match(url):
        raise ValueError(f"repo-list entry is not a recognised URL: {line!r}")
    if not name or name in {".", ".."} or re.search(r"[/\\]", name):
        raise ValueError(f"repo-list entry has an invalid name: {name!r}")

    return RepoEntry(url=url, name=name)


def parse_repo_list_text(text: str) -> list[RepoEntry]:
    """Parse the contents of a ``repo-list.txt`` file.

    Blank lines and ``#`` comments are ignored. Lines may carry an optional
    second column overriding the derived directory name. ``CRLF`` endings are
    tolerated because the file is authored on Windows.
    """
    entries: list[RepoEntry] = []
    seen: set[str] = set()
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        entry = _parse_line(raw)
        if entry is None:
            continue
        if entry.name in seen:
            raise ValueError(f"duplicate repository name in repo-list: {entry.name!r}")
        seen.add(entry.name)
        entries.append(entry)
    return entries


def parse_repo_list(path: str | Path) -> list[RepoEntry]:
    """Alias of :func:`read_repo_list` kept for readability at call sites."""
    return read_repo_list(path)


def read_repo_list(path: str | Path) -> list[RepoEntry]:
    """Read and parse a ``repo-list.txt`` file from disk."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"repo-list not found: {p}")
    return parse_repo_list_text(p.read_text(encoding="utf-8"))


def build_submodule_plan(
    entries: Sequence[RepoEntry] | Iterable[RepoEntry],
    root: str = SUBMODULE_ROOT,
) -> list[SubmoduleSpec]:
    """Map entries onto their submodule mount points.

    Paths are emitted with POSIX separators so the plan can be handed straight
    to ``git submodule add`` on Linux CI as well as on Windows.
    """
    root = root.strip("/")
    return [SubmoduleSpec(url=e.url, path=f"{root}/{e.name}") for e in entries]

"""Match Arch Linux (core/extra/AUR) packages against MSYS2 and MINGW.

The dataset's question is: *for a given upstream project, which Arch package and
which MSYS2/MINGW package ship it?* MSYS2 and MINGW packages are **not**
designed to correspond to each other — they are two different porting efforts of
the same upstream software — so pairing them directly is meaningless. Arch
Linux is the common reference point.

Matching is by normalised upstream name, with a curated alias table for the
cases where the Arch and MSYS2 names genuinely differ:

- MINGW prefixes are stripped: ``mingw-w64-ucrt-x86_64-zlib`` -> ``zlib``
- Arch version/epoch suffixes never appear in the name itself
- ``python-<x>``, ``perl-<x>`` etc. are kept distinct from the base project,
  because they are separately packaged on both sides
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .arch import ArchPackage, dependency_name
from .packages import PackageEntry, normalise_name

__all__ = [
    "NAME_ALIASES",
    "EcosystemMatch",
    "build_arch_index",
    "match_ecosystems",
    "summarise_matches",
    "normalise_arch_name",
]

#: Upstream projects whose Arch and MSYS2/MINGW package names differ. Keys are
#: normalised Arch names; values are the normalised MSYS2/MINGW name.
NAME_ALIASES: dict[str, str] = {
    # Arch ships the reference implementation under the upstream name while
    # MSYS2 keeps the historical one (and vice versa).
    "libjpeg-turbo": "libjpeg-turbo",
    "jpeg": "libjpeg-turbo",
    "python": "python",
    "python2": "python2",
    "openssl": "openssl",
    "libressl": "libressl",
    "gtk2": "gtk2",
    "gtk3": "gtk3",
    "gtk4": "gtk4",
    "sdl": "sdl",
    "sdl2": "sdl2",
    "ninja": "ninja",
    "meson": "meson",
    "cmake": "cmake",
    "binutils": "binutils",
    "gcc": "gcc",
    "clang": "clang",
    "rust": "rust",
    "go": "go",
    "nodejs": "nodejs",
    "perl": "perl",
    "ruby": "ruby",
    "php": "php",
    "lua": "lua",
    "vim": "vim",
    "nano": "nano",
    "emacs": "emacs",
    "git": "git",
    "curl": "curl",
    "wget": "wget",
    "bash": "bash",
    "zsh": "zsh",
    "fish": "fish",
    "tmux": "tmux",
    "sqlite": "sqlite",
    "zlib": "zlib",
    "bzip2": "bzip2",
    "xz": "xz",
    "zstd": "zstd",
    "libpng": "libpng",
    "libtiff": "libtiff",
    "freetype2": "freetype",
    "fontconfig": "fontconfig",
    "harfbuzz": "harfbuzz",
    "cairo": "cairo",
    "pango": "pango",
    "gdk-pixbuf2": "gdk-pixbuf2",
    "atk": "atk",
    "glib2": "glib2",
    "boost": "boost",
    "ffmpeg": "ffmpeg",
    "gstreamer": "gstreamer",
    "qt5-base": "qt5",
    "qt6-base": "qt6",
    "protobuf": "protobuf",
    "grpc": "grpc",
    "libxml2": "libxml2",
    "libxslt": "libxslt",
    "readline": "readline",
    "ncurses": "ncurses",
    "expat": "expat",
    "pcre2": "pcre2",
    "libffi": "libffi",
    "gmp": "gmp",
    "libtool": "libtool",
    "automake": "automake",
    "autoconf": "autoconf",
    "make": "make",
    "patch": "patch",
    "tar": "tar",
    "gzip": "gzip",
    "unzip": "unzip",
    "zip": "zip",
    "rsync": "rsync",
    "openssh": "openssh",
    "gnupg": "gnupg",
    "ca-certificates": "ca-certificates",
}

#: Arch packages that are build tooling for a language ecosystem and should be
#: matched against the same-named MSYS2 package rather than the base project.
_SUFFIX_RE = re.compile(r"^(lib|python-|perl-|ruby-|lua-|php-|haskell-|ocaml-)")

#: Names that are too generic to match on, to avoid nonsense pairs like
#: Arch `base` matching MSYS2 `base`.
_STOPWORDS = frozenset(
    {
        "base",
        "base-devel",
        "filesystem",
        "linux",
        "linux-api-headers",
        "pacman",
        "pacman-mirrorlist",
        "archlinux-keyring",
        "systemd",
        "glibc",
        "gcc-libs",
        "tzdata",
        "iana-etc",
        "bash",
        "coreutils",
        "util-linux",
        "sed",
        "grep",
        "gawk",
        "findutils",
        "diffutils",
        "procps-ng",
        "psmisc",
        "e2fsprogs",
        "cryptsetup",
        "device-mapper",
        "iproute2",
        "iputils",
        "jfsutils",
        "less",
        "licenses",
        "logrotate",
        "man-db",
        "man-pages",
        "mdadm",
        "nano",
        "netctl",
        "pciutils",
        "pcmciautils",
        "reiserfsprogs",
        "s-nail",
        "sysfsutils",
        "texinfo",
        "usbutils",
        "vi",
        "which",
        "xfsprogs",
    }
)


@dataclass
class EcosystemMatch:
    """One upstream project, with whatever each ecosystem ships for it."""

    name: str
    arch: list[ArchPackage] = field(default_factory=list)
    msys2: list[PackageEntry] = field(default_factory=list)
    mingw: list[PackageEntry] = field(default_factory=list)

    @property
    def in_arch(self) -> bool:
        return bool(self.arch)

    @property
    def in_msys2(self) -> bool:
        return bool(self.msys2)

    @property
    def in_mingw(self) -> bool:
        return bool(self.mingw)

    @property
    def ecosystems(self) -> list[str]:
        found = []
        if self.in_arch:
            found.append("arch")
        if self.in_msys2:
            found.append("msys2")
        if self.in_mingw:
            found.append("mingw")
        return found

    def as_row(self) -> dict[str, object]:
        """Flatten for JSONL/table storage."""
        return {
            "name": self.name,
            "in_arch": self.in_arch,
            "in_msys2": self.in_msys2,
            "in_mingw": self.in_mingw,
            "ecosystems": self.ecosystems,
            "arch_names": [p.name for p in self.arch],
            "arch_repos": sorted({p.repo for p in self.arch}),
            "msys2_names": [p.name for p in self.msys2],
            "mingw_names": [p.name for p in self.mingw],
        }


def normalise_arch_name(name: str) -> str:
    """Normalise an Arch package name for cross-ecosystem matching."""
    base = dependency_name(name)
    mapped = NAME_ALIASES.get(base)
    return mapped if mapped else base


def build_arch_index(packages: Iterable[ArchPackage]) -> dict[str, list[ArchPackage]]:
    """Group Arch packages by normalised name."""
    index: dict[str, list[ArchPackage]] = {}
    for package in packages:
        index.setdefault(normalise_arch_name(package.name), []).append(package)
    return index


def match_ecosystems(
    arch: Iterable[ArchPackage],
    msys2: Iterable[PackageEntry],
    mingw: Iterable[PackageEntry],
    include_stopwords: bool = False,
) -> list[EcosystemMatch]:
    """Match Arch packages against MSYS2 and MINGW packages by normalised name.

    Returns one entry per distinct normalised project name, sorted by name.
    Entries present in only one ecosystem are kept, because "Arch packages this,
    MSYS2 does not" is itself the interesting signal.
    """
    arch_index = build_arch_index(arch)
    msys2_index: dict[str, list[PackageEntry]] = {}
    mingw_index: dict[str, list[PackageEntry]] = {}

    for entry in msys2:
        msys2_index.setdefault(normalise_name(entry.name), []).append(entry)
    for entry in mingw:
        mingw_index.setdefault(normalise_name(entry.name), []).append(entry)

    names = set(arch_index) | set(msys2_index) | set(mingw_index)
    matches: list[EcosystemMatch] = []
    for name in sorted(names):
        if not include_stopwords and name in _STOPWORDS:
            continue
        matches.append(
            EcosystemMatch(
                name=name,
                arch=sorted(arch_index.get(name, []), key=lambda p: (p.repo, p.name)),
                msys2=sorted(msys2_index.get(name, []), key=lambda p: p.name),
                mingw=sorted(mingw_index.get(name, []), key=lambda p: p.name),
            )
        )
    return matches


def summarise_matches(matches: Sequence[EcosystemMatch]) -> dict[str, int]:
    """Count how many projects appear in each combination of ecosystems."""
    total = len(matches)
    arch_only = sum(1 for m in matches if m.in_arch and not m.in_msys2 and not m.in_mingw)
    arch_msys2 = sum(1 for m in matches if m.in_arch and m.in_msys2)
    arch_mingw = sum(1 for m in matches if m.in_arch and m.in_mingw)
    all_three = sum(1 for m in matches if m.in_arch and m.in_msys2 and m.in_mingw)
    return {
        "total": total,
        "arch_total": sum(1 for m in matches if m.in_arch),
        "msys2_total": sum(1 for m in matches if m.in_msys2),
        "mingw_total": sum(1 for m in matches if m.in_mingw),
        "arch_matched": sum(1 for m in matches if m.in_arch and (m.in_msys2 or m.in_mingw)),
        "arch_only": arch_only,
        "arch_msys2": arch_msys2,
        "arch_mingw": arch_mingw,
        "all_three": all_three,
    }

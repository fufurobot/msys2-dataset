#!/usr/bin/env python
"""Scrape Arch Linux (core/extra/AUR) and match it against MSYS2 and MINGW.

This is the dataset's data-collection entry point. It writes:

``data/arch-pkgs.jsonl``
    Every scraped Arch/AUR package, one JSON object per line.
``data/processed/ecosystem-matches.jsonl``
    One row per upstream project, recording which ecosystems ship it.
``data/processed/ecosystem-summary.json``
    Aggregate counts for the README and CI.

Usage::

    python tools/scrape_arch.py                     # core + extra + AUR
    python tools/scrape_arch.py --no-aur            # official repos only
    python tools/scrape_arch.py --aur-pages 5       # bounded AUR sample
    python tools/scrape_arch.py --reuse             # match from cached JSONL
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from msys2_dataset.arch import (  # noqa: E402
    DEFAULT_REPOS,
    ArchPackage,
    scrape,
    write_arch_jsonl,
)
from msys2_dataset.matching import (  # noqa: E402
    match_ecosystems,
    summarise_matches,
)
from msys2_dataset.packages import (  # noqa: E402
    parse_ls_listing_file,
)

DATA = REPO_ROOT / "data"
ARCH_JSONL = DATA / "arch-pkgs.jsonl"
MSYS2_LISTING = DATA / "msys2-pkgs.tsv"
MINGW_LISTING = DATA / "mingw-pkgs.tsv"
PROCESSED = DATA / "processed"


def load_arch_jsonl(path: Path) -> list[ArchPackage]:
    """Read back a scrape produced by :func:`write_arch_jsonl`."""
    packages: list[ArchPackage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        packages.append(ArchPackage(**json.loads(line)))
    return packages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repos", nargs="*", default=list(DEFAULT_REPOS))
    parser.add_argument("--mirror", default=None, help="override the mirror list")
    parser.add_argument("--no-aur", action="store_true", help="skip the AUR")
    parser.add_argument(
        "--aur-pages",
        type=int,
        default=None,
        help="limit AUR RPC pages (100 packages each); default is the full set",
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="skip scraping and reuse the cached data/arch-pkgs.jsonl",
    )
    parser.add_argument("--out", type=Path, default=ARCH_JSONL)
    args = parser.parse_args(argv)

    if args.reuse:
        if not args.out.is_file():
            print(f"no cached scrape at {args.out}; run without --reuse", file=sys.stderr)
            return 1
        print(f"reusing {args.out}")
        arch = load_arch_jsonl(args.out)
    else:
        print("scraping arch linux...")
        arch = scrape(
            repos=args.repos,
            mirror=args.mirror,
            include_aur=not args.no_aur,
            aur_limit=None if args.aur_pages is None else args.aur_pages * 100,
        )
        write_arch_jsonl(arch, args.out)
        print(f"wrote {len(arch)} packages to {args.out}")

    print("loading msys2 and mingw listings...")
    msys2 = parse_ls_listing_file(MSYS2_LISTING)
    mingw = parse_ls_listing_file(MINGW_LISTING)
    print(f"  msys2: {len(msys2)} packages, mingw: {len(mingw)} packages")

    matches = match_ecosystems(arch, msys2, mingw)
    stats = summarise_matches(matches)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    matches_path = PROCESSED / "ecosystem-matches.jsonl"
    with matches_path.open("w", encoding="utf-8", newline="\n") as handle:
        for match in matches:
            handle.write(json.dumps(match.as_row(), ensure_ascii=False))
            handle.write("\n")
    print(f"wrote {len(matches)} rows to {matches_path}")

    summary_path = PROCESSED / "ecosystem-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "stats": stats,
                "sources": {
                    "arch_repos": args.repos,
                    "include_aur": not args.no_aur,
                },
                "counts": {
                    "arch": len(arch),
                    "msys2": len(msys2),
                    "mingw": len(mingw),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {summary_path}")

    print("\nsummary")
    for key in (
        "arch_total",
        "msys2_total",
        "mingw_total",
        "arch_matched",
        "arch_only",
        "all_three",
    ):
        print(f"  {key:14} {stats[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
    python tools/scrape_arch.py --aur-limit 10000   # bounded AUR sample
    python tools/scrape_arch.py --reuse             # match from cached JSONL

Status: ``core`` and ``extra`` are fully scraped. The **AUR sweep is NOT
complete** — a run reached ~14,200 of ~121,445 packages before the AUR stopped
accepting connections and throttled this host. TODO(aur-full-crawl): finish it
in bounded, resumable runs; see ``msys2_dataset.arch.fetch_aur_bulk``.
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
    """Read back a scrape, transparently handling ``.zst``.

    The committed artefact is zstd-compressed (about 12x smaller), so the
    loader accepts either form.
    """
    if path.suffix == ".zst":
        import zstandard

        raw = (
            zstandard.ZstdDecompressor()
            .decompress(path.read_bytes(), max_output_size=1 << 31)
            .decode("utf-8")
        )
    else:
        raw = path.read_text(encoding="utf-8")

    packages: list[ArchPackage] = []
    for line in raw.splitlines():
        if line.strip():
            packages.append(ArchPackage(**json.loads(line)))
    return packages


def _write_compressed(source: Path, level: int = 19) -> Path:
    """Compress ``source`` to ``source.zst`` and remove the plain file."""
    from msys2_dataset.io import compress_file_zstd

    target = compress_file_zstd(source, level=level)
    source.unlink(missing_ok=True)
    return target


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
        "--aur-limit",
        type=int,
        default=None,
        help=(
            "limit how many AUR names are queried. The full namespace is ~121k "
            "packages and takes well over 20 minutes; the AUR throttles "
            "sustained rates, so prefer bounded, resumable runs."
        ),
    )
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="skip scraping and reuse the cached data/arch-pkgs.jsonl[.zst]",
    )
    parser.add_argument("--out", type=Path, default=ARCH_JSONL)
    parser.add_argument(
        "--level",
        type=int,
        default=19,
        help="zstd level for the committed .jsonl.zst artefact",
    )
    args = parser.parse_args(argv)

    if args.reuse:
        cached = args.out if args.out.is_file() else args.out.with_suffix(args.out.suffix + ".zst")
        if not cached.is_file():
            print(f"no cached scrape at {args.out}; run without --reuse", file=sys.stderr)
            return 1
        print(f"reusing {cached}")
        arch = load_arch_jsonl(cached)
    else:
        print("scraping arch linux...")
        # --aur-limit wins over --aur-pages; both bound the AUR sweep because
        # the full namespace is large and the AUR throttles sustained rates.
        limit = args.aur_limit
        if limit is None and args.aur_pages is not None:
            limit = args.aur_pages * 100
        arch = scrape(
            repos=args.repos,
            mirror=args.mirror,
            include_aur=not args.no_aur,
            aur_limit=limit,
        )
        write_arch_jsonl(arch, args.out)
        packed = _write_compressed(args.out, level=args.level)
        print(f"wrote {len(arch)} packages to {packed} ({packed.stat().st_size:,} bytes)")

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
    packed_matches = _write_compressed(matches_path, level=args.level)
    print(
        f"wrote {len(matches)} rows to {packed_matches} ({packed_matches.stat().st_size:,} bytes)"
    )

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

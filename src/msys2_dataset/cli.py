"""Command line interface for the msys2-dataset builder.

Subcommands mirror the pipeline stages, so each one is independently runnable
and testable:

``pair``      pair the msys2 and mingw package listings
``pack``      write paired rows into a columnar zstd table archive
``submodules`` report drift between repo-list.txt and the git submodules
``info``      describe the on-disk storage constraints
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .packages import (
    pair_packages,
    parse_ls_listing_file,
    summarise,
)
from .repolist import build_submodule_plan, read_repo_list
from .store import FAT32_MAX_BYTES, write_table

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA = REPO_ROOT / "data"
MSYS2_LISTING = DATA / "msys2-pkgs.tsv"
MINGW_LISTING = DATA / "mingw-pkgs.tsv"


def _cmd_pair(args: argparse.Namespace) -> int:
    msys2 = parse_ls_listing_file(args.msys2)
    mingw = parse_ls_listing_file(args.mingw)
    pairs = pair_packages(msys2, mingw)
    stats = summarise(pairs)

    rows = [p.as_row() for p in pairs]
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote {len(rows)} pairs to {output}")
    else:
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))

    print(
        f"total={stats['total']} paired={stats['paired']} "
        f"msys2_only={stats['msys2_only']} mingw_only={stats['mingw_only']}",
        file=sys.stderr,
    )
    return 0


def _cmd_pack(args: argparse.Namespace) -> int:
    msys2 = parse_ls_listing_file(args.msys2)
    mingw = parse_ls_listing_file(args.mingw)
    pairs = pair_packages(msys2, mingw)

    table = {
        "name": [p.name for p in pairs],
        "msys2_name": [p.msys2.name if p.msys2 else None for p in pairs],
        "mingw_name": [p.mingw.name if p.mingw else None for p in pairs],
        "paired": [p.paired for p in pairs],
    }
    archive = write_table(args.output, args.table, table, max_part_bytes=args.max_part_bytes)
    print(f"wrote {archive} ({archive.stat().st_size} bytes)")
    return 0


def _cmd_submodules(args: argparse.Namespace) -> int:
    entries = read_repo_list(args.repo_list)
    plan = build_submodule_plan(entries)
    exit_code = 0
    for spec in plan:
        path = REPO_ROOT / spec.path
        present = (path / ".git").exists()
        status = "ok" if present else "MISSING"
        if not present:
            exit_code = 1
        print(f"{status:8} {spec.path}  <- {spec.url}")
    print(f"{len(plan)} submodule(s) declared in {args.repo_list}")
    return exit_code


def _cmd_info(args: argparse.Namespace) -> int:
    gib = FAT32_MAX_BYTES / 1024**3
    print(f"max file size : {FAT32_MAX_BYTES} bytes ({gib:.4f} GiB)")
    print("layout        : <table>/<column>.zst.partNN inside an uncompressed tar")
    print("partition cap : enforced against compressed size")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="msys2-dataset", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    pair = sub.add_parser("pair", help="pair msys2 and mingw package listings")
    pair.add_argument("--msys2", type=Path, default=MSYS2_LISTING)
    pair.add_argument("--mingw", type=Path, default=MINGW_LISTING)
    pair.add_argument("--output", type=Path, default=None, help="JSONL output path")
    pair.set_defaults(func=_cmd_pair)

    pack = sub.add_parser("pack", help="write pairs into a columnar table archive")
    pack.add_argument("--msys2", type=Path, default=MSYS2_LISTING)
    pack.add_argument("--mingw", type=Path, default=MINGW_LISTING)
    pack.add_argument("--output", type=Path, required=True, help="target .tar")
    pack.add_argument("--table", default="packages")
    pack.add_argument(
        "--max-part-bytes",
        type=int,
        default=1024**3,
        help="compressed size cap per .zst part",
    )
    pack.set_defaults(func=_cmd_pack)

    submodules = sub.add_parser("submodules", help="report drift against repo-list.txt")
    submodules.add_argument("--repo-list", type=Path, default=DATA / "repo" / "repo-list.txt")
    submodules.set_defaults(func=_cmd_submodules)

    info = sub.add_parser("info", help="describe storage constraints")
    info.set_defaults(func=_cmd_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

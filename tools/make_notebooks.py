#!/usr/bin/env python
"""Generate the committed notebooks.

The notebooks are build artifacts of this script so that they stay valid JSON
with deterministic outputs, and so a reviewer can see exactly what they run.
Re-run after changing the analysis code::

    python tools/make_notebooks.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = REPO_ROOT / "notebooks"


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.strip("\n").splitlines(keepends=True),
    }


def markdown(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.strip("\n").splitlines(keepends=True),
    }


def notebook(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3.11",
                "mimetype": "text/x-python",
                "file_extension": ".py",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


EXPLORATION = notebook(
    [
        markdown(
            """
# 01 — Exploration

Match **Arch Linux** packages against **MSYS2** and **MINGW**.

MSYS2 and MINGW are not designed to correspond to each other — they are two
separate porting efforts of the same upstream software — so pairing them
directly is meaningless. Arch Linux is the neutral reference point: it packages
much of the same upstream software under close-to-upstream names.

Three inputs:

| file | contents |
| --- | --- |
| `data/arch-pkgs.jsonl` | scraped Arch/AUR packages (run `tools/scrape_arch.py`) |
| `data/msys2-pkgs.tsv` | `ls -la` snapshot of the MSYS2 repository |
| `data/mingw-pkgs.tsv` | `ls -la` snapshot of the MINGW repository |

If you have not run the scraper yet, this notebook fetches `core` live.
"""
        ),
        code(
            """
import sys
from pathlib import Path

# Keep the src-layout importable when running from the repository root.
sys.path.insert(0, str(Path.cwd() / "src"))

from msys2_dataset.matching import match_ecosystems, summarise_matches
from msys2_dataset.packages import parse_ls_listing_file
"""
        ),
        markdown("### Load the three ecosystems"),
        code(
            """
DATA = Path("data")
msys2 = parse_ls_listing_file(DATA / "msys2-pkgs.tsv")
mingw = parse_ls_listing_file(DATA / "mingw-pkgs.tsv")
len(msys2), len(mingw)
"""
        ),
        markdown(
            "The listings are `ls -la` output, so only directories are packages; "
            "`total`, `LICENSE` and `README.md` lines are metadata."
        ),
        code(
            """
assert not {"total", "LICENSE", "README.md"} & {e.name for e in msys2}
assert not {"total", "LICENSE", "README.md"} & {e.name for e in mingw}
[e.name for e in msys2[:5]], [e.name for e in mingw[:3]]
"""
        ),
        markdown(
            """
### Load the scraped Arch packages

Prefer the cached scrape; fall back to fetching `core` live so the notebook
works on a fresh checkout.
"""
        ),
        code(
            """
import json

arch_path = DATA / "arch-pkgs.jsonl"
if arch_path.is_file():
    from msys2_dataset.arch import ArchPackage

    arch = [
        ArchPackage(**json.loads(line))
        for line in arch_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    source = "cached scrape"
else:
    from msys2_dataset.arch import fetch_official_repo

    arch = fetch_official_repo("core")
    source = "live core only"

from collections import Counter

print(f"{len(arch)} arch packages ({source})")
Counter(p.repo for p in arch).most_common()
"""
        ),
        markdown(
            """
### Match by normalised upstream name

`mingw-w64-ucrt-x86_64-zlib` normalises to `zlib`, which matches Arch's `zlib`.
A small alias table covers genuinely divergent names (Arch `freetype2` ↔ MSYS2
`freetype`).
"""
        ),
        code(
            """
matches = match_ecosystems(arch, msys2, mingw)
stats = summarise_matches(matches)
stats
"""
        ),
        markdown(
            "### Projects present in all three ecosystems\n\nThese are the genuinely portable upstream projects."
        ),
        code(
            """
all_three = [m for m in matches if m.in_arch and m.in_msys2 and m.in_mingw]
for m in all_three[:15]:
    print(f"{m.name:20} arch={m.arch[0].version:16} msys2={m.msys2[0].name:20} mingw={m.mingw[0].name}")
len(all_three)
"""
        ),
        markdown(
            """
### Where the ecosystems diverge

Arch packages a lot that MSYS2/MINGW do not: MSYS2 and MINGW are
POSIX-on-Windows toolchains, so they cover the portable core but not Arch's
Linux-only surface.
"""
        ),
        code(
            """
arch_only = [m.name for m in matches if m.in_arch and not m.in_msys2 and not m.in_mingw]
print(f"arch only ({len(arch_only)}):", arch_only[:12])

# Projects MSYS2 ships but Arch does not, under the same normalised name.
msys2_only = [m.name for m in matches if m.in_msys2 and not m.in_arch]
print(f"msys2 only ({len(msys2_only)}):", msys2_only[:12])
"""
        ),
        markdown("### Coverage of Arch by the Windows toolchains"),
        code(
            """
{
    "arch coverage by msys2+mingw": round(stats["arch_matched"] / stats["arch_total"], 3),
    "arch coverage by msys2": round(stats["arch_msys2"] / stats["arch_total"], 3),
    "arch coverage by mingw": round(stats["arch_mingw"] / stats["arch_total"], 3),
}
"""
        ),
    ]
)

ANALYSIS = notebook(
    [
        markdown(
            """
# 02 — Analysis

Persist the cross-ecosystem match table using the project's columnar container
(`<table>/<column>.zst.partNN` inside an **uncompressed** tar), verify the
round-trip, and confirm the FAT32 constraint holds.
"""
        ),
        code(
            """
import json
import shutil
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))

from msys2_dataset.arch import ArchPackage
from msys2_dataset.matching import match_ecosystems
from msys2_dataset.packages import parse_ls_listing_file
from msys2_dataset.store import FAT32_MAX_BYTES, read_table, write_table

FAT32_MAX_BYTES, FAT32_MAX_BYTES / 1024**3
"""
        ),
        markdown("### Build the table from the three ecosystems"),
        code(
            """
arch_path = Path("data/arch-pkgs.jsonl")
if arch_path.is_file():
    arch = [
        ArchPackage(**json.loads(line))
        for line in arch_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
else:
    from msys2_dataset.arch import fetch_official_repo

    arch = fetch_official_repo("core")

matches = match_ecosystems(
    arch,
    parse_ls_listing_file("data/msys2-pkgs.tsv"),
    parse_ls_listing_file("data/mingw-pkgs.tsv"),
)

table = {
    "name": [m.name for m in matches],
    "in_arch": [m.in_arch for m in matches],
    "in_msys2": [m.in_msys2 for m in matches],
    "in_mingw": [m.in_mingw for m in matches],
    "ecosystems": [",".join(m.ecosystems) for m in matches],
    "arch_names": [",".join(p.name for p in m.arch) for m in matches],
    "arch_repos": [",".join(sorted({p.repo for p in m.arch})) for m in matches],
    "msys2_names": [",".join(p.name for p in m.msys2) for m in matches],
    "mingw_names": [",".join(p.name for p in m.mingw) for m in matches],
}
{column: len(values) for column, values in table.items()}
"""
        ),
        markdown("### Write it as a columnar zstd archive"),
        code(
            """
# Build into a scratch directory so running the notebook does not dirty the
# tracked tree; the real pipeline writes to data/processed/.
out = Path(".notebook-tmp/processed")
out.mkdir(parents=True, exist_ok=True)
archive = out / "matches.tar"
write_table(archive, "matches", table, max_part_bytes=1024 * 1024)
archive.stat().st_size
"""
        ),
        markdown("### Inspect the produced layout"),
        code(
            """
import tarfile

with tarfile.open(archive, mode="r:") as tar:  # "r:" rejects compression
    names = tar.getnames()
print(f"{len(names)} members")
names[:8]
"""
        ),
        markdown("### Verify the round-trip and the size cap"),
        code(
            """
import json

# Extract into a separate directory; deleting the parent would remove the
# archive we are reading from.
extract_dir = Path(".notebook-tmp/extracted")
shutil.rmtree(extract_dir, ignore_errors=True)
restored = read_table(archive, extract_dir)
assert restored == table, "round-trip mismatch"
print("round-trip OK")

with tarfile.open(archive, mode="r:") as tar:
    oversize = [m.name for m in tar.getmembers() if m.size > FAT32_MAX_BYTES]
assert not oversize, oversize
print("every member is within the FAT32 limit")
"""
        ),
        markdown(
            """
### SQLite alternative

For shards that are rewritten repeatedly, the SQLite path is faster than
re-serialising a whole column. SQLite columns are scalar, so the list-valued
fields are stored as JSON strings.
"""
        ),
        code(
            """
from msys2_dataset.sqlite_store import load_rows, write_rows

db = out / "matches.sqlite"
matched = [m for m in matches if m.in_arch and (m.in_msys2 or m.in_mingw)]
records = [
    {
        "name": m.name,
        "in_msys2": int(m.in_msys2),
        "in_mingw": int(m.in_mingw),
        "ecosystems": ",".join(m.ecosystems),
        "arch_names": json.dumps([p.name for p in m.arch]),
        "msys2_names": json.dumps([p.name for p in m.msys2]),
        "mingw_names": json.dumps([p.name for p in m.mingw]),
    }
    for m in matched
]
write_rows(db, "matches", records, if_exists="replace")
rows = load_rows(db, "matches")
print(f"{len(rows)} matched rows in {db}")
rows[0]
"""
        ),
    ]
)


def main() -> int:
    NOTEBOOKS.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("01_exploration.ipynb", EXPLORATION),
        ("02_analysis.ipynb", ANALYSIS),
    ):
        path = NOTEBOOKS / name
        # newline="\n" keeps the committed JSON stable across platforms.
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

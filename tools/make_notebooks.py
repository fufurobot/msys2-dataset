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

Pair the MSYS2 and MINGW package repositories and look at what the two
ecosystems share.

The inputs are `ls -la` snapshots of the two upstream repositories
(`data/msys2-pkgs.tsv`, `data/mingw-pkgs.tsv`). Only directory entries are
packages; `total`, `LICENSE` and `README.md` lines are metadata and are
ignored by the parser.
"""
        ),
        code(
            """
import sys
from pathlib import Path

# Keep the src-layout importable when running from the repository root.
sys.path.insert(0, str(Path.cwd() / "src"))

from msys2_dataset.packages import (
    pair_packages,
    parse_ls_listing_file,
    summarise,
)
"""
        ),
        code(
            """
DATA = Path("data")
msys2 = parse_ls_listing_file(DATA / "msys2-pkgs.tsv")
mingw = parse_ls_listing_file(DATA / "mingw-pkgs.tsv")
len(msys2), len(mingw)
"""
        ),
        markdown(
            "### Sanity check the parser\n\nDirectories only, no `total` line, no plain files."
        ),
        code(
            """
assert not {"total", "LICENSE", "README.md"} & {e.name for e in msys2}
assert not {"total", "LICENSE", "README.md"} & {e.name for e in mingw}
[e.name for e in msys2[:5]]
"""
        ),
        markdown(
            "### Pair by normalised name\n\n`mingw-w64-zlib` and `mingw-w64-ucrt-x86_64-zlib` both normalise to `zlib`."
        ),
        code(
            """
pairs = pair_packages(msys2, mingw)
stats = summarise(pairs)
stats
"""
        ),
        code(
            """
paired = [p for p in pairs if p.paired]
[f"{p.msys2.name} <-> {p.mingw.name}" for p in paired[:15]]
"""
        ),
        markdown("### Only on one side"),
        code(
            """
msys2_only = [p.name for p in pairs if p.msys2 and not p.mingw]
mingw_only = [p.name for p in pairs if p.mingw and not p.msys2]
print(f"msys2 only ({len(msys2_only)}):", msys2_only[:10])
print(f"mingw only ({len(mingw_only)}):", mingw_only[:10])
"""
        ),
        markdown("### Overlap ratio\n\nHow much of the MSYS2 package set has a MINGW counterpart?"),
        code(
            """
summary = {
    "coverage of msys2": round(stats["paired"] / (stats["paired"] + stats["msys2_only"]), 3),
    "coverage of mingw": round(stats["paired"] / (stats["paired"] + stats["mingw_only"]), 3),
}
summary
"""
        ),
    ]
)

ANALYSIS = notebook(
    [
        markdown(
            """
# 02 — Analysis

Persist the paired table using the project's columnar container
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

from msys2_dataset.packages import pair_packages, parse_ls_listing_file
from msys2_dataset.store import FAT32_MAX_BYTES, read_table, write_table

FAT32_MAX_BYTES, FAT32_MAX_BYTES / 1024**3
"""
        ),
        markdown("### Build the table"),
        code(
            """
pairs = pair_packages(
    parse_ls_listing_file("data/msys2-pkgs.tsv"),
    parse_ls_listing_file("data/mingw-pkgs.tsv"),
)

table = {
    "name": [p.name for p in pairs],
    "msys2_name": [p.msys2.name if p.msys2 else None for p in pairs],
    "mingw_name": [p.mingw.name if p.mingw else None for p in pairs],
    "paired": [p.paired for p in pairs],
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
archive = out / "packages.tar"
write_table(archive, "packages", table, max_part_bytes=1024 * 1024)
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
re-serialising a whole column. SQLite columns are scalar, so `aliases` (a
list) is stored as a JSON string.
"""
        ),
        code(
            """
from msys2_dataset.sqlite_store import load_rows, write_rows

db = out / "packages.sqlite"
records = [
    {
        "name": p.name,
        "msys2_name": p.msys2.name if p.msys2 else None,
        "mingw_name": p.mingw.name if p.mingw else None,
        "paired": int(p.paired),
        "aliases": json.dumps(list(p.aliases)),
    }
    for p in pairs
    if p.paired
]
write_rows(db, "paired", records, if_exists="replace")
rows = load_rows(db, "paired")
print(f"{len(rows)} paired rows in {db}")
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

"""msys2-dataset — pair MSYS2 and MINGW package metadata.

Layout of the package:

``packages``      parse ``ls -la`` listings and pair msys2 <-> mingw packages
``repolist``      parse ``repo-list.txt`` and plan ``data/repo`` submodules
``store``         columnar, zstd-partitioned, FAT32-safe table archives
``io``            CSV / JSONL ingest plus zstd and tar.zst helpers
``sqlite_store``  SQLite fast-write fallback for oversized shards
``cli``           command line entry point (``msys2-dataset``)
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]

"""CSV / JSONL ingest plus zstd and tar.zst helpers.

The dataset is CSV/JSONL oriented. These helpers are the single place where
that convention lives, so ingest scripts and tests agree on encoding
(UTF-8), delimiters, and compression.
"""

from __future__ import annotations

import csv
import io
import json
import tarfile
from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import zstandard

__all__ = [
    "compress_file_zstd",
    "decompress_file_zstd",
    "iter_jsonl",
    "iter_csv",
    "pack_tar_zstd",
    "read_csv",
    "read_jsonl",
    "unpack_tar_zstd",
    "write_csv",
    "write_jsonl",
]

_CHUNK = 1 << 20


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------
def write_csv(
    path: str | Path,
    rows: Iterable[Mapping[str, Any]],
    fieldnames: Sequence[str] | None = None,
    delimiter: str = ",",
) -> Path:
    """Write ``rows`` as CSV with a header.

    ``fieldnames`` defaults to the keys of the first row. Writing an empty row
    set without explicit ``fieldnames`` is a programming error and raises.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    materialised = list(rows)
    if fieldnames is None:
        if not materialised:
            raise ValueError("fieldnames are required when there are no rows")
        fieldnames = list(materialised[0].keys())

    with target.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=list(fieldnames), delimiter=delimiter, extrasaction="ignore"
        )
        writer.writeheader()
        for row in materialised:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    return target


def iter_csv(path: str | Path, delimiter: str = ",") -> Iterator[dict[str, str]]:
    """Stream rows from a CSV file as dicts."""
    with Path(path).open("r", encoding="utf-8", newline="") as fh:
        yield from csv.DictReader(fh, delimiter=delimiter)


def read_csv(path: str | Path, delimiter: str = ",") -> list[dict[str, str]]:
    """Read a whole CSV file into a list of dicts."""
    return list(iter_csv(path, delimiter=delimiter))


# --------------------------------------------------------------------------
# JSONL
# --------------------------------------------------------------------------
def write_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    """Write ``rows`` as newline-delimited JSON (one object per line)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False))
            fh.write("\n")
    return target


def iter_jsonl(path: str | Path) -> Iterator[Any]:
    """Stream objects from a JSONL file, skipping blank lines."""
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def read_jsonl(path: str | Path) -> list[Any]:
    """Read a whole JSONL file into a list."""
    return list(iter_jsonl(path))


# --------------------------------------------------------------------------
# zstd single-file helpers
# --------------------------------------------------------------------------
def compress_file_zstd(
    src: str | Path,
    dst: str | Path | None = None,
    level: int = 3,
) -> Path:
    """Compress ``src`` to ``dst`` (default: ``src`` with ``.zst`` appended)."""
    source = Path(src)
    target = Path(dst) if dst is not None else source.with_suffix(source.suffix + ".zst")
    target.parent.mkdir(parents=True, exist_ok=True)
    compressor = zstandard.ZstdCompressor(level=level)
    with source.open("rb") as fin, target.open("wb") as fout:
        compressor.copy_stream(fin, fout)
    return target


def decompress_file_zstd(src: str | Path, dst: str | Path | None = None) -> Path:
    """Decompress a ``.zst`` file; default target drops the suffix."""
    source = Path(src)
    if dst is not None:
        target = Path(dst)
    elif source.suffix == ".zst":
        target = source.with_suffix("")
    else:
        target = source.with_suffix(source.suffix + ".out")
    target.parent.mkdir(parents=True, exist_ok=True)
    decompressor = zstandard.ZstdDecompressor()
    with source.open("rb") as fin, target.open("wb") as fout:
        decompressor.copy_stream(fin, fout)
    return target


# --------------------------------------------------------------------------
# tar.zst directory bundles
# --------------------------------------------------------------------------
def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract ``tar`` into ``dest`` with traversal protection.

    Intermediate directories are created up front by this function rather than
    implicitly by :meth:`tarfile.TarFile.extractall`. Under restrictive ACLs
    (notably the DSH sandbox) a directory created implicitly during extraction
    does not inherit a writable ACL, and writing the member file inside it then
    fails with ``PermissionError``. Creating them explicitly avoids that, and
    also lets us reject path traversal before touching the filesystem.
    """
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    members = tar.getmembers()

    for member in members:
        target = (resolved_dest / member.name).resolve()
        if not str(target).startswith(str(resolved_dest)):
            raise ValueError(f"unsafe path in archive: {member.name!r}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)

    for member in members:
        if member.isdir():
            continue
        tar.extract(member, str(resolved_dest), set_attrs=False)


def pack_tar_zstd(root: str | Path, archive: str | Path, level: int = 3) -> Path:
    """Bundle the directory ``root`` into a zstd-compressed tar at ``archive``."""
    source = Path(root)
    target = Path(archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    compressor = zstandard.ZstdCompressor(level=level)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        # Directories must precede their contents, otherwise extraction into an
        # empty tree fails because the parent directory does not exist yet.
        entries = sorted(
            source.rglob("*"),
            key=lambda p: (len(p.relative_to(source).parts), str(p)),
        )
        for path in entries:
            tar.add(
                str(path),
                arcname=str(path.relative_to(source)).replace("\\", "/"),
                recursive=False,
            )
    with target.open("wb") as fout:
        compressor.copy_stream(io.BytesIO(raw.getvalue()), fout)
    return target


def unpack_tar_zstd(archive: str | Path, dest: str | Path) -> Path:
    """Extract a zstd-compressed tar produced by :func:`pack_tar_zstd`."""
    source = Path(archive)
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    decompressor = zstandard.ZstdDecompressor()
    raw = io.BytesIO()
    with source.open("rb") as fin:
        decompressor.copy_stream(fin, raw)
    raw.seek(0)
    with tarfile.open(fileobj=raw, mode="r:") as tar:
        _safe_extract(tar, target)
    return target

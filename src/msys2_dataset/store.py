"""Columnar, zstd-compressed, size-capped table storage.

On-disk contract (from the project specification)::

    <archive.tar>            # an UNCOMPRESSED tar container
      <table_name>/
        <column_name>.zst.part00
        <column_name>.zst.part01
        ...

Each ``.zst.partNN`` member is an independent zstd frame. A column whose
compressed payload would exceed the part cap is split into multiple frames so
that **no produced file exceeds 4 GiB**, keeping the dataset transportable on
FAT32 (which cannot represent a file of exactly 4 GiB).

Columns are encoded as newline-delimited JSON (JSON Lines). That keeps the
format self-describing, streaming-friendly, and lossless for ``None`` and for
non-string scalars, without pulling in a heavyweight columnar dependency.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

import zstandard

__all__ = [
    "FAT32_MAX_BYTES",
    "PartWriter",
    "iter_part_names",
    "part_name",
    "parse_part_name",
    "read_parts",
    "read_table",
    "write_table",
]

#: Hard cap for any single produced file. FAT32 cannot address a file of
#: exactly 4 GiB (its size field is 32-bit, so the largest representable size
#: is 4 GiB - 1), hence the usable maximum is one byte below that boundary.
FAT32_MAX_BYTES = 4 * 1024**3 - 1

#: Default part cap: 1 GiB compressed, a comfortable margin under the limit.
DEFAULT_MAX_PART_BYTES = 1 * 1024**3

_PART_RE = re.compile(r"^(?P<column>.+)\.zst\.part(?P<index>\d{2,})$")

#: Safe table / column identifier: no path separators, no traversal.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


def part_name(column: str, index: int) -> str:
    """Return the file name of part ``index`` for ``column``."""
    if index < 0:
        raise ValueError(f"part index must be non-negative, got {index}")
    return f"{column}.zst.part{index:02d}"


def parse_part_name(name: str) -> tuple[str, int]:
    """Inverse of :func:`part_name`. Raises ``ValueError`` if not a part."""
    match = _PART_RE.match(name)
    if match is None:
        raise ValueError(f"not a zstd part name: {name!r}")
    return match.group("column"), int(match.group("index"))


def iter_part_names(names: Sequence[str], column: str | None = None) -> list[str]:
    """Filter ``names`` to part files, sorted by part index.

    When ``column`` is given, only that column's parts are returned.
    """
    found: list[tuple[int, str]] = []
    for name in names:
        try:
            col, index = parse_part_name(name)
        except ValueError:
            continue
        if column is None or col == column:
            found.append((index, name))
    found.sort()
    return [name for _, name in found]


def _check_identifier(value: str, what: str) -> str:
    if not value or not _SAFE_NAME_RE.match(value) or value in {".", ".."}:
        raise ValueError(f"invalid {what}: {value!r}")
    if "\\" in value or "/" in value:
        raise ValueError(f"invalid {what} (path separator): {value!r}")
    return value


class PartWriter:
    """Write one column as a sequence of size-capped zstd parts.

    Used as a context manager. Bytes are accumulated in memory and flushed as a
    completed zstd frame whenever the *compressed* output for the current part
    would reach ``max_bytes``. Flushing per frame (rather than splitting a
    single frame's bytes across files) keeps every part independently
    decompressable, which is what allows a reader to stream a single column
    part without touching the rest of the column.
    """

    def __init__(
        self,
        directory: str | Path,
        column: str,
        max_bytes: int = DEFAULT_MAX_PART_BYTES,
        level: int = 3,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be positive, got {max_bytes}")
        if max_bytes > FAT32_MAX_BYTES:
            raise ValueError(
                f"max_bytes {max_bytes} exceeds the FAT32 cap {FAT32_MAX_BYTES}"
            )
        self.directory = Path(directory)
        self.column = _check_identifier(column, "column name")
        self.max_bytes = max_bytes
        self.level = level

        self._buffer = bytearray()
        self._index = 0
        self._parts: list[Path] = []
        self._closed = False

    # -- internals ---------------------------------------------------------
    def _compress(self, payload: bytes) -> bytes:
        """Compress ``payload`` as a single standalone zstd frame."""
        return zstandard.ZstdCompressor(level=self.level).compress(payload)

    def _flush(self) -> None:
        if not self._buffer:
            return
        payload = bytes(self._buffer)
        self._buffer.clear()
        frame = self._compress(payload)
        if len(frame) > FAT32_MAX_BYTES:
            raise ValueError(
                f"compressed part would be {len(frame)} bytes, "
                f"exceeding the FAT32 cap {FAT32_MAX_BYTES}"
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / part_name(self.column, self._index)
        target.write_bytes(frame)
        self._parts.append(target)
        self._index += 1

    # -- public API --------------------------------------------------------
    def write(self, data: bytes | bytearray | memoryview) -> None:
        """Append raw bytes, flushing a new part when the cap is reached.

        The cap is measured against the *compressed* size of the current part,
        because that is what lands on disk.
        """
        if self._closed:
            raise ValueError("cannot write to a closed PartWriter")
        chunk = bytes(data)
        if not chunk:
            return

        candidate = bytes(self._buffer) + chunk
        if len(self._compress(candidate)) >= self.max_bytes and self._buffer:
            # Current part is full: flush it, then start a new one.
            self._flush()
            candidate = chunk

        if len(self._compress(candidate)) >= self.max_bytes and not self._buffer:
            # A single chunk already exceeds the cap; emit it as its own part
            # rather than looping forever.
            self._buffer.extend(candidate)
            self._flush()
            return

        self._buffer = bytearray(candidate)

    @property
    def parts(self) -> list[Path]:
        return list(self._parts)

    def close(self) -> None:
        if not self._closed:
            self._flush()
            self._closed = True

    def __enter__(self) -> "PartWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_parts(directory: str | Path, column: str) -> bytes:
    """Read and concatenate every part of ``column`` from ``directory``."""
    _check_identifier(column, "column name")
    root = Path(directory)
    names = [p.name for p in root.iterdir() if p.is_file()] if root.is_dir() else []
    parts = iter_part_names(names, column)
    if not parts:
        raise FileNotFoundError(f"no parts found for column {column!r} in {root}")

    out = bytearray()
    dctx = zstandard.ZstdDecompressor()
    for name in parts:
        raw = (root / name).read_bytes()
        out.extend(dctx.decompress(raw, max_output_size=1 << 31))
    return bytes(out)


def _encode_rows(values: Sequence[Any]) -> bytes:
    buf = io.BytesIO()
    for value in values:
        buf.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        buf.write(b"\n")
    return buf.getvalue()


def _decode_rows(raw: bytes) -> list[Any]:
    if not raw:
        return []
    return [
        json.loads(line)
        for line in raw.decode("utf-8").split("\n")
        if line.strip()
    ]


def write_table(
    archive: str | Path,
    table: str,
    columns: Mapping[str, Sequence[Any]],
    max_part_bytes: int = DEFAULT_MAX_PART_BYTES,
) -> Path:
    """Write ``columns`` as a table into an uncompressed tar at ``archive``.

    Produces members named ``<table>/<column>.zst.partNN``. Every member and
    the archive itself are guaranteed to stay at or below
    :data:`FAT32_MAX_BYTES`.
    """
    _check_identifier(table, "table name")
    archive_path = Path(archive)

    for column in columns:
        _check_identifier(column, "column name")

    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for column, values in columns.items():
            payload = _encode_rows(list(values))
            with PartWriter(
                archive_path.parent / f".parts-{table}-{column}",
                column,
                max_bytes=max_part_bytes,
            ) as writer:
                # Feed in bounded chunks so a huge column is not held twice.
                chunk_size = 1 << 20
                for offset in range(0, len(payload), chunk_size):
                    writer.write(payload[offset : offset + chunk_size])
            staging = archive_path.parent / f".parts-{table}-{column}"
            for part in writer.parts:
                info = tar.gettarinfo(str(part), arcname=f"{table}/{part.name}")
                info.size = part.stat().st_size
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with part.open("rb") as fh:
                    tar.addfile(info, fh)
                part.unlink()
            try:
                staging.rmdir()
            except OSError:
                pass

    if archive_path.stat().st_size > FAT32_MAX_BYTES:
        raise ValueError(
            f"archive {archive_path} exceeds the FAT32 cap {FAT32_MAX_BYTES}; "
            "split the table into multiple archives"
        )
    return archive_path


def read_table(archive: str | Path, dest: str | Path, table: str | None = None) -> dict[str, list[Any]]:
    """Read a table written by :func:`write_table` back into memory.

    ``dest`` receives the extracted ``<table>/`` tree. When ``table`` is None
    the sole top-level directory in the archive is used.
    """
    archive_path = Path(archive)
    dest_path = Path(dest)
    dest_path.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive_path, mode="r:") as tar:
        members = tar.getmembers()
        if table is None:
            roots = {m.name.split("/", 1)[0] for m in members if "/" in m.name}
            if not roots:
                return {}
            if len(roots) > 1:
                raise ValueError(
                    f"archive contains multiple tables {sorted(roots)}; specify one"
                )
            table = roots.pop()
        _check_identifier(table, "table name")
        # Create directories explicitly before extraction so they inherit a
        # writable ACL (see msys2_dataset.io._safe_extract for the rationale).
        for member in members:
            if member.isdir():
                (dest_path / member.name).mkdir(parents=True, exist_ok=True)
        tar.extractall(dest_path)

    table_dir = dest_path / table
    if not table_dir.is_dir():
        raise FileNotFoundError(f"table {table!r} not found in {archive_path}")

    columns: dict[str, list[Any]] = {}
    names = [p.name for p in table_dir.iterdir() if p.is_file()]
    seen: list[str] = []
    for name in names:
        try:
            column, _ = parse_part_name(name)
        except ValueError:
            continue
        if column not in seen:
            seen.append(column)
    for column in seen:
        columns[column] = _decode_rows(read_parts(table_dir, column))
    return columns


def iter_table_columns(archive: str | Path) -> Iterator[tuple[str, str]]:
    """Yield ``(table, column)`` pairs present in ``archive`` without extracting."""
    with tarfile.open(archive, mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile() or "/" not in member.name:
                continue
            table, filename = member.name.split("/", 1)
            try:
                column, _ = parse_part_name(filename)
            except ValueError:
                continue
            yield table, column

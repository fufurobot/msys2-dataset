"""Tests for the columnar zstd-partitioned table format.

Required layout (see project spec):

    <table_name>/<column_name>.zst.partNN

Constraints pinned here:
  * a plain (uncompressed) tar container,
  * one directory per table, one or more `.zst.partNN` members per column,
  * no produced file may exceed 4 GiB (FAT32 compatibility).
"""

from __future__ import annotations

import io
import json
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import zstandard

from msys2_dataset.store import (
    FAT32_MAX_BYTES,
    PartWriter,
    iter_part_names,
    part_name,
    parse_part_name,
    read_parts,
    read_table,
    write_table,
)

MAX_4GB = 4 * 1024**3


class TestFat32Limit(unittest.TestCase):
    def test_limit_is_4gib(self) -> None:
        self.assertEqual(FAT32_MAX_BYTES, MAX_4GB)

    def test_limit_strictly_below_4gb_boundary(self) -> None:
        # FAT32 cannot address a file of exactly 4 GiB.
        self.assertLess(FAT32_MAX_BYTES, 4 * 1024**3)


class TestPartName(unittest.TestCase):
    def test_zero_padded_two_digits(self) -> None:
        self.assertEqual(part_name("name", 0), "name.zst.part00")
        self.assertEqual(part_name("name", 7), "name.zst.part07")
        self.assertEqual(part_name("name", 42), "name.zst.part42")

    def test_roundtrip(self) -> None:
        for index in (0, 1, 9, 10, 99):
            self.assertEqual(parse_part_name(part_name("col", index)), ("col", index))

    def test_parse_rejects_unrelated(self) -> None:
        for bad in ("col.zst", "col.part00", "col.zst.part", "col.zst.part0"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_part_name(bad)

    def test_iter_part_names_is_sorted_by_index(self) -> None:
        names = [
            part_name("c", 10),
            part_name("c", 2),
            part_name("c", 0),
            "other.txt",
        ]
        self.assertEqual(
            iter_part_names(names), [part_name("c", 0), part_name("c", 2), part_name("c", 10)]
        )


class TestPartWriter(unittest.TestCase):
    def test_rolls_over_at_byte_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            # 10-byte limit, write 25 bytes => 3 parts
            with PartWriter(out, "blob", max_bytes=10) as writer:
                for _ in range(25):
                    writer.write(b"x")
            parts = sorted(p.name for p in out.iterdir())
            self.assertEqual(parts, ["blob.zst.part00", "blob.zst.part01", "blob.zst.part02"])
            for path in out.iterdir():
                self.assertLessEqual(path.stat().st_size, FAT32_MAX_BYTES)

    def test_parts_are_zstd_frames_and_concatenate_to_input(self) -> None:
        payload = b"hello world " * 1000
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            with PartWriter(out, "col", max_bytes=1024) as writer:
                writer.write(payload)
            raw = read_parts(out, "col")
            self.assertEqual(raw, payload)
            # A single-part file must be a valid zstd frame.
            first = out / part_name("col", 0)
            self.assertEqual(
                zstandard.ZstdDecompressor().decompress(
                    first.read_bytes(), max_output_size=len(payload) + 1
                ),
                payload,
            )

    def test_empty_write_produces_no_parts(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            with PartWriter(out, "col", max_bytes=10):
                pass
            self.assertEqual(list(out.iterdir()), [])

    def test_part_index_is_sequential(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            with PartWriter(out, "col", max_bytes=4) as writer:
                writer.write(b"a" * 20)
            names = sorted(p.name for p in out.iterdir())
            self.assertEqual(
                names,
                [part_name("col", i) for i in range(len(names))],
            )

    def test_rejects_absurdly_small_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                PartWriter(Path(tmp), "col", max_bytes=0)


class TestReadWriteTableRoundtrip(unittest.TestCase):
    def _roundtrip(self, table: dict[str, list]) -> dict[str, list]:
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "table.tar"
            write_table(archive, "pkg", table)
            self.assertTrue(archive.exists())
            with TemporaryDirectory() as tmp2:
                return read_table(archive, tmp2)

    def test_simple_columns(self) -> None:
        table = {"name": ["a", "b", "c"], "version": [1, 2, 3]}
        self.assertEqual(self._roundtrip(table), table)

    def test_unicode_values(self) -> None:
        table = {"name": ["café", "日本語", "emoji-🎉"]}
        self.assertEqual(self._roundtrip(table), table)

    def test_empty_table(self) -> None:
        self.assertEqual(self._roundtrip({}), {})

    def test_single_column_many_rows_forces_parts(self) -> None:
        table = {"blob": [f"row-{i:06d}-{'y' * 50}" for i in range(5000)]}
        got = self._roundtrip(table)
        self.assertEqual(got, table)

    def test_none_values_preserved(self) -> None:
        table = {"opt": ["x", None, "z"]}
        self.assertEqual(self._roundtrip(table), table)


class TestTarLayout(unittest.TestCase):
    def test_container_is_uncompressed_tar(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "t.tar"
            write_table(archive, "pkg", {"name": ["a"]}, max_part_bytes=1024)
            with tarfile.open(archive, "r:") as tf:  # "r:" == uncompressed only
                members = tf.getnames()
            self.assertIn("pkg/name.zst.part00", [m.replace("\\", "/") for m in members])

    def test_member_names_use_posix_separators(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "t.tar"
            write_table(archive, "pkg", {"name": ["a"]}, max_part_bytes=1024)
            with tarfile.open(archive, "r:") as tf:
                for member in tf.getnames():
                    self.assertNotIn("\\", member)
                    self.assertRegex(member, r"^pkg/[A-Za-z0-9_.\-]+\.zst\.part\d{2}$")

    def test_all_members_within_fat32_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "t.tar"
            write_table(
                archive,
                "pkg",
                {"blob": ["z" * 1000 for _ in range(200)]},
                max_part_bytes=4096,
            )
            with tarfile.open(archive, "r:") as tf:
                for member in tf.getmembers():
                    self.assertLessEqual(member.size, FAT32_MAX_BYTES)

    def test_archive_itself_within_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "t.tar"
            write_table(archive, "pkg", {"name": ["a", "b"]}, max_part_bytes=1024)
            self.assertLessEqual(archive.stat().st_size, FAT32_MAX_BYTES)

    def test_table_name_with_traversal_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            archive = Path(tmp) / "t.tar"
            for bad in ("../evil", "a/b", "..", ""):
                with self.subTest(bad=bad):
                    with self.assertRaises(ValueError):
                        write_table(archive, bad, {"c": [1]}, max_part_bytes=1024)


class TestReadPartsErrors(unittest.TestCase):
    def test_corrupt_part_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / part_name("col", 0)).write_bytes(b"this is not zstd")
            with self.assertRaises(zstandard.ZstdError):
                read_parts(out, "col")

    def test_missing_column_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                read_parts(Path(tmp), "nope")


if __name__ == "__main__":
    unittest.main()

"""Tests for CSV / JSONL ingest, zstd compression, and archive helpers."""

from __future__ import annotations

import json
import tarfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import zstandard

from msys2_dataset.io import (
    compress_file_zstd,
    decompress_file_zstd,
    pack_tar_zstd,
    unpack_tar_zstd,
    read_csv,
    read_jsonl,
    write_csv,
    write_jsonl,
)


class TestCsvRoundtrip(unittest.TestCase):
    def test_basic(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            rows = [{"name": "a", "ver": "1"}, {"name": "b", "ver": "2"}]
            write_csv(path, rows, fieldnames=["name", "ver"])
            self.assertEqual(read_csv(path), rows)

    def test_header_is_written(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            write_csv(path, [{"a": "1"}], fieldnames=["a"])
            self.assertTrue(path.read_text(encoding="utf-8").startswith("a"))

    def test_tsv_delimiter(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.tsv"
            rows = [{"a": "1", "b": "2"}]
            write_csv(path, rows, fieldnames=["a", "b"], delimiter="\t")
            self.assertEqual(read_csv(path, delimiter="\t"), rows)

    def test_missing_fieldnames_derived_from_first_row(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            write_csv(path, [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}])
            self.assertEqual(
                read_csv(path), [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]
            )

    def test_empty_rows_writes_header_only(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            write_csv(path, [], fieldnames=["a", "b"])
            self.assertEqual(read_csv(path), [])

    def test_rows_without_fieldnames_and_empty_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_csv(Path(tmp) / "t.csv", [])

    def test_unicode_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            rows = [{"name": "café-日本語"}]
            write_csv(path, rows, fieldnames=["name"])
            self.assertEqual(read_csv(path), rows)


class TestJsonlRoundtrip(unittest.TestCase):
    def test_basic(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            rows = [{"a": 1}, {"a": 2, "b": [1, 2, 3]}]
            write_jsonl(path, rows)
            self.assertEqual(read_jsonl(path), rows)

    def test_one_object_per_line(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            write_jsonl(path, [{"a": 1}, {"a": 2}])
            lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
            self.assertEqual(len(lines), 2)
            for line in lines:
                json.loads(line)

    def test_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            write_jsonl(path, [])
            self.assertEqual(read_jsonl(path), [])
            self.assertEqual(path.read_text(encoding="utf-8"), "")

    def test_nested_and_unicode(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            rows = [{"k": {"nested": ["é", "日"], "n": None}}]
            write_jsonl(path, rows)
            self.assertEqual(read_jsonl(path), rows)

    def test_blank_lines_are_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.jsonl"
            path.write_text('{"a": 1}\n\n{"a": 2}\n', encoding="utf-8")
            self.assertEqual(read_jsonl(path), [{"a": 1}, {"a": 2}])


class TestZstdFileHelpers(unittest.TestCase):
    def test_compress_then_decompress_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.bin"
            data = b"payload-" * 5000
            src.write_bytes(data)
            comp = compress_file_zstd(src)
            self.assertTrue(comp.exists())
            self.assertEqual(comp.suffix, ".zst")
            self.assertLess(comp.stat().st_size, len(data))
            out = decompress_file_zstd(comp)
            self.assertEqual(out.read_bytes(), data)

    def test_compress_explicit_destination(self) -> None:
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.bin"
            src.write_bytes(b"x" * 100)
            dst = Path(tmp) / "custom.zst"
            self.assertEqual(compress_file_zstd(src, dst), dst)
            self.assertTrue(dst.exists())

    def test_compress_level_is_honoured(self) -> None:
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "src.bin"
            src.write_bytes(b"abcabcabc" * 10000)
            fast = compress_file_zstd(src, Path(tmp) / "fast.zst", level=1)
            best = compress_file_zstd(src, Path(tmp) / "best.zst", level=19)
            self.assertLessEqual(best.stat().st_size, fast.stat().st_size)


class TestTarZstdHelpers(unittest.TestCase):
    def test_pack_and_unpack_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "tree"
            (root / "sub").mkdir(parents=True)
            (root / "a.txt").write_text("alpha", encoding="utf-8")
            (root / "sub" / "b.bin").write_bytes(b"\x00\x01\x02")
            archive = Path(tmp) / "tree.tar.zst"
            pack_tar_zstd(root, archive)
            self.assertTrue(archive.exists())
            dest = Path(tmp) / "out"
            unpack_tar_zstd(archive, dest)
            self.assertEqual((dest / "a.txt").read_text(encoding="utf-8"), "alpha")
            self.assertEqual((dest / "sub" / "b.bin").read_bytes(), b"\x00\x01\x02")

    def test_archive_is_zstd_compressed_tar(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "tree"
            root.mkdir()
            (root / "a.txt").write_text("a" * 5000, encoding="utf-8")
            archive = Path(tmp) / "tree.tar.zst"
            pack_tar_zstd(root, archive)
            # Must be a zstd frame, not a bare tar.
            self.assertEqual(archive.read_bytes()[:4], b"\x28\xb5\x2f\xfd")
            raw = zstandard.ZstdDecompressor().stream_reader(
                archive.open("rb")
            ).read()
            self.assertEqual(len(raw) % 512, 0)
            with tarfile.open(fileobj=__import__("io").BytesIO(raw)) as tf:
                self.assertIn("a.txt", tf.getnames())

    def test_empty_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "empty"
            root.mkdir()
            archive = Path(tmp) / "e.tar.zst"
            pack_tar_zstd(root, archive)
            dest = Path(tmp) / "out"
            unpack_tar_zstd(archive, dest)
            self.assertTrue(dest.exists())


if __name__ == "__main__":
    unittest.main()

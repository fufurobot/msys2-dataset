"""Tests for the SQLite fast-write path.

The spec says: when CSV/JSONL gets too big, fall back to zstd on tar or to
SQLite for fast writes. These tests pin the SQLite side of that contract.
"""

from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from msys2_dataset.sqlite_store import (
    load_rows,
    open_db,
    query_rows,
    write_rows,
    table_names,
)


class TestWriteRows(unittest.TestCase):
    def test_creates_table_and_inserts(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            rows = [{"name": "a", "version": "1"}, {"name": "b", "version": "2"}]
            n = write_rows(db, "pkg", rows)
            self.assertEqual(n, 2)
            self.assertEqual(load_rows(db, "pkg"), rows)

    def test_infers_column_order_from_first_row(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            write_rows(db, "pkg", [{"b": 1, "a": 2}])
            with open_db(db) as conn:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(pkg)")]
            self.assertEqual(cols, ["b", "a"])

    def test_append_mode_adds_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            write_rows(db, "pkg", [{"a": 1}])
            write_rows(db, "pkg", [{"a": 2}], if_exists="append")
            self.assertEqual(load_rows(db, "pkg"), [{"a": 1}, {"a": 2}])

    def test_replace_mode_replaces_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            write_rows(db, "pkg", [{"a": 1}])
            write_rows(db, "pkg", [{"a": 9}], if_exists="replace")
            self.assertEqual(load_rows(db, "pkg"), [{"a": 9}])

    def test_fail_mode_raises_on_existing(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            write_rows(db, "pkg", [{"a": 1}])
            with self.assertRaises(Exception):
                write_rows(db, "pkg", [{"a": 2}], if_exists="fail")

    def test_invalid_if_exists_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_rows(Path(tmp) / "t.sqlite", "pkg", [{"a": 1}], if_exists="bogus")

    def test_empty_rows_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_rows(Path(tmp) / "t.sqlite", "pkg", [])

    def test_unicode_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            rows = [{"n": "café-日本語"}]
            write_rows(db, "pkg", rows)
            self.assertEqual(load_rows(db, "pkg"), rows)

    def test_none_values_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            rows = [{"a": None, "b": "x"}]
            write_rows(db, "pkg", rows)
            self.assertEqual(load_rows(db, "pkg"), rows)

    def test_mixed_int_and_float(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            rows = [{"v": 1}, {"v": 2.5}]
            write_rows(db, "pkg", rows)
            self.assertEqual(load_rows(db, "pkg"), rows)


class TestTableNames(unittest.TestCase):
    def test_lists_created_tables(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            write_rows(db, "alpha", [{"a": 1}])
            write_rows(db, "beta", [{"a": 1}])
            self.assertEqual(sorted(table_names(db)), ["alpha", "beta"])

    def test_empty_db_has_no_tables(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            with open_db(db):
                pass
            self.assertEqual(table_names(db), [])


class TestQueryRows(unittest.TestCase):
    def test_selects_with_where(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            write_rows(db, "pkg", [{"n": "a", "v": 1}, {"n": "b", "v": 2}])
            got = query_rows(db, "SELECT n, v FROM pkg WHERE v > ?", (1,))
            self.assertEqual(got, [{"n": "b", "v": 2}])

    def test_returns_dicts(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            write_rows(db, "pkg", [{"n": "a"}])
            row = query_rows(db, "SELECT * FROM pkg")[0]
            self.assertIsInstance(row, dict)
            self.assertEqual(row["n"], "a")


class TestOpenDb(unittest.TestCase):
    def test_context_manager_commits(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            with open_db(db) as conn:
                conn.execute("CREATE TABLE x (a INTEGER)")
                conn.execute("INSERT INTO x VALUES (1)")
            self.assertEqual(query_rows(db, "SELECT a FROM x"), [{"a": 1}])

    def test_rolls_back_on_error(self) -> None:
        with TemporaryDirectory() as tmp:
            db = Path(tmp) / "t.sqlite"
            with open_db(db) as conn:
                conn.execute("CREATE TABLE x (a INTEGER)")
            with self.assertRaises(sqlite3.OperationalError):
                with open_db(db) as conn:
                    conn.execute("INSERT INTO x VALUES (1)")
                    conn.execute("SELECT * FROM missing_table")


if __name__ == "__main__":
    unittest.main()

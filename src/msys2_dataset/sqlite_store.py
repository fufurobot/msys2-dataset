"""SQLite fast-write path.

When a CSV/JSONL shard gets too large to rewrite efficiently, the loader falls
back to SQLite: it accepts row-at-a-time writes, survives interruption, and
stays a single transportable file. Tables are written from ``dict`` rows with
the column order taken from the first row.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = [
    "load_rows",
    "open_db",
    "query_rows",
    "table_names",
    "write_rows",
]

_VALID_IF_EXISTS = {"fail", "replace", "append"}

_SQL_TYPE_BY_PYTHON: list[tuple[type, str]] = [
    (bool, "INTEGER"),
    (int, "INTEGER"),
    (float, "REAL"),
    (bytes, "BLOB"),
]


def _quote(identifier: str) -> str:
    """Quote a SQL identifier, rejecting embedded quotes."""
    if not identifier or '"' in identifier or "\x00" in identifier:
        raise ValueError(f"invalid SQL identifier: {identifier!r}")
    return '"' + identifier + '"'


def _sql_type(value: Any) -> str:
    for python_type, sql_type in _SQL_TYPE_BY_PYTHON:
        if isinstance(value, python_type) and not isinstance(value, bool):
            return sql_type
        if python_type is bool and isinstance(value, bool):
            return sql_type
    return "TEXT"


@contextmanager
def open_db(path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open ``path`` with sane pragmas; commit on success, roll back on error."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Durability/speed tradeoff appropriate for a regenerable dataset.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def table_names(path: str | Path) -> list[str]:
    """List user tables in the database."""
    with open_db(path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]


def write_rows(
    path: str | Path,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    if_exists: str = "replace",
) -> int:
    """Write ``rows`` into ``table``; returns the number of rows written.

    ``if_exists`` is one of ``replace`` (default), ``append`` or ``fail``.
    """
    if if_exists not in _VALID_IF_EXISTS:
        raise ValueError(f"if_exists must be one of {sorted(_VALID_IF_EXISTS)}, got {if_exists!r}")
    if not rows:
        raise ValueError("cannot write an empty row set: column types are unknown")

    columns = list(rows[0].keys())
    if not columns:
        raise ValueError("rows must have at least one column")

    table_sql = _quote(table)
    col_sql = [_quote(c) for c in columns]

    with open_db(path) as conn:
        exists = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            is not None
        )

        if exists and if_exists == "fail":
            raise ValueError(f"table {table!r} already exists")
        if exists and if_exists == "replace":
            conn.execute(f"DROP TABLE {table_sql}")

        if not exists or if_exists == "replace":
            decls = ", ".join(
                f"{col} {_sql_type(rows[0][name])}"
                for col, name in zip(col_sql, columns, strict=False)
            )
            conn.execute(f"CREATE TABLE {table_sql} ({decls})")

        placeholders = ", ".join("?" for _ in columns)
        sql = f"INSERT INTO {table_sql} ({', '.join(col_sql)}) VALUES ({placeholders})"
        conn.executemany(sql, [[row.get(name) for name in columns] for row in rows])
    return len(rows)


def load_rows(path: str | Path, table: str) -> list[dict[str, Any]]:
    """Read every row of ``table`` back as a list of dicts."""
    return query_rows(path, f"SELECT * FROM {_quote(table)}")


def query_rows(path: str | Path, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    """Run ``sql`` and return rows as dicts."""
    with open_db(path) as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]

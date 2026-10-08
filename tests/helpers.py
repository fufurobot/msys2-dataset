"""Shared test helpers.

The DSH file sandbox denies ``chmod`` on the system temp directory, which makes
stdlib ``tempfile.TemporaryDirectory`` fail during *teardown* even though the
test body succeeded. Tests therefore allocate scratch directories inside the
workspace via :func:`scratch_dir`.
"""

from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

#: Scratch root inside the workspace; gitignored.
SCRATCH_ROOT = Path(__file__).resolve().parents[1] / ".pytest-scratch"


@contextmanager
def scratch_dir(name: str = "case", cleanup: bool = True) -> Iterator[Path]:
    """Yield a fresh writable directory under the workspace.

    Each call gets a unique directory. Reusing one is unsafe: when a previous
    run was interrupted, the leftover directory can carry ACLs that deny
    writes, which would make an unrelated test fail.

    Cleanup is best-effort: sandbox ACLs may prevent removal, which must never
    turn a passing test into a failure.
    """
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    target = SCRATCH_ROOT / f"{name}-{uuid.uuid4().hex[:8]}"
    target.mkdir(parents=True, exist_ok=True)
    try:
        yield target
    finally:
        if cleanup:
            shutil.rmtree(target, ignore_errors=True)

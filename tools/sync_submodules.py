#!/usr/bin/env python
"""Materialise ``data/repo/`` as real git submodules from ``repo-list.txt``.

``data/repo/repo-list.txt`` is the single source of truth for which upstream
package repositories this dataset is built from. This script turns that list
into registered git submodules (``.gitmodules`` + mode-160000 gitlinks), so a
fresh ``git clone --recurse-submodules`` reproduces the exact upstream
revisions the dataset was derived from.

Usage::

    python tools/sync_submodules.py            # add/update every entry
    python tools/sync_submodules.py --check    # report drift, change nothing

Why this script exists rather than a shell loop: ``git submodule add`` shells
out to ``sh``, which is unavailable in some confined environments (the MSYS2
runtime cannot create its shared-memory namespace under the DSH sandbox). The
equivalent plumbing used here needs no shell.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Import the parser from the package rather than duplicating its rules.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from msys2_dataset.repolist import (  # noqa: E402
    SubmoduleSpec,
    build_submodule_plan,
    read_repo_list,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_LIST = REPO_ROOT / "data" / "repo" / "repo-list.txt"
GITMODULES = REPO_ROOT / ".gitmodules"


def _run(args: list[str], cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=False)


def _gitmodules_text(specs: list[SubmoduleSpec]) -> str:
    blocks = []
    for spec in specs:
        blocks.append(
            f'[submodule "{spec.path}"]\n'
            f"\tpath = {spec.path}\n"
            f"\turl = {spec.url}\n"
            f"\tshallow = true\n"
        )
    return "".join(blocks)


def _current_commit(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    result = _run(["git", "rev-parse", "HEAD"], cwd=path)
    return result.stdout.strip() if result.returncode == 0 else None


def _indexed_commit(spec_path: str) -> str | None:
    result = _run(["git", "ls-files", "-s", "--", spec_path])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    return result.stdout.split()[1]


def sync(specs: list[SubmoduleSpec], check: bool) -> int:
    problems = 0

    if check:
        expected = _gitmodules_text(specs)
        actual = GITMODULES.read_text(encoding="utf-8") if GITMODULES.exists() else ""
        if expected != actual:
            print("DRIFT: .gitmodules does not match repo-list.txt", file=sys.stderr)
            problems += 1

    for spec in specs:
        path = REPO_ROOT / spec.path
        if not (path / ".git").exists():
            if check:
                print(f"DRIFT: {spec.path} is not a checkout", file=sys.stderr)
                problems += 1
                continue
            print(f"cloning {spec.url} -> {spec.path}")
            result = _run(["git", "clone", "--depth", "1", spec.url, spec.path])
            if result.returncode != 0:
                print(result.stderr, file=sys.stderr)
                return 1

        if check:
            head = _current_commit(path)
            pinned = _indexed_commit(spec.path)
            if pinned and head and pinned != head:
                print(
                    f"DRIFT: {spec.path} at {head[:7]} but index pins {pinned[:7]}",
                    file=sys.stderr,
                )
                problems += 1
        else:
            _run(["git", "add", "--", spec.path])

    if check:
        print(
            "submodule plan is in sync" if problems == 0 else f"{problems} problem(s) found",
        )
        return 1 if problems else 0

    GITMODULES.write_text(_gitmodules_text(specs), encoding="utf-8")
    _run(["git", "add", "--", ".gitmodules"])
    for spec in specs:
        commit = _current_commit(REPO_ROOT / spec.path)
        print(f"registered {spec.path} @ {commit[:12] if commit else '?'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify submodules match repo-list.txt without changing anything",
    )
    parser.add_argument(
        "--repo-list",
        type=Path,
        default=REPO_LIST,
        help="path to repo-list.txt",
    )
    args = parser.parse_args(argv)

    plan = build_submodule_plan(read_repo_list(args.repo_list))
    if not plan:
        print("repo-list.txt declares no repositories", file=sys.stderr)
        return 1
    return sync(plan, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Execute the code cells of every notebook in-process and report failures.

Jupyter's kernel supervisor needs to write connection files under the system
temp directory, which the DSH sandbox denies, so a real `jupyter execute` is
not possible here. Running the cells in a single shared namespace is a strict
subset of what the kernel does for this project's notebooks (they are linear,
with no async or kernel-magic usage), and it still catches syntax errors,
bad imports, wrong attribute names and failing assertions.

Usage::

    python tools/run_notebooks.py           # execute all notebooks
    python tools/run_notebooks.py --check   # validate JSON only, run nothing
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = REPO_ROOT / "notebooks"


def iter_code_sources(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    for index, cell in enumerate(data["cells"]):
        if cell["cell_type"] == "code":
            source = "".join(cell["source"])
            if source.strip():
                yield index, source


def validate(path: Path) -> int:
    """Structural validation of notebook JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = 0
    if data.get("nbformat") != 4:
        print(f"{path.name}: unexpected nbformat {data.get('nbformat')}", file=sys.stderr)
        problems += 1
    for cell in data["cells"]:
        if not isinstance(cell.get("source"), list):
            print(f"{path.name}: cell source is not a list of lines", file=sys.stderr)
            problems += 1
        if cell["cell_type"] not in {"code", "markdown"}:
            print(f"{path.name}: unknown cell type {cell['cell_type']}", file=sys.stderr)
            problems += 1
    return problems


def execute(path: Path) -> int:
    """Run every code cell in one namespace, mirroring the notebook order."""
    # Notebooks are written to run from the repository root.
    import os

    os.chdir(REPO_ROOT)
    sys.path.insert(0, str(REPO_ROOT / "src"))

    namespace: dict = {"__name__": "__notebook__"}
    failures = 0
    for index, source in iter_code_sources(path):
        try:
            compiled = compile(source, f"{path.name}:cell{index}", "exec")
            exec(compiled, namespace)  # noqa: S102 - test helper for our own notebooks
        except Exception:
            failures += 1
            print(f"\nFAILED {path.name} cell {index}:", file=sys.stderr)
            print(source, file=sys.stderr)
            traceback.print_exc()
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="validate JSON only")
    args = parser.parse_args(argv)

    paths = sorted(NOTEBOOKS.glob("*.ipynb"))
    if not paths:
        print("no notebooks found", file=sys.stderr)
        return 1

    problems = 0
    for path in paths:
        problems += validate(path)
        if not args.check and problems == 0:
            problems += execute(path)
        print(f"{'checked' if args.check else 'executed'} {path.name}")

    if problems:
        print(f"{problems} problem(s)", file=sys.stderr)
        return 1
    print(f"all {len(paths)} notebook(s) OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

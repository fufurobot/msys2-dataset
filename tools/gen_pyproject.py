#!/usr/bin/env python
"""Generate the gitignored ``pyproject.toml`` from ``requirements.txt``.

``requirements.txt`` is the authoritative, committed dependency source.
``pyproject.toml`` and ``uv.lock`` are deliberately gitignored so that the
committed repository stays minimal and there is exactly one place where pins
live. Everything that needs a build file (local dev, Codespaces, CI) runs this
script first.

Runtime dependencies are taken verbatim from ``requirements.txt``; a curated
subset is mirrored into ``[project.optional-dependencies].dev`` so tooling such
as ruff and pytest is not installed into the published wheel's runtime set.

Usage::

    python tools/gen_pyproject.py                 # write pyproject.toml
    python tools/gen_pyproject.py --check         # verify it is up to date
    python tools/gen_pyproject.py --version 0.2.0 # override the version
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = REPO_ROOT / "requirements.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"

PACKAGE_NAME = "msys2-dataset"
DEFAULT_VERSION = "0.1.0"
MIN_PYTHON = "3.10"

#: Packages that only make sense for development and testing.
DEV_PACKAGES = ("ruff", "pytest")

#: Packages needed to build/publish but not to use the library.
BUILD_REQUIRES = ("hatchling",)


def read_requirements(path: Path = REQUIREMENTS) -> list[str]:
    """Return the requirement lines, stripped of comments and blanks."""
    if not path.is_file():
        raise FileNotFoundError(f"requirements file not found: {path}")
    requirements: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            requirements.append(line)
    return requirements


def _requirement_key(requirement: str) -> str:
    """Extract the bare project name from a requirement specifier."""
    return re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip().lower()


def split_requirements(
    requirements: list[str],
) -> tuple[list[str], list[str]]:
    """Split into (runtime, dev) requirement lists."""
    runtime: list[str] = []
    dev: list[str] = []
    for requirement in requirements:
        if _requirement_key(requirement) in DEV_PACKAGES:
            dev.append(requirement)
        else:
            runtime.append(requirement)
    return runtime, dev


def render_pyproject(
    runtime: list[str],
    dev: list[str],
    version: str = DEFAULT_VERSION,
) -> str:
    """Render the pyproject.toml text."""
    deps = "\n".join(f'    "{d}",' for d in runtime)
    dev_deps = "\n".join(f'        "{d}",' for d in dev)
    build = "\n".join(f'    "{b}",' for b in BUILD_REQUIRES)

    dev_section = f"\n[project.optional-dependencies]\ndev = [\n{dev_deps}\n]\n" if dev else ""

    return f"""# GENERATED FILE — DO NOT EDIT.
# Produced by tools/gen_pyproject.py from requirements.txt, which is the
# authoritative dependency source. This file is gitignored on purpose.
[project]
name = "{PACKAGE_NAME}"
version = "{version}"
description = "Dataset pairing MSYS2 and MINGW package metadata."
readme = "README.md"
requires-python = ">={MIN_PYTHON}"
license = {{ text = "MIT" }}
dependencies = [
{deps}
]
{dev_section}
[project.scripts]
msys2-dataset = "msys2_dataset.cli:main"

[build-system]
requires = [
{build}
]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/msys2_dataset"]

[tool.hatch.build.targets.sdist]
# The submodule trees under data/ hold thousands of upstream package files and
# must never be packed into a distribution; neither should local caches.
exclude = [
    "data/repo/**",
    "data/**",
    ".uv-cache/**",
    ".pytest-scratch/**",
    ".venv/**",
    "dist/**",
    ".git/**",
]

[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B"]
ignore = ["E501"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["B011"]
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify, do not write")
    parser.add_argument(
        "--version",
        default=os.environ.get("PACKAGE_VERSION", DEFAULT_VERSION),
        help="version to stamp into the generated file",
    )
    parser.add_argument("--output", type=Path, default=PYPROJECT)
    args = parser.parse_args(argv)

    runtime, dev = split_requirements(read_requirements())
    rendered = render_pyproject(runtime, dev, version=args.version)

    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            print(
                f"{args.output} is out of date; run tools/gen_pyproject.py",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output} is up to date")
        return 0

    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output} ({len(runtime)} runtime, {len(dev)} dev deps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

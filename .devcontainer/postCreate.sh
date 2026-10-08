#!/usr/bin/env bash
# Codespaces / Dev Container setup.
#
# `pyproject.toml` and `uv.lock` are gitignored (requirements.txt is the
# authoritative dependency source), so this script regenerates them before
# running `uv sync`. It is idempotent and safe to re-run.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> generating pyproject.toml from requirements.txt"
python tools/gen_pyproject.py

echo "==> uv sync"
uv sync

echo "==> installing jupyterlab"
uv pip install jupyterlab

echo "==> fetching submodules declared in data/repo/repo-list.txt"
git submodule update --init --depth 1 || \
    python tools/sync_submodules.py

echo "==> done. start Jupyter with:"
echo "    uv run jupyter server --ip=0.0.0.0 --port=8888 --no-browser"

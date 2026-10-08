# Contributing

## Setup

```bash
git clone --recurse-submodules https://github.com/fufurobot/msys2-dataset
cd msys2-dataset

python tools/gen_pyproject.py   # pyproject.toml is gitignored
uv sync
```

git-annex is a system dependency (≥ 10.20230126): `apt-get install git-annex`
or `conda install -c conda-forge git-annex`. Run `datalad get .` to fetch
annexed content.

## The one rule about `pyproject.toml`

`pyproject.toml` and `uv.lock` are **gitignored**. `requirements.txt` is the
authoritative dependency source.

To add a dependency, edit `requirements.txt` and regenerate:

```bash
python tools/gen_pyproject.py
```

CI fails if `pyproject.toml` is stale relative to `requirements.txt`. Never
commit either generated file; if you see them staged, something is wrong with
`.gitignore`.

## Tests come first

The suite uses the standard library `unittest`. Write the failing test first,
commit it as `[WIP]`, then implement until green:

```bash
python -m pytest tests -q
ruff check src tests tools
ruff format --check src tests tools
```

Red commits are labelled `[WIP]`; implementation commits are ordinary
`feat:`/`fix:` commits. Keep history readable — a reviewer should be able to
see the contract before the implementation.

## Adding an upstream repository

`data/repo/repo-list.txt` is the single source of truth. Add a URL (optionally
followed by a directory-name override), then:

```bash
python tools/sync_submodules.py         # clones and registers the submodule
python tools/sync_submodules.py --check # verify no drift
git add .gitmodules data/repo/<name>
```

Every entry becomes a real git submodule pinned to a commit. Do not commit a
plain checkout under `data/repo/`.

`tools/sync_submodules.py` uses git plumbing rather than `git submodule add`,
because the latter shells out to `sh`, which is unavailable in some confined
environments.

## Storage invariants

Any change to `src/msys2_dataset/store.py` must preserve:

1. the container is a **plain, uncompressed** tar;
2. members are named `<table>/<column>.zst.partNN`, POSIX separators only;
3. each part is an independently decompressable zstd frame;
4. **no produced file exceeds 4 GiB** (`FAT32_MAX_BYTES`, i.e. 4 GiB − 1);
5. table and column names are validated against path traversal.

Tests assert all five.

## Commits

- One logical change per commit; write the *why* in the body.
- Commit the staged and unstaged halves of a change separately rather than
  mixing unrelated edits.
- Leave the working tree clean when you stop.

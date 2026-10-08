# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `data/repo/repo-list.txt` and `tools/sync_submodules.py`: `data/repo/` is now
  backed by real git submodules (`msys2-packages`, `MINGW-packages`) pinned to
  commits, driven by the repo list.
- `src/msys2_dataset/store.py`: columnar table format — a plain uncompressed
  tar holding `<table>/<column>.zst.partNN`, with per-part zstd frames and a
  4 GiB − 1 file cap for FAT32 compatibility.
- `src/msys2_dataset/sqlite_store.py`: SQLite fast-write path with
  `replace` / `append` / `fail` modes.
- `src/msys2_dataset/io.py`: CSV and JSONL read/write plus single-file zstd and
  tar.zst helpers.
- `src/msys2_dataset/packages.py`: `ls -la` listing parser and MSYS2 ↔ MINGW
  package pairing by normalised name.
- `src/msys2_dataset/cli.py`: `pair`, `pack`, `submodules` and `info`
  subcommands.
- `tools/gen_pyproject.py`: generates the gitignored `pyproject.toml` from
  `requirements.txt`, with a `--check` mode for CI.
- `tools/make_notebooks.py`, `tools/run_notebooks.py`, `notebooks/01_exploration.ipynb`
  and `notebooks/02_analysis.ipynb`.
- DataLad dataset initialised; `datalad get .` retrieves annexed content.
- CI: `tests.yml` (ruff + pytest on 3.10/3.11/3.12), `publish-testpypi.yml`,
  `sync-huggingface.yml`.
- Binder and Codespaces configuration.
- 128-test `unittest` suite.

### Changed

- `pyproject.toml` and `uv.lock` are gitignored; `requirements.txt` is the
  authoritative dependency source.
- Ruff configured with `E`, `F`, `W`, `I`, `UP`, `B`; the codebase is
  formatted with `ruff format`.
- Distribution metadata excludes `data/` and caches from the sdist
  (8444 members / 5.9 MB → 35 members / 44 KB).

### Fixed

- `FAT32_MAX_BYTES` is 4 GiB − 1 rather than 4 GiB; a 32-bit size field cannot
  address a file of exactly 4 GiB.
- `pack_tar_zstd` emits directories before their contents, so extraction into
  an empty tree succeeds.
- Tar extraction creates intermediate directories explicitly; directories
  created implicitly during extraction do not inherit a writable ACL under
  restrictive sandboxes.
- Part rollover is measured against *compressed* size, and each part is a
  standalone zstd frame.

## [0.1.0] - Initial

### Added

- Initial `msys2-dataset` repository with MSYS2 and MINGW package listings.

[Unreleased]: https://github.com/fufurobot/msys2-dataset/commits/main
[0.1.0]: https://github.com/fufurobot/msys2-dataset/releases/tag/v0.1.0

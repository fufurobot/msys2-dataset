<!-- badges -->
[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/fufurobot/msys2-dataset/HEAD)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/fufurobot/msys2-dataset)
[![DataLad](https://img.shields.io/badge/DataLad-dataset-blue)](https://handbook.datalad.org/)
[![Tests](https://github.com/fufurobot/msys2-dataset/actions/workflows/tests.yml/badge.svg)](https://github.com/fufurobot/msys2-dataset/actions/workflows/tests.yml)
[![TestPyPI](https://img.shields.io/badge/TestPyPI-v0.1.0-blue)](https://test.pypi.org/project/msys2-dataset/)
[![HuggingFace](https://img.shields.io/badge/%F0%9F%A4%97%20Hub-dataset-yellow)](https://huggingface.co/datasets/fufurobot/msys2-dataset)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

# msys2-dataset

Pair **MSYS2** and **MINGW** package metadata to answer a practical question:
*which MSYS2 package corresponds to which MINGW package?*

The two upstream repositories are checked out as real git submodules, their
package directories are parsed, and the two sets are joined on a normalised
name (`mingw-w64-ucrt-x86_64-zlib` → `zlib` → the MSYS2 `zlib` package).

On the current snapshots:

| metric | value |
| --- | --- |
| MSYS2 packages | 608 |
| MINGW packages | 3361 |
| paired (`zlib` ↔ `mingw-w64-zlib`) | **304** |
| MSYS2-only | 304 |
| MINGW-only | 3057 |

Exactly half of the MSYS2 package set has a MINGW counterpart; the MINGW
repository is far larger because it builds each library for several
toolchains (msvcrt, ucrt, clang, clangarm64).

---

## Quick start

### Binder

Click the Binder badge. `environment.yml` provisions Python 3.11 and
git-annex; `.binder/postBuild` initialises the DataLad dataset and fetches
annexed content. The notebooks in `notebooks/` then run as-is.

### GitHub Codespaces

Click the Codespaces badge. `.devcontainer/` installs uv and ruff, then
`.devcontainer/postCreate.sh` regenerates `pyproject.toml`, runs `uv sync`,
installs JupyterLab and initialises the submodules. Start a server with:

```bash
uv run jupyter server --ip=0.0.0.0 --port=8888 --no-browser
```

Codespaces forwards port 8888 automatically.

### Local (uv)

```bash
git clone --recurse-submodules https://github.com/fufurobot/msys2-dataset
cd msys2-dataset

python tools/gen_pyproject.py   # pyproject.toml is gitignored; regenerate it
uv sync                         # or: pip install -r requirements.txt

uv run python tools/run_notebooks.py   # execute both notebooks
uv run python -m pytest tests -q
```

---

## Data

### Submodules (`data/repo/`)

`data/repo/repo-list.txt` is the single source of truth for which upstream
repositories this dataset is built from. `data/repo/` is **not** a plain
directory: each entry is a registered git submodule pinned to a commit, so a
fresh clone reproduces the exact upstream revisions.

```bash
git submodule update --init --depth 1   # fetch what repo-list.txt declares
python tools/sync_submodules.py         # add/update from repo-list.txt
python tools/sync_submodules.py --check # report drift (used by CI)
```

Current pins:

| submodule | commit |
| --- | --- |
| `data/repo/msys2-packages` | `e555935d7e69499c19043fe2dc97599a9900bb3f` |
| `data/repo/MINGW-packages` | `c70dff25f150b26672dbfa8e9da076f5608e4b57` |

`tools/sync_submodules.py` uses git plumbing rather than `git submodule add`,
because the latter shells out to `sh`.

### DataLad / git-annex

The repository is a DataLad dataset (id `c82f7932-78c7-4f81-9be6-364b9cd018da`),
so large files live in git-annex and are retrieved on demand:

```bash
datalad get .          # fetch all annexed content
git annex whereis      # which files are annexed
```

`.gitattributes` keeps code, notebooks, docs, configuration and the small TSV
listings in plain Git (so diffs stay reviewable) and sends anything over 100 kB
to the annex. `data/repo/**` is never annexed.

> git-annex is a system dependency, not a Python package. On Debian/Ubuntu:
> `apt-get install git-annex`; on conda: `conda install -c conda-forge git-annex`.
> DataLad requires ≥ 10.20230126.

### Tabular storage

The dataset is CSV/JSONL oriented. When a shard outgrows a flat file, the
fallbacks are zstd-compressed tar or SQLite for fast writes.

For large tables there is a columnar, zstd-compressed layout: a **plain,
uncompressed tar** containing one directory per table and one or more
`.zst.partNN` members per column.

```
packages.tar                     # uncompressed tar container
└── packages/
    ├── name.zst.part00
    ├── msys2_name.zst.part00
    ├── mingw_name.zst.part00
    └── paired.zst.part00
```

```python
from msys2_dataset.store import write_table, read_table

write_table("data/processed/packages.tar", "packages", columns)
restored = read_table("data/processed/packages.tar", dest)
```

**Every produced file stays under 4 GiB** (`store.FAT32_MAX_BYTES` =
4 GiB − 1) so the dataset remains transportable on FAT32. Columns whose
compressed size would exceed the cap are split into additional parts, and each
part is an independently decompressable zstd frame. The cap is enforced against
*compressed* size, which is what actually lands on disk.

SQLite is used when rows are written incrementally:

```python
from msys2_dataset.sqlite_store import write_rows, load_rows

write_rows("packages.sqlite", "paired", rows, if_exists="replace")
load_rows("packages.sqlite", "paired")
```

SQLite columns are scalar, so list-valued fields are stored as JSON strings.

---

## Command line

```bash
msys2-dataset pair       # emit paired packages as JSONL (+ summary on stderr)
msys2-dataset pack       # write a columnar zstd tar archive
msys2-dataset submodules # report drift against repo-list.txt
msys2-dataset info       # describe the storage constraints
```

---

## Dependency management

`requirements.txt` is the **authoritative, committed** dependency source.
`pyproject.toml` and `uv.lock` are deliberately **gitignored** and generated:

```bash
python tools/gen_pyproject.py            # write pyproject.toml
python tools/gen_pyproject.py --check    # verify it is current (CI uses this)
python tools/gen_pyproject.py --version 1.2.3
```

This keeps one place where pins live while still giving uv, hatchling and CI a
build file. Runtime dependencies are taken verbatim from `requirements.txt`;
ruff and pytest are split into the `dev` extra so they never leak into the
published runtime set.

---

## Testing

```bash
python -m pytest tests -q     # 128 tests
ruff check src tests tools
ruff format --check src tests tools
```

Tests use the standard library `unittest` and cover the parser, the storage
format (including FAT32 enforcement and zstd round-trips), the SQLite path,
package pairing, `repo-list.txt`, the pyproject generator, submodule
registration and notebook execution.

---

## CI

| workflow | trigger | does |
| --- | --- | --- |
| [`tests.yml`](.github/workflows/tests.yml) | push, PR, dispatch | ruff + pytest on Python 3.10/3.11/3.12 |
| [`publish-testpypi.yml`](.github/workflows/publish-testpypi.yml) | push to `main`, `v*` tags | build sdist+wheel, publish to TestPyPI |
| [`sync-huggingface.yml`](.github/workflows/sync-huggingface.yml) | release published | `datalad get .`, upload `data/` to the Hub |

Because `pyproject.toml` is gitignored, every workflow regenerates it with
`tools/gen_pyproject.py` instead of inlining a heredoc.

### Required configuration

| secret / variable | workflow | purpose |
| --- | --- | --- |
| `TEST_PYPI_API_TOKEN` | publish-testpypi | authenticate to test.pypi.org |
| `HF_TOKEN` *(optional)* | sync-huggingface | fallback when OIDC is unavailable |
| `HF_OIDC_RESOURCE` | sync-huggingface | target HF repo (`fufurobot/msys2-dataset`) |

For HuggingFace, configure **Trusted Publishers** (Settings → Trusted
Publishers): provider GitHub Actions, repository `fufurobot/msys2-dataset`,
branch `main`, workflow `sync-huggingface.yml`. Without it, set `HF_TOKEN`.

For models, change `--repo-type model` and adjust the path.

---

## License

MIT — see [LICENSE](LICENSE).

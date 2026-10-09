<!-- badges -->
[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/fufurobot/msys2-dataset/HEAD)
[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/fufurobot/msys2-dataset)
[![DataLad](https://img.shields.io/badge/DataLad-dataset-blue)](https://handbook.datalad.org/)
[![Tests](https://github.com/fufurobot/msys2-dataset/actions/workflows/tests.yml/badge.svg)](https://github.com/fufurobot/msys2-dataset/actions/workflows/tests.yml)
[![TestPyPI](https://img.shields.io/badge/TestPyPI-v0.1.0-blue)](https://test.pypi.org/project/msys2-dataset/)
[![HuggingFace](https://img.shields.io/badge/%F0%9F%A4%97%20Hub-dataset-yellow)](https://huggingface.co/datasets/fufurobot/msys2-dataset)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
# msys2-dataset

Match **Arch Linux** (`core`, `extra`, `AUR`) packages against **MSYS2** and
**MINGW** packages, to answer a practical question:

> This project exists on Arch as `foo`. Does MSYS2 ship it? Does MINGW? Under
> what name?

## Why Arch is the reference point

MSYS2 and MINGW packages are **not** designed to correspond to each other. They
are two different porting efforts of the same upstream software, packaged for
different environments, and they do not track one another's names or versions.
Pairing MSYS2 against MINGW directly is therefore meaningless — it produces
coincidences, not correspondences.

**Arch Linux is the neutral reference.** It packages a very large slice of the
same upstream projects under close-to-upstream names, and it has stable,
machine-readable metadata (exact versions, dependency lists, licenses). So the
dataset joins all three ecosystems on the **normalised upstream project name**:

```
                 Arch: zlib 1:1.3.2-3        (core)
                          │
      normalise ──────────┼────────── normalise
                          │
   MSYS2: zlib      MINGW: mingw-w64-ucrt-x86_64-zlib
```

Normalisation strips the MINGW toolchain prefix
(`mingw-w64-ucrt-x86_64-zlib` → `zlib`) and applies a curated alias table for
the genuinely divergent names (Arch `freetype2` ↔ MSYS2 `freetype`).

## Current figures

### Scrape status

| source | status | packages | notes |
| --- | --- | --- | --- |
| Arch `core` | ✅ complete | 299 | `core.db` |
| Arch `extra` | ✅ complete | 15,025 | `extra.db` |
| AUR | ⚠️ **partial** | ~14,200 / 121,445 | stopped by upstream throttling |
| MSYS2 | ✅ complete | 608 | `data/msys2-pkgs.tsv` |
| MINGW | ✅ complete | 3,361 | `data/mingw-pkgs.tsv` |

> **TODO(aur-full-crawl)** — the AUR sweep is **not** finished. A run reached
> ~14,200 packages before the AUR stopped accepting connections (the HTTP
> client's connect timeout is fixed at 10s) and then throttled this host for a
> sustained period. To complete it, crawl in bounded resumable chunks rather
> than one long loop; `msys2_dataset.arch.fetch_aur_bulk` documents the plan.
> The committed dataset contains **core + extra only**, so every figure below
> excludes the AUR.

### Matching results (core + extra)

| metric | value |
| --- | --- |
| distinct upstream projects | 16,472 |
| Arch projects matched to MSYS2 and/or MINGW | **2,453** |
| Arch-only (no MSYS2/MINGW equivalent) | 12,827 |
| present in all three ecosystems | **281** |
| Arch ↔ MSYS2 | 471 |
| Arch ↔ MINGW | 2,263 |

The asymmetry is the point: MSYS2/MINGW are **POSIX-on-Windows** toolchains, so
they cover the portable core (compression, TLS, build tools, language runtimes)
but not Arch's Linux-only surface (kernel modules, systemd units, GPU stacks).
`all_three = 281` is the set of genuinely portable upstream projects.

The live values are written to
[`data/processed/ecosystem-summary.json`](data/processed/ecosystem-summary.json)
by the scraper; treat the table above as a snapshot.

## Quick start

### Reproduce the data

```bash
git clone --recurse-submodules https://github.com/fufurobot/msys2-dataset
cd msys2-dataset
python tools/gen_pyproject.py   # pyproject.toml is gitignored
uv sync

python tools/scrape_arch.py --no-aur       # official repos only (fast, complete)
python tools/scrape_arch.py --aur-limit 10000   # bounded AUR sample
python tools/scrape_arch.py --reuse        # re-match from the cached scrape
```

> A full AUR run (`python tools/scrape_arch.py`) is ~1200 requests and takes
> well over 20 minutes. It is currently incomplete — see the status table above.
> Prefer bounded `--aur-limit` runs and re-run with `--reuse` to re-match.

The scrape writes:

| file | contents |
| --- | --- |
| `data/arch-pkgs.jsonl.zst` | every scraped Arch/AUR package (zstd) |
| `data/processed/ecosystem-matches.jsonl.zst` | one row per upstream project |
| `data/processed/ecosystem-summary.json` | aggregate counts |

Output is zstd-compressed because the raw JSONL is large: the core+extra scrape
is 10.4 MB plain, **892 KB compressed (11.6×)**, and the match table is 3.4 MB
plain, **152 KB compressed (22×)**.

Query it directly:

```python
from msys2_dataset.arch import fetch_official_repo
from msys2_dataset.matching import match_ecosystems, summarise_matches
from msys2_dataset.packages import parse_ls_listing_file

matches = match_ecosystems(
    fetch_official_repo("core"),
    parse_ls_listing_file("data/msys2-pkgs.tsv"),
    parse_ls_listing_file("data/mingw-pkgs.tsv"),
)
summarise_matches(matches)
```

### Binder

Click the Binder badge. `environment.yml` provisions Python 3.11 and git-annex;
`.binder/postBuild` initialises the DataLad dataset and fetches annexed content.

### GitHub Codespaces

Click the Codespaces badge. `.devcontainer/postCreate.sh` regenerates
`pyproject.toml`, runs `uv sync`, installs JupyterLab and initialises the
submodules. Then:

```bash
uv run jupyter server --ip=0.0.0.0 --port=8888 --no-browser
```

## Notebooks

| notebook | shows |
| --- | --- |
| [`01_exploration.ipynb`](notebooks/01_exploration.ipynb) | parse both listings, pair and inspect overlap |
| [`02_analysis.ipynb`](notebooks/02_analysis.ipynb) | persist matches to the columnar zstd tar and SQLite |

Both are generated by `tools/make_notebooks.py` (so they stay valid nbformat with
no stored outputs) and executed by `tools/run_notebooks.py`.

## Scraper design

`src/msys2_dataset/arch.py` reads three public, unauthenticated sources:

| source | format | why |
| --- | --- | --- |
| `core.db` / `extra.db` | gzipped tar of `<pkg>/desc` | one request yields every package, vs. walking the web UI |
| `packages.gz` (AUR) | gzipped **name list** | enumerates the 121k-package AUR namespace |
| AUR RPC `/rpc/v5/info` | JSON | the name list has no metadata; this supplies it, 200 names per call |

Two things about the upstream formats are easy to get wrong, so both are pinned
by tests:

- `packages.gz` is **only a newline-separated list of names**, not the
  pipe-delimited records its name suggests.
- The AUR `search` endpoint rejects short terms and cannot enumerate the
  namespace, so bulk collection goes through `info` by name.

Network access is delegated to `tools/fetch.js`: Node's TLS stack reaches the
Arch mirrors reliably from confined environments, while Python's `urllib` can
fail per-host. Mirror rotation and retries live there.

## Data

### Submodules (`data/repo/`)

`data/repo/repo-list.txt` is the single source of truth. `data/repo/` holds real
git submodules pinned to commits, so a fresh clone reproduces the exact upstream
revisions used.

| submodule | commit |
| --- | --- |
| `data/repo/msys2-packages` | `e555935d7e69499c19043fe2dc97599a9900bb3f` |
| `data/repo/MINGW-packages` | `c70dff25f150b26672dbfa8e9da076f5608e4b57` |

```bash
git submodule update --init --depth 1
python tools/sync_submodules.py         # add/update from repo-list.txt
python tools/sync_submodules.py --check # verify registration (CI uses this)
```

`--check` validates the **registration** (`.gitmodules` + mode-160000 gitlinks),
which is what `repo-list.txt` governs; a missing working-tree checkout is
reported as a note, not drift, because CI checks out with `submodules: false`.

### DataLad / git-annex

The repository is initialised as a DataLad dataset (id
`c82f7932-78c7-4f81-9be6-364b9cd018da`), so `datalad get .` retrieves annexed
content on a clone that has git-annex available:

```bash
datalad get .
```

`.gitattributes` keeps code, notebooks, docs, config and the JSONL/TSV listings
in plain Git so diffs stay reviewable, and routes anything over 100 kB to the
annex.

> **git-annex is a system dependency (≥ 10.20230126), not a Python package.**
> `apt-get install git-annex`, or `conda install -c conda-forge git-annex`.
>
> It needs a working POSIX shell and coreutils. In environments where the MSYS2
> runtime cannot start (it fails to create its `\BaseNamedObjects` namespace),
> `git annex add` fails at the point it links content into the store, and the
> `pre-commit` hook cannot run. The dataset therefore also ships every artefact
> as a plain zstd-compressed file, so **no git-annex is required to use the
> data** — only to exercise the annex path itself.

### Columnar table storage

For large tables: a **plain uncompressed tar** holding
`<table>/<column>.zst.partNN`, one directory per table, one or more
independently decompressable zstd frames per column.

```
ecosystem-matches.tar
└── matches/
    ├── name.zst.part00
    ├── ecosystems.zst.part00
    └── arch_names.zst.part00
```

```python
from msys2_dataset.store import write_table, read_table

write_table("data/processed/matches.tar", "matches", columns)
```

**No produced file exceeds 4 GiB** (`store.FAT32_MAX_BYTES` = 4 GiB − 1), so the
dataset stays transportable on FAT32. The cap is enforced against *compressed*
size, which is what lands on disk; oversized columns split into more parts.

`data/processed/*.jsonl` is used while the dataset is small enough to diff; the
tar format is the transport for when it is not. SQLite
(`msys2_dataset.sqlite_store`) is the fast-write fallback for incremental loads.

## Command line

```bash
msys2-dataset pair        # pair msys2 against mingw (legacy; see note below)
msys2-dataset pack        # write a columnar zstd tar archive
msys2-dataset submodules  # report drift against repo-list.txt
msys2-dataset info        # describe the storage constraints
```

> `pair` predates the Arch work and compares MSYS2 against MINGW. Since those
> ecosystems are not designed to correspond, prefer the Arch-based matching in
> `msys2_dataset.matching`. `pair` is retained only for the storage-format
> demonstrations in the notebooks.

## Dependency management

`requirements.txt` is the **authoritative, committed** dependency source;
`pyproject.toml` and `uv.lock` are gitignored and generated:

```bash
python tools/gen_pyproject.py            # write
python tools/gen_pyproject.py --check    # verify (CI uses this)
```

Ruff and pytest are exactly pinned and split into the `dev` extra so they never
leak into the published runtime set — an unpinned linter makes CI formatting
sensitive to the runner's resolve order.

## Testing

```bash
python -m pytest tests -q     # 172 tests
ruff check src tests tools
ruff format --check src tests tools
```

The scraper's parsers are tested against fixtures, never the network, so the
suite is deterministic and offline.

## CI

| workflow | trigger | does |
| --- | --- | --- |
| [`tests.yml`](.github/workflows/tests.yml) | push, PR, dispatch | ruff + pytest on Python 3.10/3.11/3.12 |
| [`publish-testpypi.yml`](.github/workflows/publish-testpypi.yml) | push to `main`, `v*` tags | build sdist+wheel, publish to TestPyPI |
| [`sync-huggingface.yml`](.github/workflows/sync-huggingface.yml) | release published | `datalad get .`, upload `data/` to the Hub |

### Required configuration

| secret / variable | workflow | purpose |
| --- | --- | --- |
| `TEST_PYPI_API_TOKEN` | publish-testpypi | authenticate to test.pypi.org |
| `HF_TOKEN` *(optional)* | sync-huggingface | fallback when OIDC is unavailable |
| `HF_OIDC_RESOURCE` | sync-huggingface | target HF repo (`fufurobot/msys2-dataset`) |

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

The full text of the license is available in the [LICENSE](LICENSE) file.

### Network Use (AGPL Section 13)

This license includes a "network use" clause. In accordance with **Section 13** of the AGPL-3.0:

If you modify this software and make it available to users over a computer network (for example, as a web service), you **must** offer those users the opportunity to receive the corresponding source code of your modified version, at no charge.

### Source Code Availability

The source code for this project is available at [here](https://github.com/fufurobot/msys2-dataset).

## Problem

`data/repo/` and the annex workflow assume `git-annex` is available, but it is a
**system** dependency with no usable Python distribution:

- `pip install git-annex` installs a Python wrapper; what DataLad actually needs
  is the `git-annex` binary on `PATH` or resolvable as `git annex`.
- In this development environment git-annex is absent from PATH, the MSYS2
  mirrors are unreachable, and no `msys2_shell.cmd` route can install it.

So the DataLad path (`datalad get .`) cannot be exercised end to end here, and CI
has to install git-annex separately (`apt-get install git-annex`).

## What does work today

The repository **is** a valid DataLad dataset. `datalad create -d .` succeeded
once a git-annex binary was on `PATH`, producing `.datalad/config` with dataset id
`c82f7932-78c7-4f81-9be6-364b9cd018da` and `.datalad/.gitattributes`. DataLad
reports `is_installed: True` with `repo: AnnexRepo`.

The binary that satisfied DataLad was version `10.20260901`, comfortably above the
required `>=10.20230126`.

## Proposal

- Document git-annex as an explicit system prerequisite (done in README).
- Add a preflight check that fails with an actionable message when git-annex is
  missing or too old, instead of surfacing a deep DataLad traceback.
- Consider a documented fallback path (plain Git + zstd artifacts) for users who
  only need the tabular data and not annex.

## Acceptance criteria

- [ ] `datalad get .` is covered by a smoke test that skips cleanly when
      git-annex is unavailable.
- [ ] Missing or too-old git-annex produces an actionable error message.

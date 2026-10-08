## Problem

CI's `ruff format --check` failed while the identical command passed locally.

`requirements.txt` pinned `ruff==0.4.4`, but the workflow installed whatever
resolved in CI, which was `0.16.10`. The two versions disagreed about collapsing
implicit string concatenation in `tests/test_tooling.py`:

```diff
-  "# a comment\n" "pandas==2.2.2\n" "\n" "numpy==1.26.4  # inline comment\n",
+  "# a comment\npandas==2.2.2\n\nnumpy==1.26.4  # inline comment\n",
```

A second instance of the same class of bug hit **import sorting**: isort decides
first-party vs third-party by *importability*, so the expected import order
differed between

- CI, where the package is installed into the venv, and
- local, where only `src/` was on `PYTHONPATH`.

## Resolution

- Bumped the pin to `ruff==0.16.10` (`2146d33`).
- Pinned ruff's view of the layout in the generated config
  (`src = ["src", "tests"]`, `[tool.ruff.lint.isort] known-first-party`) so the
  classification is environment-independent (`9eb9a0f`).
- Added guard tests asserting `ruff` and `pytest` stay `==`-pinned and that dev
  tools never leak into the runtime dependency set.

## Lesson

Any tool whose output CI asserts must be pinned exactly, otherwise the build
becomes sensitive to the runner's dependency resolution.

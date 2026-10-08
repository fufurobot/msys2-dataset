## Problem

`tools/sync_submodules.py --check` originally treated a submodule that is not
checked out as drift:

```
DRIFT: data/repo/msys2-packages is not a checkout
DRIFT: data/repo/MINGW-packages is not a checkout
```

That conflates two different facts:

| fact | where it lives | governed by |
| --- | --- | --- |
| registration (`.gitmodules` + mode-160000 gitlink) | git index | `repo-list.txt` |
| checkout (working tree present) | working tree | `git submodule update` |

CI checks out with `submodules: false` because the tests only need the
registration, so the check failed on a perfectly healthy repository.

## Resolution

Fixed in `5bd8194`. `--check` now:

- fails only when `.gitmodules` or the gitlinks drift from `repo-list.txt`;
- reports missing checkouts as an informational note and exits 0;
- still compares the checked-out `HEAD` against the pinned gitlink whenever a
  checkout exists, so genuine version drift is still caught.

Verified by removing both submodule working trees: `--check` prints the note and
exits 0, while an unregistered submodule still exits 1 (covered by
`test_unregistered_submodule_is_reported_as_drift`).

## Follow-up

- [ ] Should CI also assert the submodules are *fetchable*? That would need a
      separate, slower job using `submodules: recursive`.

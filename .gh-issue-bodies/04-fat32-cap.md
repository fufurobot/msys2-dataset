## Problem

The specification says "no file shall exceed 4GB to make it compatible with
FAT32". Implementing that literally as `4 * 1024**3` is subtly wrong.

FAT32 stores file size in a **32-bit** field, so the largest addressable file is
**4 GiB − 1**. A file of exactly 4 GiB cannot be represented, which is why
`FAT32_MAX_BYTES` is one byte below the boundary.

## Resolution

`store.FAT32_MAX_BYTES = 4 * 1024**3 - 1`.

Coverage in `tests/test_store.py`:

- `test_limit_is_one_byte_below_4gib`
- `test_limit_strictly_below_4gb_boundary`
- `test_no_part_exceeds_configured_cap` — measured against the **compressed**
  size, which is what actually lands on disk
- `test_all_members_within_fat32_limit` and `test_archive_itself_within_limit`

## Note for reviewers

Because the cap is enforced on *compressed* size, highly compressible input can
legitimately stay in a single part whose raw size far exceeds the cap. That is
intended and is pinned by `test_highly_compressible_data_stays_in_one_part`; it
is why the rollover tests feed incompressible data.

Related invariants enforced by the same module:

- the container is a **plain, uncompressed** tar;
- members are named `<table>/<column>.zst.partNN` with POSIX separators;
- each part is an independently decompressable zstd frame;
- table and column names are validated against path traversal.

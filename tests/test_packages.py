"""Tests for parsing MSYS2 / MINGW package listings and pairing the two trees.

`data/msys2-pkgs.tsv` and `data/mingw-pkgs.tsv` are `ls -la` listings of the
two package repositories. Pairing them is the point of the dataset: which
MSYS2 package corresponds to which MINGW package.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from msys2_dataset.packages import (
    PackageEntry,
    normalise_name,
    pair_packages,
    parse_ls_listing,
    parse_ls_listing_file,
    summarise,
)

LS_SAMPLE = """total 564
-rw-r--r-- 1 fufu fufu 1490 Sep 24 04:49 LICENSE
-rw-r--r-- 1 fufu fufu 2072 Sep 24 04:49 README.md
drwxr-xr-x 1 fufu fufu    0 Sep 24 04:49 bash
drwxr-xr-x 1 fufu fufu    0 Sep 24 04:49 ack
drwxr-xr-x 1 fufu fufu    0 Sep 24 04:49 ansible-core
"""


class TestParseLsListing(unittest.TestCase):
    def test_parses_only_directories(self) -> None:
        entries = parse_ls_listing(LS_SAMPLE)
        names = [e.name for e in entries]
        self.assertEqual(names, ["bash", "ack", "ansible-core"])

    def test_ignores_total_line(self) -> None:
        entries = parse_ls_listing(LS_SAMPLE)
        self.assertNotIn("total", [e.name for e in entries])

    def test_captures_size_and_date(self) -> None:
        entry = parse_ls_listing(LS_SAMPLE)[0]
        self.assertEqual(entry.name, "bash")
        self.assertEqual(entry.size, 0)
        self.assertEqual(entry.date, "Sep 24 04:49")

    def test_empty_input(self) -> None:
        self.assertEqual(parse_ls_listing(""), [])

    def test_only_files_yields_empty(self) -> None:
        text = "total 4\n-rw-r--r-- 1 u g 10 Jan  1 00:00 a.txt\n"
        self.assertEqual(parse_ls_listing(text), [])

    def test_handles_varying_spacing(self) -> None:
        text = "drwxr-xr-x 1 fufu fufu 0 Sep  8 08:22   mingw-w64-7zip\n"
        entries = parse_ls_listing(text)
        self.assertEqual([e.name for e in entries], ["mingw-w64-7zip"])

    def test_handles_crlf(self) -> None:
        text = "drwxr-xr-x 1 fufu fufu 0 Sep 24 04:49 bash\r\n"
        entries = parse_ls_listing(text)
        self.assertEqual([e.name for e in entries], ["bash"])
        self.assertNotIn("\r", entries[0].name)

    def test_handles_names_with_spaces(self) -> None:
        text = "drwxr-xr-x 1 fufu fufu 0 Sep 24 04:49 my package\n"
        entries = parse_ls_listing(text)
        self.assertEqual(entries[0].name, "my package")

    def test_malformed_line_is_skipped_not_fatal(self) -> None:
        text = "drwxr-xr-x\nnot a listing at all\n"
        self.assertEqual(parse_ls_listing(text), [])


class TestParseLsListingFile(unittest.TestCase):
    def test_reads_real_msys2_listing(self) -> None:
        root = Path(__file__).resolve().parents[1]
        path = root / "data" / "msys2-pkgs.tsv"
        if not path.exists():
            self.skipTest("data/msys2-pkgs.tsv not present")
        entries = parse_ls_listing_file(path)
        names = {e.name for e in entries}
        self.assertIn("bash", names)
        self.assertIn("base-devel", names)
        self.assertNotIn("LICENSE", names)

    def test_reads_real_mingw_listing(self) -> None:
        root = Path(__file__).resolve().parents[1]
        path = root / "data" / "mingw-pkgs.tsv"
        if not path.exists():
            self.skipTest("data/mingw-pkgs.tsv not present")
        entries = parse_ls_listing_file(path)
        names = {e.name for e in entries}
        self.assertIn("mingw-w64-7zip", names)
        self.assertNotIn("README.md", names)


class TestNormaliseName(unittest.TestCase):
    def test_strips_mingw_prefix(self) -> None:
        self.assertEqual(normalise_name("mingw-w64-7zip"), "7zip")

    def test_strips_ucrt_and_clang_variants(self) -> None:
        self.assertEqual(normalise_name("mingw-w64-ucrt-x86_64-zlib"), "zlib")
        self.assertEqual(normalise_name("mingw-w64-clang-x86_64-zlib"), "zlib")

    def test_lowercases(self) -> None:
        self.assertEqual(normalise_name("MinHook"), "minhook")

    def test_leaves_plain_msys2_names(self) -> None:
        self.assertEqual(normalise_name("bash"), "bash")

    def test_idempotent(self) -> None:
        once = normalise_name("mingw-w64-ucrt-x86_64-SDL2")
        self.assertEqual(normalise_name(once), once)


class TestPairPackages(unittest.TestCase):
    def test_pairs_matching_names(self) -> None:
        msys2 = [PackageEntry(name="zlib"), PackageEntry(name="bash")]
        mingw = [PackageEntry(name="mingw-w64-zlib"), PackageEntry(name="mingw-w64-curl")]
        pairs = pair_packages(msys2, mingw)
        matched = {p.name: p for p in pairs}
        self.assertIn("zlib", matched)
        self.assertIsNotNone(matched["zlib"].mingw)
        self.assertIsNone(matched["bash"].mingw)
        self.assertIn("curl", matched)

    def test_mingw_only_entries_present(self) -> None:
        pairs = pair_packages([], [PackageEntry(name="mingw-w64-7zip")])
        self.assertEqual(len(pairs), 1)
        self.assertIsNone(pairs[0].msys2)
        self.assertEqual(pairs[0].name, "7zip")

    def test_is_sorted_by_name(self) -> None:
        msys2 = [PackageEntry(name="zzz"), PackageEntry(name="aaa")]
        pairs = pair_packages(msys2, [])
        self.assertEqual([p.name for p in pairs], ["aaa", "zzz"])

    def test_both_sides_set_when_matched(self) -> None:
        msys2 = [PackageEntry(name="zlib")]
        mingw = [PackageEntry(name="mingw-w64-zlib")]
        pair = pair_packages(msys2, mingw)[0]
        self.assertEqual(pair.msys2.name, "zlib")
        self.assertEqual(pair.mingw.name, "mingw-w64-zlib")

    def test_both_none_is_excluded(self) -> None:
        self.assertEqual(pair_packages([], []), [])


class TestSummarise(unittest.TestCase):
    def test_counts(self) -> None:
        msys2 = [PackageEntry(name="zlib"), PackageEntry(name="bash")]
        mingw = [PackageEntry(name="mingw-w64-zlib")]
        stats = summarise(pair_packages(msys2, mingw))
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["paired"], 1)
        self.assertEqual(stats["msys2_only"], 1)
        self.assertEqual(stats["mingw_only"], 0)

    def test_empty(self) -> None:
        stats = summarise([])
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["paired"], 0)


class TestRealDataEndToEnd(unittest.TestCase):
    def test_pairs_real_listings(self) -> None:
        root = Path(__file__).resolve().parents[1]
        msys2_path = root / "data" / "msys2-pkgs.tsv"
        mingw_path = root / "data" / "mingw-pkgs.tsv"
        if not (msys2_path.exists() and mingw_path.exists()):
            self.skipTest("real listings not present")
        msys2 = parse_ls_listing_file(msys2_path)
        mingw = parse_ls_listing_file(mingw_path)
        self.assertGreater(len(msys2), 100)
        self.assertGreater(len(mingw), 100)
        stats = summarise(pair_packages(msys2, mingw))
        self.assertGreater(stats["paired"], 50)
        self.assertEqual(stats["total"], stats["paired"] + stats["msys2_only"] + stats["mingw_only"])


if __name__ == "__main__":
    unittest.main()

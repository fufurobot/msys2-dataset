"""Tests for the Arch Linux scraper's parsing and matching logic.

The network is not touched: fixtures exercise the parsers directly, so these
tests are deterministic and run offline.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
import unittest
from pathlib import Path

from msys2_dataset.arch import (
    ArchPackage,
    _iter_desc_members,
    dependency_name,
    fetch_aur_info,
    parse_aur_packages_gz,
    parse_aur_rpc,
    parse_desc,
    write_arch_jsonl,
)
from msys2_dataset.matching import (
    NAME_ALIASES,
    build_arch_index,
    match_ecosystems,
    normalise_arch_name,
    summarise_matches,
)
from msys2_dataset.packages import PackageEntry
from tests.helpers import scratch_dir

DESC_ZLIB = """%FILENAME%
zlib-1:1.3.2-3-x86_64.pkg.tar.zst

%NAME%
zlib

%BASE%
zlib

%VERSION%
1:1.3.2-3

%DESC%
Compression library implementing the deflate compression method found in gzip and PKZIP

%GROUPS%
%

%URL%
https://zlib.net/

%LICENSE%
Zlib

%DEPENDS%
glibc

%MAKEDEPENDS%
%

%OPTDEPENDS%
%

"""

DESC_WITH_PROVIDES = """%NAME%
python-requests

%VERSION%
2.32.3-1

%DESC%
Python HTTP library

%PROVIDES%
python-requests
%LICENSE%
Apache
%DEPENDS%
python-urllib3>=2.0
python-certifi
"""


class TestDependencyName(unittest.TestCase):
    def test_strips_version_constraints(self) -> None:
        self.assertEqual(dependency_name("glibc>=2.38"), "glibc")
        self.assertEqual(dependency_name("python-urllib3>=2.0"), "python-urllib3")
        self.assertEqual(dependency_name("libfoo=1.2-3"), "libfoo")
        self.assertEqual(dependency_name("libfoo<2"), "libfoo")

    def test_leaves_plain_names(self) -> None:
        self.assertEqual(dependency_name("zlib"), "zlib")

    def test_lowercases(self) -> None:
        self.assertEqual(dependency_name("ZLIB"), "zlib")

    def test_handles_soname_provides(self) -> None:
        self.assertEqual(dependency_name("libfoo.so=1-64"), "libfoo.so")


class TestParseDesc(unittest.TestCase):
    def test_parses_scalar_fields(self) -> None:
        fields = parse_desc(DESC_ZLIB)
        self.assertEqual(fields["name"], "zlib")
        self.assertEqual(fields["version"], "1:1.3.2-3")
        self.assertEqual(fields["base"], "zlib")
        self.assertEqual(fields["url"], "https://zlib.net/")

    def test_parses_list_fields(self) -> None:
        fields = parse_desc(DESC_ZLIB)
        self.assertEqual(fields["license"], ["Zlib"])
        self.assertEqual(fields["depends"], ["glibc"])

    def test_empty_section_becomes_empty_list(self) -> None:
        fields = parse_desc(DESC_ZLIB)
        self.assertEqual(fields["groups"], [])
        self.assertEqual(fields["makedepends"], [])

    def test_does_not_leak_values_across_sections(self) -> None:
        fields = parse_desc(DESC_WITH_PROVIDES)
        self.assertEqual(fields["name"], "python-requests")
        self.assertEqual(fields["license"], ["Apache"])
        self.assertEqual(fields["depends"], ["python-urllib3>=2.0", "python-certifi"])

    def test_multiline_section_keeps_order(self) -> None:
        fields = parse_desc(DESC_WITH_PROVIDES)
        self.assertEqual(fields["provides"], ["python-requests"])

    def test_empty_input(self) -> None:
        self.assertEqual(parse_desc(""), {})


class TestIterDescMembers(unittest.TestCase):
    def _make_db(self, entries: dict[str, str]) -> bytes:
        """Build a fake repo db; keys are literal member paths."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for member_name, text in entries.items():
                data = text.encode()
                info = tarfile.TarInfo(member_name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buffer.getvalue()

    def test_yields_each_desc(self) -> None:
        raw = self._make_db({"zlib-1.3/desc": DESC_ZLIB})
        found = list(_iter_desc_members(raw))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], "zlib-1.3")

    def test_ignores_non_desc_members(self) -> None:
        raw = self._make_db({"pkg-1/files": "usr/bin/x\n"})
        self.assertEqual(list(_iter_desc_members(raw)), [])

    def test_does_not_match_a_name_merely_ending_in_desc(self) -> None:
        """`zdesc` is not `desc`; the match must be on the full component."""
        raw = self._make_db({"pkg-1/zdesc": "should be ignored\n"})
        self.assertEqual(list(_iter_desc_members(raw)), [])

    def test_parses_multiple_packages_in_one_db(self) -> None:
        raw = self._make_db(
            {
                "zlib-1.3/desc": DESC_ZLIB,
                "python-requests-2.32/desc": DESC_WITH_PROVIDES,
            }
        )
        names = {fallback for fallback, _ in _iter_desc_members(raw)}
        self.assertEqual(names, {"zlib-1.3", "python-requests-2.32"})


class TestParseAurPackagesGz(unittest.TestCase):
    """`packages.gz` is only a newline-separated list of names, not metadata."""

    def _gz(self, lines: list[str]) -> bytes:
        return gzip.compress("\n".join(lines).encode())

    def test_returns_names(self) -> None:
        blob = self._gz(["yay", "paru", "zlib-ng"])
        self.assertEqual(parse_aur_packages_gz(blob), ["yay", "paru", "zlib-ng"])

    def test_skips_blank_lines(self) -> None:
        blob = self._gz(["a", "", "b", "  "])
        self.assertEqual(parse_aur_packages_gz(blob), ["a", "b"])

    def test_empty_input(self) -> None:
        self.assertEqual(parse_aur_packages_gz(gzip.compress(b"")), [])


class TestParseAurRpc(unittest.TestCase):
    """Metadata comes from the RPC, whose shape is pinned here."""

    SAMPLE = {
        "resultcount": 1,
        "results": [
            {
                "Name": "yay",
                "Version": "12.3.5-1",
                "Description": "Yet another yogurt. Pacman wrapper and AUR helper.",
                "Maintainer": "jguer",
                "NumVotes": 2500,
                "Popularity": 5.25,
                "URL": "https://github.com/Jguer/yay",
                "URLPath": "/cgit/aur.git/snapshot/yay.tar.gz",
                "OutOfDate": None,
                "PackageBase": "yay",
                "Depends": ["git", "pacman>6.1"],
                "MakeDepends": ["go"],
                "License": ["GPL-3.0-only"],
                "Provides": ["yay-bin"],
                "OptDepends": ["sudo: for installation"],
                "Conflicts": ["yay-bin"],
            }
        ],
    }

    def test_parses_core_fields(self) -> None:
        pkg = parse_aur_rpc(self.SAMPLE)[0]
        self.assertEqual(pkg.name, "yay")
        self.assertEqual(pkg.version, "12.3.5-1")
        self.assertEqual(pkg.repo, "aur")
        self.assertEqual(pkg.maintainer, "jguer")
        self.assertEqual(pkg.votes, 2500)
        self.assertAlmostEqual(pkg.popularity, 5.25)
        self.assertIsNone(pkg.out_of_date)

    def test_parses_relationship_fields(self) -> None:
        pkg = parse_aur_rpc(self.SAMPLE)[0]
        self.assertEqual(pkg.depends, ["git", "pacman>6.1"])
        self.assertEqual(pkg.makedepends, ["go"])
        self.assertEqual(pkg.license, ["GPL-3.0-only"])
        self.assertEqual(pkg.conflicts, ["yay-bin"])
        self.assertEqual(pkg.num_depends, 2)

    def test_description_containing_pipes_is_preserved(self) -> None:
        payload = {
            "results": [{"Name": "x", "Description": "a|b|c tool", "Depends": [], "NumVotes": 0}]
        }
        self.assertEqual(parse_aur_rpc(payload)[0].description, "a|b|c tool")

    def test_unmaintained_package_has_none_maintainer(self) -> None:
        payload = {"results": [{"Name": "orphan", "Maintainer": None, "Depends": []}]}
        self.assertIsNone(parse_aur_rpc(payload)[0].maintainer)

    def test_out_of_date_is_parsed(self) -> None:
        payload = {"results": [{"Name": "old", "OutOfDate": 1700000000, "Depends": []}]}
        self.assertEqual(parse_aur_rpc(payload)[0].out_of_date, 1700000000)

    def test_missing_optional_lists_become_empty(self) -> None:
        payload = {"results": [{"Name": "bare"}]}
        pkg = parse_aur_rpc(payload)[0]
        self.assertEqual(pkg.depends, [])
        self.assertEqual(pkg.license, [])
        self.assertIsNone(pkg.maintainer)

    def test_empty_results(self) -> None:
        self.assertEqual(parse_aur_rpc({"resultcount": 0, "results": []}), [])


class TestWriteArchJsonl(unittest.TestCase):
    def test_roundtrips_and_sorts(self) -> None:
        packages = [
            ArchPackage(name="zzz", repo="extra", version="1"),
            ArchPackage(name="aaa", repo="core", version="2"),
        ]
        with scratch_dir("arch1") as tmp:
            path = write_arch_jsonl(packages, Path(tmp) / "arch.jsonl")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["name"] for r in rows], ["aaa", "zzz"])
        self.assertEqual(rows[0]["repo"], "core")

    def test_row_contains_expected_fields(self) -> None:
        with scratch_dir("arch2") as tmp:
            path = write_arch_jsonl([ArchPackage(name="a")], Path(tmp) / "a.jsonl")
            row = json.loads(path.read_text(encoding="utf-8").strip())
        for field_name in ("name", "version", "repo", "depends", "license", "votes"):
            self.assertIn(field_name, row)


class TestNormaliseArchName(unittest.TestCase):
    def test_lowercases_and_strips_constraints(self) -> None:
        self.assertEqual(normalise_arch_name("ZLIB>=1.0"), "zlib")

    def test_applies_alias_table(self) -> None:
        # Arch's `freetype2` is MSYS2's `freetype`.
        self.assertEqual(normalise_arch_name("freetype2"), NAME_ALIASES["freetype2"])
        self.assertEqual(normalise_arch_name("gtk3"), "gtk3")

    def test_unknown_name_passes_through(self) -> None:
        self.assertEqual(normalise_arch_name("some-obscure-tool"), "some-obscure-tool")


class TestBuildArchIndex(unittest.TestCase):
    def test_groups_by_normalised_name(self) -> None:
        packages = [
            ArchPackage(name="zlib", repo="core"),
            ArchPackage(name="zlib", repo="extra"),
            ArchPackage(name="yay", repo="aur"),
        ]
        index = build_arch_index(packages)
        self.assertEqual(len(index["zlib"]), 2)
        self.assertEqual(len(index["yay"]), 1)


class TestMatchEcosystems(unittest.TestCase):
    def test_matches_across_all_three(self) -> None:
        arch = [ArchPackage(name="zlib", repo="core")]
        msys2 = [PackageEntry(name="zlib")]
        mingw = [PackageEntry(name="mingw-w64-zlib")]
        matches = match_ecosystems(arch, msys2, mingw)
        by_name = {m.name: m for m in matches}
        self.assertIn("zlib", by_name)
        self.assertEqual(by_name["zlib"].ecosystems, ["arch", "msys2", "mingw"])

    def test_arch_only_project_is_kept(self) -> None:
        matches = match_ecosystems([ArchPackage(name="archonly", repo="extra")], [], [])
        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0].in_arch)
        self.assertFalse(matches[0].in_msys2)

    def test_msys2_only_project_is_kept(self) -> None:
        matches = match_ecosystems([], [PackageEntry(name="msysonly")], [])
        self.assertEqual([m.name for m in matches], ["msysonly"])

    def test_sorted_by_name(self) -> None:
        arch = [ArchPackage(name="zzz"), ArchPackage(name="aaa")]
        self.assertEqual([m.name for m in match_ecosystems(arch, [], [])], ["aaa", "zzz"])

    def test_stopwords_excluded_by_default(self) -> None:
        arch = [ArchPackage(name="glibc"), ArchPackage(name="zlib")]
        names = [m.name for m in match_ecosystems(arch, [], [])]
        self.assertNotIn("glibc", names)
        self.assertIn("zlib", names)

    def test_stopwords_can_be_included(self) -> None:
        arch = [ArchPackage(name="glibc")]
        names = [m.name for m in match_ecosystems(arch, [], [], include_stopwords=True)]
        self.assertIn("glibc", names)

    def test_matches_multiple_arch_repos_for_one_project(self) -> None:
        arch = [
            ArchPackage(name="zlib", repo="core"),
            ArchPackage(name="zlib", repo="extra"),
        ]
        match = match_ecosystems(arch, [], [])[0]
        self.assertEqual([p.repo for p in match.arch], ["core", "extra"])

    def test_as_row_is_json_serialisable(self) -> None:
        arch = [ArchPackage(name="zlib", repo="core")]
        row = match_ecosystems(arch, [PackageEntry(name="zlib")], [])[0].as_row()
        json.dumps(row)  # must not raise
        self.assertEqual(row["arch_repos"], ["core"])
        self.assertTrue(row["in_msys2"])


class TestSummariseMatches(unittest.TestCase):
    def test_counts_combinations(self) -> None:
        arch = [ArchPackage(name="a"), ArchPackage(name="b"), ArchPackage(name="c")]
        msys2 = [PackageEntry(name="a"), PackageEntry(name="b")]
        mingw = [PackageEntry(name="a")]
        stats = summarise_matches(match_ecosystems(arch, msys2, mingw))
        self.assertEqual(stats["arch_total"], 3)
        self.assertEqual(stats["all_three"], 1)  # only "a"
        self.assertEqual(stats["arch_msys2"], 2)  # "a" and "b"
        self.assertEqual(stats["arch_matched"], 2)

    def test_empty(self) -> None:
        stats = summarise_matches([])
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["arch_matched"], 0)


class TestFetchAurInfoSignature(unittest.TestCase):
    def test_empty_name_list_makes_no_request(self) -> None:
        # Must not hit the network when there is nothing to ask about.
        self.assertEqual(fetch_aur_info([]), [])


class TestScrapeCli(unittest.TestCase):
    """The scraper script must import and wire up without touching the network."""

    def setUp(self) -> None:
        import sys

        tools = Path(__file__).resolve().parents[1] / "tools"
        sys.path.insert(0, str(tools))
        import scrape_arch

        self.scrape_arch = scrape_arch

    def test_module_exposes_entry_points(self) -> None:
        self.assertTrue(hasattr(self.scrape_arch, "main"))
        self.assertTrue(hasattr(self.scrape_arch, "load_arch_jsonl"))

    def test_roundtrips_arch_jsonl(self) -> None:
        packages = [
            ArchPackage(name="zlib", version="1:1.3.2-3", repo="core", depends=["glibc"]),
            ArchPackage(name="yay", version="13.0.1-1", repo="aur", votes=2500),
        ]
        with scratch_dir("cli1") as tmp:
            path = write_arch_jsonl(packages, Path(tmp) / "arch.jsonl")
            loaded = self.scrape_arch.load_arch_jsonl(path)
        self.assertEqual(len(loaded), 2)
        by_name = {p.name: p for p in loaded}
        self.assertEqual(by_name["zlib"].depends, ["glibc"])
        self.assertEqual(by_name["yay"].votes, 2500)
        self.assertEqual(by_name["yay"].repo, "aur")

    def test_loads_zstd_compressed_artefact(self) -> None:
        """The committed artefact is `.jsonl.zst`; the loader must accept it."""
        import zstandard

        with scratch_dir("cli3") as tmp:
            plain = write_arch_jsonl([ArchPackage(name="zlib", repo="core")], Path(tmp) / "a.jsonl")
            packed = Path(tmp) / "a.jsonl.zst"
            packed.write_bytes(zstandard.ZstdCompressor(level=3).compress(plain.read_bytes()))
            loaded = self.scrape_arch.load_arch_jsonl(packed)
        self.assertEqual([p.name for p in loaded], ["zlib"])

    def test_reuse_without_cache_fails_cleanly(self) -> None:
        with scratch_dir("cli2") as tmp:
            rc = self.scrape_arch.main(["--reuse", "--out", str(Path(tmp) / "missing.jsonl")])
        self.assertEqual(rc, 1)


class TestRealScrapeArtefacts(unittest.TestCase):
    """If a scrape has been run, its outputs must be well formed."""

    ROOT = Path(__file__).resolve().parents[1]

    def test_summary_is_valid_json_if_present(self) -> None:
        path = self.ROOT / "data" / "processed" / "ecosystem-summary.json"
        if not path.is_file():
            self.skipTest("no scrape has been run")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("stats", data)
        self.assertIn("counts", data)
        self.assertGreater(data["counts"]["arch"], 0)

    def test_matches_jsonl_rows_have_expected_shape(self) -> None:
        plain = self.ROOT / "data" / "processed" / "ecosystem-matches.jsonl"
        packed = plain.with_suffix(".jsonl.zst")
        if packed.is_file():
            import zstandard

            text = (
                zstandard.ZstdDecompressor()
                .decompress(packed.read_bytes(), max_output_size=1 << 31)
                .decode("utf-8")
            )
        elif plain.is_file():
            text = plain.read_text(encoding="utf-8")
        else:
            self.skipTest("no scrape has been run")
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        self.assertGreater(len(rows), 0)
        for row in rows[:200]:
            self.assertIn("name", row)
            self.assertIn("ecosystems", row)
            for key in ("in_arch", "in_msys2", "in_mingw"):
                self.assertIsInstance(row[key], bool)

    def test_arch_jsonl_rows_load_back(self) -> None:
        plain = self.ROOT / "data" / "arch-pkgs.jsonl"
        packed = plain.with_suffix(".jsonl.zst")
        if packed.is_file():
            import zstandard

            text = (
                zstandard.ZstdDecompressor()
                .decompress(packed.read_bytes(), max_output_size=1 << 31)
                .decode("utf-8")
            )
        elif plain.is_file():
            text = plain.read_text(encoding="utf-8")
        else:
            self.skipTest("no scrape has been run")
        package = ArchPackage(**json.loads(text.splitlines()[0]))
        self.assertTrue(package.name)
        self.assertTrue(package.repo)


if __name__ == "__main__":
    unittest.main()

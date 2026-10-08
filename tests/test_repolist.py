"""Tests for repo-list.txt parsing and git submodule planning.

The `data/repo/` tree MUST be real git submodules cloned from the remotes
listed in `data/repo/repo-list.txt`. These tests pin the contract that an
agent or CI job can consume without shelling out to `git` by hand.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from msys2_dataset.repolist import (
    RepoEntry,
    parse_repo_list,
    parse_repo_list_text,
    repo_dir_name,
    build_submodule_plan,
    read_repo_list,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestParseRepoListText(unittest.TestCase):
    def test_parses_plain_urls(self) -> None:
        entries = parse_repo_list_text(
            "https://github.com/msys2/msys2-packages.git\n"
            "https://github.com/msys2/MINGW-packages.git\n"
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].url, "https://github.com/msys2/msys2-packages.git")
        self.assertEqual(entries[1].url, "https://github.com/msys2/MINGW-packages.git")

    def test_derives_directory_name_from_url(self) -> None:
        entries = parse_repo_list_text("https://github.com/msys2/msys2-packages.git\n")
        self.assertEqual(entries[0].name, "msys2-packages")

    def test_handles_crlf_line_endings(self) -> None:
        """repo-list.txt is authored on Windows and committed with CRLF."""
        entries = parse_repo_list_text(
            "https://github.com/msys2/msys2-packages.git\r\n"
            "https://github.com/msys2/MINGW-packages.git\r\n"
        )
        self.assertEqual([e.name for e in entries], ["msys2-packages", "MINGW-packages"])
        for entry in entries:
            self.assertNotIn("\r", entry.url)

    def test_ignores_blank_lines_and_comments(self) -> None:
        entries = parse_repo_list_text(
            "# msys2 package repos\n"
            "\n"
            "https://github.com/msys2/msys2-packages.git\n"
            "   \n"
            "# mingw\n"
            "https://github.com/msys2/MINGW-packages.git\n"
        )
        self.assertEqual(len(entries), 2)

    def test_strips_inline_whitespace(self) -> None:
        entries = parse_repo_list_text("   https://github.com/msys2/msys2-packages.git   \n")
        self.assertEqual(entries[0].url, "https://github.com/msys2/msys2-packages.git")

    def test_supports_name_override_column(self) -> None:
        entries = parse_repo_list_text(
            "https://github.com/msys2/msys2-packages.git msys2\n"
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].name, "msys2")
        self.assertEqual(entries[0].url, "https://github.com/msys2/msys2-packages.git")

    def test_supports_scp_like_ssh_urls(self) -> None:
        entries = parse_repo_list_text("git@github.com:msys2/msys2-packages.git\n")
        self.assertEqual(entries[0].name, "msys2-packages")

    def test_rejects_non_url_garbage(self) -> None:
        with self.assertRaises(ValueError):
            parse_repo_list_text("not a url at all\n")

    def test_empty_list_is_empty(self) -> None:
        self.assertEqual(parse_repo_list_text(""), [])


class TestRepoDirName(unittest.TestCase):
    def test_strips_git_suffix(self) -> None:
        self.assertEqual(repo_dir_name("https://github.com/msys2/foo.git"), "foo")

    def test_without_git_suffix(self) -> None:
        self.assertEqual(repo_dir_name("https://github.com/msys2/foo"), "foo")

    def test_trailing_slash(self) -> None:
        self.assertEqual(repo_dir_name("https://github.com/msys2/foo/"), "foo")


class TestReadRepoList(unittest.TestCase):
    def test_reads_committed_repo_list(self) -> None:
        path = FIXTURES / "repo-list.txt"
        entries = read_repo_list(path)
        self.assertEqual(
            [e.name for e in entries], ["msys2-packages", "MINGW-packages"]
        )

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_repo_list(FIXTURES / "does-not-exist.txt")

    def test_real_repository_repo_list_is_valid(self) -> None:
        """The repo-list.txt committed at data/repo/ must parse."""
        root = Path(__file__).resolve().parents[1]
        path = root / "data" / "repo" / "repo-list.txt"
        if not path.exists():
            self.skipTest("data/repo/repo-list.txt not present")
        entries = read_repo_list(path)
        self.assertGreaterEqual(len(entries), 2)
        names = {e.name for e in entries}
        self.assertIn("msys2-packages", names)
        self.assertIn("MINGW-packages", names)
        for entry in entries:
            self.assertTrue(entry.url.startswith(("https://", "git@")), entry.url)


class TestBuildSubmodulePlan(unittest.TestCase):
    def test_plan_targets_data_repo_dir(self) -> None:
        entries = parse_repo_list_text("https://github.com/msys2/msys2-packages.git\n")
        plan = build_submodule_plan(entries)
        self.assertEqual(len(plan), 1)
        # POSIX-style relative paths so the plan is portable to Linux CI.
        self.assertEqual(plan[0].path, "data/repo/msys2-packages")
        self.assertEqual(plan[0].url, entries[0].url)

    def test_plan_is_deterministic(self) -> None:
        text = (
            "https://github.com/msys2/msys2-packages.git\n"
            "https://github.com/msys2/MINGW-packages.git\n"
        )
        self.assertEqual(
            build_submodule_plan(parse_repo_list_text(text)),
            build_submodule_plan(parse_repo_list_text(text)),
        )


class TestRepoEntry(unittest.TestCase):
    def test_is_hashable_and_comparable(self) -> None:
        a = RepoEntry(url="https://github.com/msys2/foo.git", name="foo")
        b = RepoEntry(url="https://github.com/msys2/foo.git", name="foo")
        self.assertEqual(a, b)
        self.assertEqual(len({a, b}), 1)


if __name__ == "__main__":
    unittest.main()

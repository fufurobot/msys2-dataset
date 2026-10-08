"""Tests for the repository tooling: pyproject generation and submodule sync.

``pyproject.toml`` is gitignored and generated from ``requirements.txt``, so
the generator is load-bearing for local dev, Codespaces and CI. These tests
pin its behaviour, plus the ``repo-list.txt`` -> ``.gitmodules`` contract.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from tests.helpers import scratch_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = REPO_ROOT / "tools"
sys.path.insert(0, str(TOOLS))

import gen_pyproject  # noqa: E402
import sync_submodules  # noqa: E402


class TestGenPyproject(unittest.TestCase):
    def test_read_requirements_skips_comments_and_blanks(self) -> None:
        with scratch_dir("gp1") as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text(
                "# a comment\n" "pandas==2.2.2\n" "\n" "numpy==1.26.4  # inline comment\n",
                encoding="utf-8",
            )
            self.assertEqual(
                gen_pyproject.read_requirements(req),
                ["pandas==2.2.2", "numpy==1.26.4"],
            )

    def test_missing_requirements_raises(self) -> None:
        with scratch_dir("gp2") as tmp:
            with self.assertRaises(FileNotFoundError):
                gen_pyproject.read_requirements(Path(tmp) / "nope.txt")

    def test_split_moves_dev_packages_out_of_runtime(self) -> None:
        runtime, dev = gen_pyproject.split_requirements(
            ["pandas==2.2.2", "ruff==0.4.4", "pytest==8.2.0", "numpy==1.26.4"]
        )
        self.assertEqual(runtime, ["pandas==2.2.2", "numpy==1.26.4"])
        self.assertEqual(dev, ["ruff==0.4.4", "pytest==8.2.0"])

    def test_rendered_output_parses_as_toml(self) -> None:
        with scratch_dir("gp3") as tmp:
            req = Path(tmp) / "requirements.txt"
            req.write_text("pandas==2.2.2\nruff==0.4.4\npytest==8.2.0\n", encoding="utf-8")
            runtime, dev = gen_pyproject.split_requirements(gen_pyproject.read_requirements(req))
            text = gen_pyproject.render_pyproject(runtime, dev, version="9.9.9")
            out = Path(tmp) / "pyproject.toml"
            out.write_text(text, encoding="utf-8")

            tomllib = __import__("tomllib") if sys.version_info >= (3, 11) else None
            if tomllib is None:
                try:
                    import tomli as tomllib  # type: ignore
                except ImportError:
                    self.skipTest("no TOML parser available")

            data = tomllib.loads(text)
            self.assertEqual(data["project"]["name"], "msys2-dataset")
            self.assertEqual(data["project"]["version"], "9.9.9")
            self.assertIn("pandas==2.2.2", data["project"]["dependencies"])
            self.assertNotIn("pytest==8.2.0", data["project"]["dependencies"])
            self.assertIn("pytest==8.2.0", data["project"]["optional-dependencies"]["dev"])
            self.assertEqual(data["build-system"]["build-backend"], "hatchling.build")

    def test_generated_file_is_marked_generated(self) -> None:
        text = gen_pyproject.render_pyproject(["pandas==2.2.2"], ["pytest==8.2.0"])
        self.assertIn("GENERATED FILE", text)
        self.assertIn("DO NOT EDIT", text)

    def test_check_mode_reports_up_to_date(self) -> None:
        with scratch_dir("gp4") as tmp:
            out = Path(tmp) / "pyproject.toml"
            rc = gen_pyproject.main(["--output", str(out), "--version", "1.2.3"])
            self.assertEqual(rc, 0)
            rc = gen_pyproject.main(["--output", str(out), "--check", "--version", "1.2.3"])
            self.assertEqual(rc, 0)

    def test_check_mode_detects_drift(self) -> None:
        with scratch_dir("gp5") as tmp:
            out = Path(tmp) / "pyproject.toml"
            gen_pyproject.main(["--output", str(out), "--version", "1.0.0"])
            rc = gen_pyproject.main(["--output", str(out), "--check", "--version", "2.0.0"])
            self.assertEqual(rc, 1)

    def test_real_pyproject_is_reproducible(self) -> None:
        """The committed requirements.txt must regenerate pyproject.toml."""
        rc = gen_pyproject.main(["--check"])
        self.assertEqual(
            rc,
            0,
            "pyproject.toml is stale; run python tools/gen_pyproject.py",
        )


class TestSyncSubmodules(unittest.TestCase):
    def test_gitmodules_text_matches_plan(self) -> None:
        from msys2_dataset.repolist import RepoEntry, build_submodule_plan

        plan = build_submodule_plan(
            [RepoEntry(url="https://github.com/msys2/msys2-packages.git", name="msys2-packages")]
        )
        text = sync_submodules._gitmodules_text(plan)
        self.assertIn('[submodule "data/repo/msys2-packages"]', text)
        self.assertIn("path = data/repo/msys2-packages", text)
        self.assertIn("url = https://github.com/msys2/msys2-packages.git", text)

    def test_gitmodules_text_is_parseable_by_git_config(self) -> None:
        """`.gitmodules` must be valid git config syntax."""
        from msys2_dataset.repolist import RepoEntry, build_submodule_plan

        plan = build_submodule_plan(
            [
                RepoEntry(url="https://github.com/msys2/msys2-packages.git", name="msys2-packages"),
                RepoEntry(url="https://github.com/msys2/MINGW-packages.git", name="MINGW-packages"),
            ]
        )
        with scratch_dir("ss1") as tmp:
            path = Path(tmp) / ".gitmodules"
            path.write_text(sync_submodules._gitmodules_text(plan), encoding="utf-8")
            result = subprocess.run(
                ["git", "config", "--file", str(path), "--list"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("submodule.data/repo/msys2-packages.url", result.stdout)
            self.assertIn("submodule.data/repo/MINGW-packages.path", result.stdout)

    def test_real_repo_is_in_sync(self) -> None:
        rc = sync_submodules.main(["--check"])
        self.assertEqual(rc, 0, "submodules drift from repo-list.txt")


class TestRealSubmodules(unittest.TestCase):
    """data/repo/ must be real submodules, not plain checkouts."""

    def test_gitmodules_exists_and_lists_both_repos(self) -> None:
        path = REPO_ROOT / ".gitmodules"
        self.assertTrue(path.is_file(), ".gitmodules is required")
        text = path.read_text(encoding="utf-8")
        self.assertIn("data/repo/msys2-packages", text)
        self.assertIn("data/repo/MINGW-packages", text)

    def test_gitlinks_are_staged_with_mode_160000(self) -> None:
        result = subprocess.run(
            ["git", "ls-files", "-s", "data/repo/"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        gitlinks = [line for line in result.stdout.splitlines() if line.startswith("160000")]
        self.assertGreaterEqual(len(gitlinks), 2, result.stdout)
        for line in gitlinks:
            # mode, sha, stage, path
            fields = line.split()
            self.assertEqual(fields[0], "160000")
            self.assertEqual(len(fields[1]), 40, "submodule must pin a commit sha")
            self.assertEqual(fields[2], "0")

    def test_repo_list_declares_the_same_paths(self) -> None:
        from msys2_dataset.repolist import build_submodule_plan, read_repo_list

        plan = build_submodule_plan(read_repo_list(REPO_ROOT / "data" / "repo" / "repo-list.txt"))
        declared = {spec.path for spec in plan}
        self.assertEqual(
            declared,
            {"data/repo/msys2-packages", "data/repo/MINGW-packages"},
        )


if __name__ == "__main__":
    unittest.main()

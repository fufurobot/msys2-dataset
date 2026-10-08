#!/usr/bin/env python
"""File the repository's tracked issues from `.gh-issue-bodies/*.md`.

Issue bodies live as markdown files rather than inline strings so they can be
reviewed and diffed like any other document. This script is idempotent: an issue
whose title already exists is skipped, so it is safe to re-run after editing a
body.

Requires ``GITHUB_TOKEN`` in the environment (or a ``.env`` file next to the
repository root, which is gitignored).

Usage::

    python tools/file_issues.py --dry-run
    python tools/file_issues.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BODIES = REPO_ROOT / ".gh-issue-bodies"

#: Stable mapping from body file to issue title. Titles are the identity used
#: for the idempotency check, so they must not drift casually.
ISSUES: dict[str, str] = {
    "01-git-annex.md": "git-annex is a system dependency and cannot be provisioned from Python",
    "02-submodule-check.md": "Submodule check must distinguish registration from checkout",
    "03-ruff-pin.md": "Pin ruff exactly so local and CI formatting agree",
    "04-fat32-cap.md": "FAT32 cap must be 4 GiB - 1, not 4 GiB",
}

API = "https://api.github.com"


def _token() -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token.strip()
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("GITHUB_TOKEN is not set and no .env was found")


def _request(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "msys2-dataset-issue-filer")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="fufurobot/msys2-dataset")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    token = _token()

    try:
        existing = {
            issue["title"]
            for issue in _request("GET", f"{API}/repos/{args.repo}/issues?state=all", token)
        }
    except urllib.error.HTTPError as exc:
        print(f"could not list issues: {exc}", file=sys.stderr)
        return 1

    created = skipped = failed = 0
    for filename, title in ISSUES.items():
        path = BODIES / filename
        if not path.is_file():
            print(f"missing body file: {path}", file=sys.stderr)
            failed += 1
            continue

        if title in existing:
            print(f"skipped (already filed): {title}")
            skipped += 1
            continue

        if args.dry_run:
            print(f"would file: {title}  <- {filename}")
            continue

        try:
            issue = _request(
                "POST",
                f"{API}/repos/{args.repo}/issues",
                token,
                {"title": title, "body": path.read_text(encoding="utf-8")},
            )
        except urllib.error.HTTPError as exc:
            print(f"failed to file {title!r}: {exc} {exc.read()[:200]!r}", file=sys.stderr)
            failed += 1
            continue

        print(f"filed #{issue['number']}: {issue['title']}")
        print(f"  {issue['html_url']}")
        created += 1

    print(f"\n{created} created, {skipped} skipped, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

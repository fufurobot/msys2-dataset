#!/usr/bin/env python
"""Open a pull request carrying a markdown body file.

Kept deliberately small and idempotent: if an open PR already exists for the
head branch, its body is updated instead of opening a duplicate.

Usage::

    python tools/open_pr.py --head spec/implementation --base main \
        --title "feat: implement the data-mining template" \
        --body .gh-pr-body.md
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
    request.add_header("User-Agent", "msys2-dataset-pr-opener")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default="fufurobot/msys2-dataset")
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", default="main")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", type=Path, required=True)
    args = parser.parse_args(argv)

    token = _token()
    body = args.body.read_text(encoding="utf-8")

    # Reuse an existing open PR for this head branch rather than duplicating it.
    owner = args.repo.split("/")[0]
    open_prs = _request(
        "GET", f"{API}/repos/{args.repo}/pulls?state=open&head={owner}:{args.head}", token
    )
    if open_prs:
        pr = open_prs[0]
        updated = _request(
            "PATCH", f"{API}/repos/{args.repo}/pulls/{pr['number']}", token, {"body": body}
        )
        print(f"updated existing PR #{updated['number']}: {updated['html_url']}")
        return 0

    try:
        pr = _request(
            "POST",
            f"{API}/repos/{args.repo}/pulls",
            token,
            {"title": args.title, "head": args.head, "base": args.base, "body": body},
        )
    except urllib.error.HTTPError as exc:
        print(f"failed to open PR: {exc} {exc.read()[:300]!r}", file=sys.stderr)
        return 1

    print(f"opened PR #{pr['number']}: {pr['title']}")
    print(f"  {pr['html_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

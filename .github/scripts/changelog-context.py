#!/usr/bin/env python3
"""Fetch everything the release-changelog skill reads, so the agent itself needs no shell (SEC-D-12).

    python3 -I .github/scripts/changelog-context.py <release.json> <out.json>

Reads the saved release (gh release view --json ...), finds every pull request it links to, and writes their
number, title, body (cut to BODY_LIMIT characters) and author login to <out.json>, one GraphQL query per 100.
Runs before the agent, in a step that holds the read-only job token; the agent then reads files only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPO = os.environ.get("GITHUB_REPOSITORY", "pipeshub-ai/pipeshub-ai")
BODY_LIMIT = 4000
BATCH = 100
_PR = re.compile(r"/pull/(\d+)\b")


def pr_numbers(release_body: str) -> list[int]:
    return sorted({int(n) for n in _PR.findall(release_body or "")})


def query(numbers: list[int]) -> str:
    owner, name = REPO.split("/", 1)
    fields = "number title body author { login }"
    aliases = " ".join(f"p{n}: pullRequest(number: {n}) {{ {fields} }}" for n in numbers)
    return f'{{ repository(owner: "{owner}", name: "{name}") {{ {aliases} }} }}'


def fetch(numbers: list[int]) -> list[dict]:
    out = []
    for i in range(0, len(numbers), BATCH):
        batch = numbers[i:i + BATCH]
        result = subprocess.run(["gh", "api", "graphql", "-f", f"query={query(batch)}"],
                                check=True, capture_output=True, text=True)
        repo = json.loads(result.stdout)["data"]["repository"]
        for n in batch:
            pr = repo.get(f"p{n}")
            if pr:
                out.append({"number": pr["number"], "title": pr["title"], "body": (pr["body"] or "")[:BODY_LIMIT],
                            "author": (pr.get("author") or {}).get("login")})
    return out


def main(argv: list[str]) -> int:
    release_path, out_path = argv
    with open(release_path, encoding="utf-8") as handle:
        release = json.load(handle)
    prs = fetch(pr_numbers(release.get("body", "")))
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump({"pullRequests": prs}, handle, indent=1)
    print(f"saved {len(prs)} pull request(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Rank the functions that burned CPU in a py-spy raw profile.

    python3 top_functions.py <profile.raw> [top_n]

Two views, because they answer different questions:

* by function — the leaf frame that was executing.
* by subsystem — every sample whose stack touches a subsystem, so nested cost
  lands on the thing that caused it. A template compile, for example, spends
  most of its time in the lexer and parser; the leaf view scatters that across
  half a dozen jinja2 frames and hides how much it adds up to.
"""
from __future__ import annotations

import collections
import re
import sys

# Threads parked waiting for work are not doing any; py-spy still samples them.
IDLE = re.compile(
    r"thread\.py:9\d|threading\.py:(359|1012|655)|selectors\.py:4|resource_tracker"
)

SUBSYSTEMS = {
    "record fetch + decode": ("blob_storage", "base64.py"),
    "graph driver": ("neo4j/", "arango"),
    "citation processing": ("citations.py",),
    "context building": ("chat_helpers.py",),
    "prompt templates": ("jinja2",),
    "tool schemas": ("_bind_tools", "json_schema"),
    "JSON encode": ("json/encoder",),
    "JSON decode": ("json/decoder",),
    "LLM stream": ("langchain", "openai/", "httpx"),
    "asyncio": ("asyncio/",),
}


def load(path: str) -> list[tuple[str, int]]:
    out = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.rstrip()
            cut = line.rfind(" ")
            try:
                n = int(line[cut + 1:])
            except ValueError:
                continue
            out.append((line[:cut], n))
    return out


def main(path: str, top_n: int = 15) -> int:
    stacks = load(path)
    if not stacks:
        print("  (empty profile)")
        return 0

    leaves: collections.Counter[str] = collections.Counter()
    groups: collections.Counter[str] = collections.Counter()
    total = 0
    for stack, n in stacks:
        leaf = stack.split(";")[-1].strip()
        if IDLE.search(leaf):
            continue
        total += n
        leaves[leaf] += n
        for name, markers in SUBSYSTEMS.items():
            if any(m in stack for m in markers):
                groups[name] += n
    if not total:
        print("  (no active samples — the service was idle)")
        return 0

    print(f"  {total} samples of executing Python\n")
    print("  by subsystem (a sample counts for every subsystem in its stack):")
    for name, n in groups.most_common():
        print(f"    {100 * n / total:6.2f}%  {name}")
    print(f"\n  by function (top {top_n}):")
    for leaf, n in leaves.most_common(top_n):
        print(f"    {100 * n / total:6.2f}%  {leaf[:88]}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: top_functions.py <profile.raw> [top_n]")
    sys.exit(main(args[0], int(args[1]) if len(args) > 1 else 15))

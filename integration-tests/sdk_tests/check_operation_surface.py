#!/usr/bin/env python3
"""Fail when an operation's generated SDK surface differs from the one generated from main.

Usage: check_operation_surface.py <operationId> <current work dir> <base work dir>

Both directories hold the checkouts `generate_sdks.sh` leaves behind
(`pipeshub-sdk-typescript`, `-python`, `-go`): <current> was generated from this
commit's spec, <base> from main's. For each language the files of the operation are
compared with comments removed, so a change to the spec's prose passes and a change to
what the SDK exposes fails. That is the point of documenting caching and range headers
in prose: declaring them would give the TypeScript operation `hdrs: true` and a
`{headers, result}` return type, and every caller would break.

A missing checkout, or an operation with no files, fails: a language that did not
generate must not read as "unchanged".
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

LANGUAGES = ("typescript", "python", "go")
_SUFFIX = {"typescript": ".ts", "python": ".py", "go": ".go"}
_SKIP_DIRS = {"node_modules", ".git", "examples", "docs", "tests", "__tests__", "vendor", "esm", "dist"}
_MATCHER_CODES = ("206", "304", "416")


def _words(name: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name)]


def _names(operation: str) -> dict[str, set[str]]:
    """Spellings of the operation id that identify its files and methods."""
    w = _words(operation)
    return {
        "file": {"".join(w), "_".join(w)},
        "python": {"_".join(w), "_".join(w) + "_async"},
        "go": {"".join(x.title() for x in w)},
        "typescript": {w[0] + "".join(x.title() for x in w[1:])},
        "id": {operation},
    }


def strip_comments(text: str, language: str) -> str:
    """The code without comments, docstrings and blank lines. String literals are kept whole."""
    hash_comments = language == "python"
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if hash_comments and text.startswith(('"""', "'''"), i):
            end = text.find(text[i:i + 3], i + 3)
            i = n if end < 0 else end + 3          # a docstring: dropped
        elif c in "\"'" or (c == "`" and not hash_comments):
            q = text[i:i + 1]
            j = i + 1
            while j < n and text[j] != q and text[j] != "\n" or (j < n and q == "`" and text[j] != "`"):
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif hash_comments and c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif not hash_comments and two == "//":
            while i < n and text[i] != "\n":
                i += 1
        elif not hash_comments and two == "/*":
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            out.append(c)
            i += 1
    return "\n".join(line.rstrip() for line in "".join(out).splitlines() if line.strip())


def _method_blocks(text: str, language: str, names: set[str]) -> list[str]:
    """Function bodies named like the operation inside a file that holds many operations."""
    blocks: list[str] = []
    if language == "python":
        pat = re.compile(r"(?m)^( *)(?:async )?def (%s)\(" % "|".join(map(re.escape, names)))
        for m in pat.finditer(text):
            indent = len(m.group(1))
            end = re.compile(r"(?m)^ {0,%d}(?:@|(?:async )?def |class )\S" % indent)
            nxt = end.search(text, m.end())
            blocks.append(text[m.start(): nxt.start() if nxt else len(text)])
    elif language == "typescript":
        pat = re.compile(r"(?m)^( *)(?:async )?(%s)\(" % "|".join(map(re.escape, names)))
        for m in pat.finditer(text):
            close = re.compile(r"(?m)^%s\}" % m.group(1)).search(text, m.end())
            blocks.append(text[m.start(): close.end() if close else len(text)])
    elif language == "go":
        pat = re.compile(r"(?m)^func \([^)]*\) (%s)\(" % "|".join(map(re.escape, names)))
        for m in pat.finditer(text):
            nxt = re.compile(r"(?m)^func ").search(text, m.end())
            blocks.append(text[m.start(): nxt.start() if nxt else len(text)])
    return blocks


def operation_files(sdk: Path, language: str, operation: str) -> dict[str, str]:
    """Relative path -> normalised source, for every piece of the generated operation."""
    names = _names(operation)
    found: dict[str, str] = {}
    for path in sorted(sdk.rglob(f"*{_SUFFIX[language]}")):
        rel = path.relative_to(sdk)
        if _SKIP_DIRS & set(rel.parts[:-1]):
            continue
        stem = re.sub(r"[-_]", "", path.stem.lower())
        text = path.read_text(encoding="utf-8", errors="replace")
        joined = "".join(_words(operation))
        if stem.endswith(joined) or stem.endswith(joined + "op"):
            found[str(rel)] = strip_comments(text, language)
        elif language == "typescript" and re.search(r'operationID:\s*"%s"' % re.escape(operation), text):
            found[str(rel)] = strip_comments(text, language)
        else:
            blocks = _method_blocks(text, language, names[language])
            if blocks:
                found[f"{rel}::{operation}"] = strip_comments("\n".join(blocks), language)
    return found


def shape_problems(language: str, files: dict[str, str]) -> list[str]:
    """Rules that hold whatever main generated: the answer stays a stream with no header wrapper."""
    if language != "typescript":
        return []
    problems = []
    for rel, src in files.items():
        if "hdrs: true" in src:
            problems.append(f"{rel}: the operation declares response headers (hdrs: true)")
        if not rel.startswith("src/funcs/"):
            continue
        if not re.search(r"M\.stream(?:<[^>]*>)?\(\s*200\b", src):
            problems.append(f"{rel}: the 200 answer is no longer M.stream(200)")
        for m in re.finditer(r"M\.\w+(?:<[^>]*>)?\(\s*(\[[^\]]*\]|[^,\s)]+)", src):
            for code in _MATCHER_CODES:
                if re.search(r"\b%s\b" % code, m.group(1)):
                    problems.append(f"{rel}: a matcher for status {code}")
    return problems


def compare(operation: str, current: Path, base: Path) -> list[str]:
    problems: list[str] = []
    for lang in LANGUAGES:
        name = f"pipeshub-sdk-{lang}"
        cur_dir, base_dir = current / name, base / name
        missing = [(label, d) for label, d in (("current", cur_dir), ("base", base_dir)) if not d.is_dir()]
        problems += [f"{lang}: the {label} checkout {d} is missing" for label, d in missing]
        if missing:
            continue
        cur = operation_files(cur_dir, lang, operation)
        old = operation_files(base_dir, lang, operation)
        if not cur:
            problems.append(f"{lang}: no generated file for {operation} in the current checkout")
        if not old:
            problems.append(f"{lang}: no generated file for {operation} in the base checkout")
        if not cur or not old:
            continue
        problems += [f"{lang}: {p}" for p in shape_problems(lang, cur)]
        for rel in sorted(set(cur) | set(old)):
            if cur.get(rel) == old.get(rel):
                continue
            if rel not in old or rel not in cur:
                problems.append(f"{lang}: {rel} exists only in the {'current' if rel in cur else 'base'} generation")
                continue
            diff = difflib.unified_diff(old[rel].splitlines(), cur[rel].splitlines(), "base", "current", lineterm="", n=0)
            problems.append(f"{lang}: {rel} changed:\n" + "\n".join(list(diff)[:20]))
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    operation, current, base = argv[1], Path(argv[2]), Path(argv[3])
    problems = compare(operation, current, base)
    if problems:
        print(f"The generated surface of {operation} changed:", file=sys.stderr)
        for p in problems:
            print(f"- {p}", file=sys.stderr)
        return 1
    print(f"{operation}: the generated surface is unchanged in {', '.join(LANGUAGES)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

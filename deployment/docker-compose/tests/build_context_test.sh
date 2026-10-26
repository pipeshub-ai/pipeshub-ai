#!/usr/bin/env bash
# The build context Docker sends for the app image stays under 250 MB.
# .dockerignore is applied with the same rules Docker uses for the patterns
# this repository relies on (directory prefixes, **, and the skill-pack exception).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
python3 - "$ROOT" <<'PY'
import fnmatch
import os
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
budget = 250 * 1024 * 1024
patterns: list[str] = []
exceptions: list[str] = []
for raw in (root / ".dockerignore").read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("!"):
        exceptions.append(line[1:])
    else:
        patterns.append(line)

def matches(rel: str, pattern: str) -> bool:
    pattern = pattern.strip().rstrip("/")
    if not pattern:
        return False
    if pattern.startswith("**/"):
        body = pattern[3:]
        if not body:
            return False
        if body.startswith("*."):
            return fnmatch.fnmatch(Path(rel).name, body)
        parts = rel.split("/")
        body_parts = body.split("/")
        span = len(body_parts)
        return any(
            all(fnmatch.fnmatch(parts[index + offset], body_parts[offset]) for offset in range(span))
            for index in range(len(parts) - span + 1)
        )
    if fnmatch.fnmatch(rel, pattern) or rel == pattern or rel.startswith(pattern + "/"):
        return True
    return fnmatch.fnmatch(Path(rel).name, pattern)

def ignored(rel: str) -> bool:
    hit = any(matches(rel, pattern) for pattern in patterns)
    if not hit:
        return False
    # An excepted directory re-includes everything under it.
    parts = rel.rstrip("/").split("/")
    prefixes = ["/".join(parts[:depth]) for depth in range(1, len(parts) + 1)]
    return not any(matches(prefix, pattern) for pattern in exceptions for prefix in prefixes)

total = 0
largest: list[tuple[int, str]] = []
for dirpath, dirnames, filenames in os.walk(root):
    rel_dir = os.path.relpath(dirpath, root)
    if rel_dir == ".":
        rel_dir = ""
    kept = []
    for name in dirnames:
        rel = f"{rel_dir}/{name}" if rel_dir else name
        if not ignored(rel) and not ignored(rel + "/"):
            kept.append(name)
    dirnames[:] = kept
    for name in filenames:
        rel = f"{rel_dir}/{name}" if rel_dir else name
        if ignored(rel):
            continue
        path = Path(dirpath) / name
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        largest.append((size, rel))

# A documentation rule like **/docs/ also matches source packages named docs.
tracked = subprocess.run(
    ["git", "-C", str(root), "ls-files", "backend/python/app", "backend/nodejs/apps/src"],
    check=True, capture_output=True, text=True,
).stdout.split()
dropped = [
    rel for rel in tracked
    if rel.endswith((".py", ".ts", ".js"))
    and any(ignored("/".join(rel.split("/")[:depth])) for depth in range(1, rel.count("/") + 2))
]
if dropped:
    print("source files excluded by .dockerignore:", file=sys.stderr)
    for rel in dropped[:20]:
        print(f"  {rel}", file=sys.stderr)
    sys.exit(1)

largest.sort(reverse=True)
mb = total / (1024 * 1024)
print(f"build context: {mb:.1f} MB")
for size, rel in largest[:8]:
    print(f"  {size / (1024 * 1024):.1f} MB  {rel}")
if total > budget:
    print(f"build context {mb:.1f} MB exceeds the 250 MB budget", file=sys.stderr)
    sys.exit(1)
PY
echo "build_context_test: ok"

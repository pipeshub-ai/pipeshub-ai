#!/usr/bin/env bash
# Content hash for the base images. The hash covers Dockerfile.base, the
# Python lockfile and pyproject.toml, and nothing else: an application-code
# change must not move these tags.
set -euo pipefail

ROOT="${BASE_TAG_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
export BASE_TAG_ROOT="$ROOT"

python3 - "$@" <<'PY'
import hashlib
import os
import sys
from pathlib import Path

root = Path(os.environ["BASE_TAG_ROOT"])
files = (
    "Dockerfile.base",
    "backend/python/uv.lock",
    "backend/python/pyproject.toml",
)
digest = hashlib.sha256()
for rel in files:
    data = (root / rel).read_bytes()
    digest.update(rel.encode())
    digest.update(b"\0")
    digest.update(data)
    digest.update(b"\0")
short = digest.hexdigest()[:12]
if "--hash" in sys.argv[1:]:
    print(short)
else:
    print(f"python-deps-{short}")
    print(f"runtime-{short}")
    print(f"models-bge-{short}")
PY

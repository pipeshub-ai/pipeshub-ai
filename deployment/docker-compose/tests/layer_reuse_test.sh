#!/usr/bin/env bash
# Build slim, then full, then change application source and rebuild slim.
# Every layer larger than 20 MB must keep its id. Full is slim plus the
# browser layer and the model layer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

python3 - "$ROOT" "$workdir" <<'PY'
import os, shutil, sys
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
skip = {".git", "node_modules", ".next", "out", "venv", ".venv", ".cursor", ".claude", ".c8_output"}
def ignore(directory, names):
    return [name for name in names if name in skip]
shutil.copytree(src, dst, ignore=ignore, dirs_exist_ok=True)
PY

slim_tag="pipeshub-layer-test:slim"
full_tag="pipeshub-layer-test:full"
slim_again="pipeshub-layer-test:slim-again"

args=()
if [ -n "${PYTHON_DEPS_IMAGE:-}" ]; then
  args+=(--build-arg "PYTHON_DEPS_IMAGE=${PYTHON_DEPS_IMAGE}")
fi
if [ -n "${RUNTIME_BASE_IMAGE:-}" ]; then
  args+=(--build-arg "RUNTIME_BASE_IMAGE=${RUNTIME_BASE_IMAGE}")
fi
if [ -n "${MODELS_IMAGE:-}" ]; then
  args+=(--build-arg "MODELS_IMAGE=${MODELS_IMAGE}")
fi

docker build ${args[@]+"${args[@]}"} -f Dockerfile --target runtime-slim -t "$slim_tag" "$workdir"
docker build ${args[@]+"${args[@]}"} -f Dockerfile --target runtime -t "$full_tag" "$workdir"

printf '\n# layer-reuse\n' >> "$workdir/backend/python/app/__init__.py"
frontend_file="$(find "$workdir/frontend" -name '*.tsx' -print -quit)"
if [ -z "$frontend_file" ]; then
  echo "no frontend tsx file to mutate" >&2
  exit 1
fi
printf '\n/* layer-reuse */\n' >> "$frontend_file"
docker build ${args[@]+"${args[@]}"} -f Dockerfile --target runtime-slim -t "$slim_again" "$workdir"

python3 - "$slim_tag" "$full_tag" "$slim_again" <<'PY'
import json, subprocess, sys

def history(image: str) -> list[tuple[str, int]]:
    raw = subprocess.check_output(
        ["docker", "history", "--no-trunc", "--format", "{{json .}}", image],
        text=True,
    )
    rows = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        rows.append((item.get("ID", ""), parse_size(str(item.get("Size", "0B")))))
    return rows

def parse_size(text: str) -> int:
    text = text.strip()
    units = (("GB", 1000**3), ("MB", 1000**2), ("kB", 1000), ("B", 1))
    for suffix, scale in units:
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * scale)
    return 0

def rootfs(image: str) -> list[str]:
    info = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
    return list(info["RootFS"]["Layers"])

def large_ids(image: str) -> set[str]:
    # History is newest-first and includes empty rows. RootFS omits empty layers.
    sized = [(layer_id, size) for layer_id, size in reversed(history(image)) if size > 0]
    layers = rootfs(image)
    if len(sized) != len(layers):
        raise SystemExit(
            f"{image}: {len(sized)} non-empty history rows vs {len(layers)} rootfs layers"
        )
    return {digest for digest, (_row_id, size) in zip(layers, sized) if size >= 20 * 1000 * 1000}

slim, full, again = (large_ids(name) for name in sys.argv[1:])
missing = slim - again
if missing:
    raise SystemExit(f"slim rebuild dropped {len(missing)} layer(s) larger than 20 MB")
extra = full - slim
if len(extra) != 2:
    raise SystemExit(f"full image should add exactly 2 large layers (browsers and models), added {len(extra)}")
print("layer_reuse_test: ok")
PY

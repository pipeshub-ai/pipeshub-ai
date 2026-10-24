#!/usr/bin/env bash
# Build slim, then full, then change application source and rebuild slim.
# Every layer larger than 20 MB keeps its id, except a layer that holds only the
# changed sources. Full is slim plus the browser layer and the model layer.
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
import json, subprocess, sys, tarfile

LARGE = 20 * 1000 * 1000
# Where the sources mutated above land. A large layer under these may change.
CHANGED_SOURCES = ("app/python/app/", "app/backend/dist/public/")

def large_layers(image: str, list_paths: bool = False) -> dict[str, set[str]]:
    """Diff id -> the file paths in it (when list_paths) for every layer of 20 MB or more.

    Sizes come from `docker save`. `docker history` cannot be lined up with
    RootFS.Layers: on the classic image store a layer that only changes metadata
    (a chown, a symlink) reports 0B there, like an instruction with no layer.
    """
    proc = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
    sizes: dict[str, int] = {}
    paths: dict[str, set[str]] = {}
    small: dict[str, bytes] = {}
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
        for member in tar:
            if not member.isfile():
                continue
            sizes[member.name] = member.size
            if member.size < 4 * 1024 * 1024:
                small[member.name] = tar.extractfile(member).read()
            elif list_paths and member.size >= LARGE:
                with tarfile.open(fileobj=tar.extractfile(member), mode="r|*") as layer:
                    paths[member.name] = {entry.name.lstrip("./") for entry in layer if entry.isfile()}
    if proc.wait() != 0:
        raise SystemExit(f"docker save {image} failed")
    manifest = json.loads(small["manifest.json"])[0]
    diff_ids = json.loads(small[manifest["Config"]])["rootfs"]["diff_ids"]
    if len(diff_ids) != len(manifest["Layers"]):
        raise SystemExit(f"{image}: {len(diff_ids)} diff ids vs {len(manifest['Layers'])} saved layers")
    return {
        diff_id: paths.get(path, set())
        for diff_id, path in zip(diff_ids, manifest["Layers"])
        if sizes[path] >= LARGE
    }

slim, full = large_layers(sys.argv[1]), large_layers(sys.argv[2])
again = large_layers(sys.argv[3], list_paths=True)
for diff_id in again.keys() - slim.keys():
    outside = sorted(p for p in again[diff_id] if not p.startswith(CHANGED_SOURCES))
    if outside:
        raise SystemExit(f"a large layer changed for files the edit did not touch: {outside[:5]}")
missing = len(slim.keys() - again.keys()) - len(again.keys() - slim.keys())
if missing > 0:
    raise SystemExit(f"slim rebuild dropped {missing} layer(s) larger than 20 MB")
extra = full.keys() - slim.keys()
if len(extra) != 2:
    raise SystemExit(f"full image should add exactly 2 large layers (browsers and models), added {len(extra)}")
print("layer_reuse_test: ok")
PY

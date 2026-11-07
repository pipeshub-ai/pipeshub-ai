#!/usr/bin/env bash
# base_tag.sh is deterministic, follows uv.lock, and ignores application code.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/backend/python" "$tmp/backend/python/app"
cp "$ROOT/Dockerfile.base" "$tmp/Dockerfile.base"
cp "$ROOT/backend/python/pyproject.toml" "$tmp/backend/python/pyproject.toml"
if [ -f "$ROOT/backend/python/uv.lock" ]; then
  cp "$ROOT/backend/python/uv.lock" "$tmp/backend/python/uv.lock"
else
  printf 'version = 1\n' > "$tmp/backend/python/uv.lock"
fi
printf 'x = 1\n' > "$tmp/backend/python/app/code.py"

hash1="$(BASE_TAG_ROOT="$tmp" bash "$ROOT/scripts/docker/base_tag.sh" --hash)"
hash2="$(BASE_TAG_ROOT="$tmp" bash "$ROOT/scripts/docker/base_tag.sh" --hash)"
[ "$hash1" = "$hash2" ] || { echo "hash is not stable: $hash1 vs $hash2"; exit 1; }

printf '\n# changed\n' >> "$tmp/backend/python/uv.lock"
hash3="$(BASE_TAG_ROOT="$tmp" bash "$ROOT/scripts/docker/base_tag.sh" --hash)"
[ "$hash1" != "$hash3" ] || { echo "hash did not change when uv.lock changed"; exit 1; }

printf '\n# app\n' >> "$tmp/backend/python/app/code.py"
hash4="$(BASE_TAG_ROOT="$tmp" bash "$ROOT/scripts/docker/base_tag.sh" --hash)"
[ "$hash3" = "$hash4" ] || { echo "hash changed when only application code changed"; exit 1; }

tags="$(BASE_TAG_ROOT="$tmp" bash "$ROOT/scripts/docker/base_tag.sh")"
printf '%s\n' "$tags" | grep -q "^python-deps-${hash4}$"
printf '%s\n' "$tags" | grep -q "^runtime-${hash4}$"
printf '%s\n' "$tags" | grep -q "^models-bge-${hash4}$"
echo "base_tag_test: ok"

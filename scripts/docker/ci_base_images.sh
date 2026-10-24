#!/usr/bin/env bash
# Base images for a CI build of this checkout. Prints PYTHON_DEPS_IMAGE=… and
# RUNTIME_BASE_IMAGE=… lines; append them to $GITHUB_ENV.
#
# The images must match this tree's Dockerfile.base and uv.lock, so they are
# looked up by content hash (scripts/docker/base_tag.sh), GHCR first, then
# Docker Hub. The floating python-deps/runtime tags follow main and are not
# used: a pull request that changes the base would be tested on main's base.
# When the hash is not published yet (the base changed in this pull request),
# the target is built here from Dockerfile.base.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
hash="$(bash "$ROOT/scripts/docker/base_tag.sh" --hash)"

for pair in PYTHON_DEPS_IMAGE=python-deps:python-deps RUNTIME_BASE_IMAGE=runtime:runtime-base; do
  var="${pair%%=*}"
  rest="${pair#*=}"
  name="${rest%%:*}"
  target="${rest#*:}"
  image=""
  for registry in ghcr.io/pipeshub-ai/pipeshub-ai-base docker.io/pipeshubai/pipeshub-ai-base; do
    if docker pull -q "${registry}:${name}-${hash}" >&2; then
      image="${registry}:${name}-${hash}"
      break
    fi
  done
  if [ -z "$image" ]; then
    image="pipeshub-ai-base:${name}-${hash}"
    echo "::notice::${name}-${hash} is not published; building it from Dockerfile.base" >&2
    docker build --target "$target" -f "$ROOT/Dockerfile.base" -t "$image" "$ROOT" >&2
  fi
  echo "${var}=${image}"
done

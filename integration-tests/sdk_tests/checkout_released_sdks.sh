#!/usr/bin/env bash
# Check out the last released tag of each SDK, with the tests that tag contains.
#
# Usage: checkout_released_sdks.sh <release list> <dir> [language ...]
#
# <release list> is .github/sdk-test-matrix.yaml. Each SDK lands in
# <dir>/pipeshub-sdk-<language>, the layout run_sdk_tests.sh expects.
set -euo pipefail

RELEASES=$(realpath "$1")
DEST=$(realpath -m "$2")
shift 2
LANGS=("$@")
[ ${#LANGS[@]} -gt 0 ] || LANGS=(typescript python go)

mkdir -p "$DEST"
for lang in "${LANGS[@]}"; do
  tag=$(awk -F': *' -v lang="$lang" '$1 == lang { print $2 }' "$RELEASES")
  if [ -z "$tag" ]; then
    echo "no release listed for $lang in $RELEASES" >&2
    exit 1
  fi
  rm -rf "$DEST/pipeshub-sdk-$lang"
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" \
    "https://github.com/pipeshub-ai/pipeshub-sdk-$lang.git" "$DEST/pipeshub-sdk-$lang"
done

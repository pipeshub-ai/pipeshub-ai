#!/usr/bin/env bash
# Copy the hand-written tests for one language into an SDK checkout.
#
# Usage: install.sh <typescript|python|go> <sdk dir>
#
# Speakeasy cannot generate tests for streaming operations, so these are written
# by hand. They live here, next to the spec, so a change to an operation and to
# its test land in the same commit.
set -euo pipefail

lang=$1
sdk_dir=$2
src="$(cd "$(dirname "$0")" && pwd)/$lang"

case "$lang" in
  typescript) dest="$sdk_dir/src/__tests__/handwritten" ;;
  python)     dest="$sdk_dir/tests/handwritten" ;;
  go)         dest="$sdk_dir/tests/handwritten" ;;
  *) echo "unknown language: $lang" >&2; exit 2 ;;
esac

rm -rf "$dest"
mkdir -p "$dest"
cp -R "$src/." "$dest/"

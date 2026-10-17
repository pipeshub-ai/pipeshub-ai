#!/usr/bin/env bash
# Generate the SDKs and their tests from this checkout's spec and test case file.
#
# Usage: generate_sdks.sh <open-api checkout> <work dir> [language ...]
#
# Mirrors generate-sdks.yml in pipeshub-ai/open-api so the SDK under test equals
# the SDK that gets published. Publishes nothing. Every language is attempted;
# the exit code is non-zero if any of them failed.
set -euo pipefail

OPEN_API_DIR=$(realpath "$1")
WORK_DIR=$(realpath -m "$2")
shift 2
LANGS=("$@")
[ ${#LANGS[@]} -gt 0 ] || LANGS=(typescript python go)

SDK_TESTS_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(git -C "$SDK_TESTS_DIR" rev-parse --show-toplevel)
API_DOCS="$REPO_ROOT/backend/nodejs/apps/src/modules/api-docs"
SDK_REF=${SDK_REF:-main}

# Speakeasy lints the Go SDK with a staticcheck it installs into GOPATH/bin.
if command -v go >/dev/null; then
  PATH="$PATH:$(go env GOPATH)/bin"
fi

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
tar -C "$OPEN_API_DIR" --exclude=.git -cf - . | tar -C "$WORK_DIR" -xf -

# open-api's trimmed spec is from the last sync of main; a PR needs its own.
cp "$API_DOCS/pipeshub-openapi.yaml" "$WORK_DIR/source_specs/pipeshub-openapi.yaml"
${PYTHON:-python3} "$WORK_DIR/scripts/filter_sdk_spec.py" \
  "$API_DOCS/pipeshub-openapi.yaml" "$WORK_DIR/source_specs/pipeshub-sdk-openapi.yaml"
cp "$API_DOCS/tests.arazzo.yaml" "$WORK_DIR/.speakeasy/tests.arazzo.yaml"

# "|| return 1" on every command: errexit is off inside a function called from an || list.
generate() {
  local lang=$1 sdk_dir="pipeshub-sdk-$1"
  git clone --quiet --depth 1 --branch "$SDK_REF" \
    "https://github.com/pipeshub-ai/pipeshub-sdk-$lang.git" "$sdk_dir" || return 1
  mkdir -p "$sdk_dir/.speakeasy" || return 1
  cp .speakeasy/tests.arazzo.yaml "$sdk_dir/.speakeasy/tests.arazzo.yaml" || return 1

  # shortcut: Speakeasy 1.801.0 stops on an examples/package.json with no "scripts" key, which
  # pipeshub-sdk-typescript has since 9 Oct. Remove this once that repo adds "scripts": {}.
  local examples="$sdk_dir/examples/package.json"
  if [ "$lang" = typescript ] && [ -f "$examples" ] && ! jq -e 'has("scripts")' "$examples" > /dev/null; then
    jq '. + {scripts: {}}' "$examples" > "$examples.tmp" && mv "$examples.tmp" "$examples" || return 1
  fi

  speakeasy run -t "$lang" --skip-upload-spec --skip-versioning --skip-testing -o console || return 1

  # Same patch generate-sdks.yml applies before it opens the SDK pull request.
  if [ "$lang" = typescript ] && [ -d "$sdk_dir/src/__tests__" ]; then
    find "$sdk_dir/src/__tests__" -name '*.ts' -exec sed -i \
      's#clientID: "<YOUR_CLIENT_ID_HERE>",#clientID: process.env["PIPESHUB_CLIENT_ID"] ?? "value",#g' {} + || return 1
  fi

  # The generated Python tests build their client without a timeout, so httpx's default 5 s
  # per read applies, and a case that waits for an LLM answer times out. Fail if the patch
  # stops matching, so the timeout cannot silently go away.
  local test_client="$sdk_dir/tests/test_client.py"
  if [ "$lang" = python ] && [ -f "$test_client" ]; then
    sed -i 's/return httpx.Client(/return httpx.Client(timeout=300,/' "$test_client" || return 1
    grep -q 'httpx.Client(timeout=300,' "$test_client" || return 1
  fi

  "$SDK_TESTS_DIR/handwritten/install.sh" "$lang" "$WORK_DIR/$sdk_dir"
}

cd "$WORK_DIR"
failed=()
for lang in "${LANGS[@]}"; do
  # A failed language keeps no checkout: its old tests from $SDK_REF must not run as the current run.
  generate "$lang" || { failed+=("$lang"); rm -rf "pipeshub-sdk-$lang"; }
done

if [ ${#failed[@]} -gt 0 ]; then
  echo "SDK generation failed for: ${failed[*]}" >&2
  exit 1
fi

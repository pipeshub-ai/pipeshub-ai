#!/usr/bin/env bash
# Generate the SDKs and their tests from this checkout's spec and test case file.
#
# Usage: generate_sdks.sh <open-api checkout> <work dir> [language ...]
#
# Mirrors generate-sdks.yml in pipeshub-ai/open-api so the SDK under test equals
# the SDK that gets published. Publishes nothing.
set -euo pipefail

OPEN_API_DIR=$(realpath "$1")
WORK_DIR=$(realpath -m "$2")
shift 2
LANGS=("$@")
[ ${#LANGS[@]} -gt 0 ] || LANGS=(typescript python go)

REPO_ROOT=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
API_DOCS="$REPO_ROOT/backend/nodejs/apps/src/modules/api-docs"
SDK_REF=${SDK_REF:-main}

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
tar -C "$OPEN_API_DIR" --exclude=.git -cf - . | tar -C "$WORK_DIR" -xf -

# open-api's trimmed spec is from the last sync of main; a PR needs its own.
cp "$API_DOCS/pipeshub-openapi.yaml" "$WORK_DIR/source_specs/pipeshub-openapi.yaml"
${PYTHON:-python3} "$WORK_DIR/scripts/filter_sdk_spec.py" \
  "$API_DOCS/pipeshub-openapi.yaml" "$WORK_DIR/source_specs/pipeshub-sdk-openapi.yaml"
cp "$API_DOCS/tests.arazzo.yaml" "$WORK_DIR/.speakeasy/tests.arazzo.yaml"

cd "$WORK_DIR"
for lang in "${LANGS[@]}"; do
  sdk_dir="pipeshub-sdk-$lang"
  git clone --quiet --depth 1 --branch "$SDK_REF" \
    "https://github.com/pipeshub-ai/pipeshub-sdk-$lang.git" "$sdk_dir"
  mkdir -p "$sdk_dir/.speakeasy"
  cp .speakeasy/tests.arazzo.yaml "$sdk_dir/.speakeasy/tests.arazzo.yaml"

  speakeasy run -t "$lang" --skip-upload-spec --skip-versioning --skip-testing -o console

  # Same patch generate-sdks.yml applies before it opens the SDK pull request.
  if [ "$lang" = typescript ] && [ -d "$sdk_dir/src/__tests__" ]; then
    find "$sdk_dir/src/__tests__" -name '*.ts' -exec sed -i \
      's#clientID: "<YOUR_CLIENT_ID_HERE>",#clientID: process.env["PIPESHUB_CLIENT_ID"] ?? "value",#g' {} +
  fi
done

#!/bin/sh
# Builds a test variant of an engine image with the skill-pack runtime (SK1). Not a release image.
#
#   deployment/sandbox-images/skill-runtime/build.sh opencode|codex [--tag TAG]
#
# Bases come from backend/python/harness_engines/allowlist.json: opencode's `variants.tools` image and
# Codex's `image`, which must already be built (their own build.sh verifies the release assets). The Codex
# image's binary digest label must equal the allowlist's binaryDigest before anything is built, and the
# Dockerfile checks the copied binary once more. Default tags: pxh-sk1-opencode:test, pxh-sk1-codex:test.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../../.." && pwd)
ALLOWLIST="$REPO/backend/python/harness_engines/allowlist.json"
usage() { echo "usage: $0 opencode|codex [--tag TAG]" >&2; exit 2; }
[ $# -ge 1 ] || usage
TARGET="$1"
shift
case "$TARGET" in opencode|codex) ;; *) usage ;; esac
TAG="pxh-sk1-$TARGET:test"
while [ $# -gt 0 ]; do
    case "$1" in
        --tag) [ $# -ge 2 ] || usage; TAG="$2"; shift 2 ;;
        *) usage ;;
    esac
done

field() {
    python3 - "$ALLOWLIST" "$1" "$2" <<'EOF'
import json, sys
entry = next(e for e in json.load(open(sys.argv[1]))["engines"] if e["kind"] == sys.argv[2])
value = entry
for key in sys.argv[3].split("."):
    value = value[key]
print(value)
EOF
}

TOOLS_IMAGE=$(field opencode variants.tools)
CODEX_IMAGE=$(field codex image)
CODEX_VERSION=$(field codex version)
CODEX_DIGEST=$(field codex binaryDigest)
LABELLED=$(docker image inspect "$CODEX_IMAGE" --format '{{ index .Config.Labels "io.pipeshub.engine.binary-digest" }}') \
    || { echo "missing $CODEX_IMAGE: build deployment/sandbox-images/codex/build.sh first" >&2; exit 1; }
[ "$LABELLED" = "$CODEX_DIGEST" ] || { echo "$CODEX_IMAGE carries $LABELLED, the allowlist pins $CODEX_DIGEST" >&2; exit 1; }
docker image inspect "$TOOLS_IMAGE" >/dev/null \
    || { echo "missing $TOOLS_IMAGE: build deployment/sandbox-images/opencode/build.sh --variant tools first" >&2; exit 1; }

CONTEXT=$(mktemp -d)
trap 'rm -rf "$CONTEXT"' EXIT INT TERM
cp "$HERE/Dockerfile" "$HERE/skills.lock" "$CONTEXT/"
cp -R "$HERE/licenses" "$CONTEXT/licenses"
cp "$REPO/deployment/sandbox-images/opencode/tools/requirements.lock" \
   "$REPO/deployment/sandbox-images/opencode/tools/runtime_note.py" "$CONTEXT/"
cp "$REPO/deployment/sandbox-images/opencode/tools/node/package-lock.json" "$CONTEXT/node-package-lock.json"

docker build --target "$TARGET" \
    --build-arg "TOOLS_IMAGE=$TOOLS_IMAGE" \
    --build-arg "CODEX_IMAGE=$CODEX_IMAGE" \
    --build-arg "CODEX_VERSION=$CODEX_VERSION" \
    --build-arg "CODEX_SHA256=${CODEX_DIGEST#sha256:}" \
    --label pipeshub.harness.owner=x-lead \
    -t "$TAG" "$CONTEXT"

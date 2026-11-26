#!/bin/sh
# Licence and marker scan of what the tools image adds to the engine image (E0b IT-13).
#
#   deployment/sandbox-images/opencode/tools/scan.sh [OUT_DIR [KIND]]     # KIND: opencode (default), codex, acp-gemini, acp-goose, acp-qwen
#
# The runtime layer is one for every Alpine run image (codex/tools and acp-gemini/tools are this folder), so each
# engine's run image is scanned against its engine-only image (the allowlist's `variants`) the same way: CPython,
# the site-packages, the Node packages, the runtime note, the tools notices and the files of every Alpine package
# the tools target adds (never the engine itself: its binary or bundle and the relay are the engine's own scan)
# go into one tarball, and scripts/engines/licence_scan.py runs on it under KIND's approvals with
# licenses/components.json as the manifest. Claude Code's Debian image has its own scan.sh. Exit code is the
# scanner's: 0 pass or pass_with_notices, 1 fail.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../../../.." && pwd)
OUT=${1:-.}
KIND=${2:-opencode}
read_tags() {
    python3 - "$REPO/backend/python/harness_engines/allowlist.json" "$KIND" <<'EOF'
import json, sys
entry = next((e for e in json.load(open(sys.argv[1]))["engines"] if e["kind"] == sys.argv[2]), None)
if entry is None or "engine" not in entry.get("variants", {}):
    sys.exit(f"{sys.argv[2]}: no allowlist entry with an engine-only variant to scan its tools layer against")
print(entry["variants"]["tools"], entry["variants"]["engine"], entry["version"])
EOF
}
set -- $(read_tags)
TOOLS=$1 ENGINE=$2 VERSION=$3
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

docker run --rm --network none --label pipeshub.harness.owner=x-lead "$ENGINE" apk info -q --no-network 2>/dev/null | sort > "$WORK/engine.apk"
docker run --rm --network none --label pipeshub.harness.owner=x-lead "$TOOLS" apk info -q --no-network 2>/dev/null | sort > "$WORK/tools.apk"
ADDED=$(comm -13 "$WORK/engine.apk" "$WORK/tools.apk" | grep -v '^\.' | tr '\n' ' ')
docker run --rm --network none --label pipeshub.harness.owner=x-lead -e "ADDED=$ADDED" "$TOOLS" /bin/sh -c '
    for p in $ADDED; do apk info -qL --no-network "$p" 2>/dev/null; done | sed "/^$/d" > /tmp/apk-files
    cd / && tar -cf - --exclude=usr/local/bin/opencode --exclude=usr/local/bin/codex-app-server \
        --exclude=usr/local/lib/gemini-cli --exclude=usr/local/bin/gemini --exclude=usr/local/bin/goose \
        --exclude=usr/local/lib/qwen-code \
        --exclude=usr/local/bin/pxh-relay \
        usr/local usr/share/pxh-engine usr/share/licenses/pxh-*-tools -T /tmp/apk-files
' > "$WORK/layer.tar"
mkdir -p "$OUT"
python3 "$REPO/scripts/engines/licence_scan.py" "$KIND" "$VERSION-tools" \
    --asset "$WORK/layer.tar" --sha256 "$(sha256sum "$WORK/layer.tar" | cut -d' ' -f1)" \
    --manifest "$HERE/licenses/components.json" --out "$OUT"

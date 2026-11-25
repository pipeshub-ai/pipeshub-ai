#!/bin/sh
# Licence and marker scan of PipesHub's layers of a Debian engine image (E9 IM-3cc, C2.43): never the engine itself.
#
#   deployment/sandbox-images/debian-layer-scan.sh KIND [OUT_DIR]     # KIND: claude_code, acp-dsh
#
# What PipesHub adds to the Debian base (the packages its apt layer and, in the run image, the CPython base add, the
# relay, the notices, CPython and the tools lock's wheels and npm packages) is exported into one tarball, with a
# manifest of those packages and the licence each one's Debian copyright file declares first, and
# scripts/engines/licence_scan.py runs on it. The run image is compared with the engine stage's Debian base, not with
# the engine-only image: the tools target starts from the CPython image, so every package that base brings is
# PipesHub's to declare. The engine's own files are its own scan (Claude Code is the admin's and is not scanned, D29;
# dsh's tree is its lock's, tallied in acp-dsh/licenses/NOTICE.md). The Alpine images' scan is opencode/tools/scan.sh.
# Exit code is the scanner's.
set -eu

KIND=${1:?usage: debian-layer-scan.sh KIND [OUT_DIR]}
OUT=${2:-.}
# DIR: the image folder; NOTICES: its notices under /usr/share/licenses; EXCLUDE: the engine's own files.
case "$KIND" in
    claude_code) DIR=claude-code NOTICES=pxh-claude-code EXCLUDE=usr/local/bin/claude ;;
    acp-dsh) DIR=acp-dsh NOTICES=pxh-dsh EXCLUDE=usr/local/lib/dsh ;;
    *) echo "debian-layer-scan.sh: $KIND is not a Debian engine image" >&2; exit 2 ;;
esac
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../.." && pwd)
IMAGE_DIR="$HERE/$DIR"
set -- $(python3 - "$REPO/backend/python/harness_engines/allowlist.json" "$KIND" <<'EOF'
import json, sys
entry = next(e for e in json.load(open(sys.argv[1]))["engines"] if e["kind"] == sys.argv[2])
print(entry["image"], entry["version"])
EOF
)
IMAGE=$1 VERSION=$2
BASE=$(sed -n 's/^ARG DEBIAN=//p' "$IMAGE_DIR/Dockerfile")
NODE=$(sed -n 's/^ARG NODE_DEBIAN=node:\([0-9.]*\)-.*/\1/p' "$IMAGE_DIR/Dockerfile")
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM
run() { docker run --rm --network none --label pipeshub.harness.owner=x-lead --entrypoint /bin/sh "$@"; }

run "$BASE" -c "dpkg-query -W -f '\${Package}\n'" | sort > "$WORK/base.pkgs"
run "$IMAGE" -c "dpkg-query -W -f '\${Package}\t\${Version}\n'" | sort > "$WORK/image.pkgs"
cut -f1 "$WORK/image.pkgs" | comm -13 "$WORK/base.pkgs" - > "$WORK/added"
ADDED=$(tr '\n' ' ' < "$WORK/added")
run -e "ADDED=$ADDED" "$IMAGE" -c '
    for p in $ADDED; do
        printf "%s\t%s\n" "$p" "$(sed -n "s/^License: *//p" /usr/share/doc/$p/copyright 2>/dev/null | head -n 1)"
    done' > "$WORK/licences"
run -e "ADDED=$ADDED" -e "NOTICES=$NOTICES" -e "EXCLUDE=$EXCLUDE" "$IMAGE" -c '
    for p in $ADDED; do dpkg -L "$p"; done | while read -r f; do [ -f "$f" ] && echo "${f#/}"; done > /tmp/files
    extra=""
    [ ! -d /usr/share/pxh-engine ] || extra="usr/local/lib usr/share/pxh-engine usr/share/licenses/$NOTICES-tools"
    cd / && tar -cf - --exclude="$EXCLUDE" -T /tmp/files usr/local/bin "usr/share/licenses/$NOTICES" $extra
' > "$WORK/layer.tar"
python3 "$REPO/scripts/engines/debian_manifest.py" "$WORK/image.pkgs" "$WORK/licences" "$WORK/manifest.json" \
    "$IMAGE_DIR/tools/licenses/components.json" "$NODE"
mkdir -p "$OUT"
python3 "$REPO/scripts/engines/licence_scan.py" "$KIND" "$VERSION-pipeshub-layers" \
    --asset "$WORK/layer.tar" --sha256 "$(sha256sum "$WORK/layer.tar" | cut -d' ' -f1)" \
    --manifest "$WORK/manifest.json" --out "$OUT"

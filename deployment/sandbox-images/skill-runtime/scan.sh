#!/bin/sh
# Licence and marker scan of what the SK1 skill-runtime layer adds to the opencode tools image.
#
#   deployment/sandbox-images/skill-runtime/scan.sh [OUT_DIR]
#
# Exports the added packages' site-packages folders and this layer's notices from pxh-sk1-opencode:test into
# one tarball and runs scripts/engines/licence_scan.py on it with licenses/components.json as the manifest.
# Exit code is the scanner's: 0 pass or pass_with_notices, 1 fail.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/../../.." && pwd)
OUT=${1:-.}
IMAGE=${SK1_OPENCODE_IMAGE:-pxh-sk1-opencode:test}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT INT TERM

docker run --rm --network none --label pipeshub.harness.owner=x-lead --entrypoint /bin/sh "$IMAGE" -c '
    cd / && tar -cf - usr/share/licenses/pxh-skill-runtime \
        $(cd / && ls -d usr/local/lib/python3.12/site-packages/pypdf* usr/local/lib/python3.12/site-packages/reportlab* \
            usr/local/lib/python3.12/site-packages/defusedxml* usr/local/lib/python3.12/site-packages/charset_normalizer*)
' > "$WORK/layer.tar"
mkdir -p "$OUT"
python3 "$REPO/scripts/engines/licence_scan.py" opencode "1.18.34-skill-runtime" \
    --asset "$WORK/layer.tar" --sha256 "$(sha256sum "$WORK/layer.tar" | cut -d' ' -f1)" \
    --manifest "$HERE/licenses/components.json" --out "$OUT"

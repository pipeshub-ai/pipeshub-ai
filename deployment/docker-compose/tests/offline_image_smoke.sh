#!/usr/bin/env bash
# ==============================================================================
# Offline smoke: an app image with PIPESHUB_OFFLINE=1 and no network at all.
# ==============================================================================
# Starts the image with `--network none` and runs offline_image_smoke.py in it:
# the switches are set, the tokenizer and Docling models load from the image, the
# embedding service starts, and a sample PDF parses. Nothing is pulled or pushed.
#
#   bash deployment/docker-compose/tests/offline_image_smoke.sh <image> slim|full
#
# Needs Docker. The image must already exist locally.
# ==============================================================================
set -euo pipefail

IMAGE="${1:?usage: offline_image_smoke.sh <image> slim|full}"
VARIANT="${2:?usage: offline_image_smoke.sh <image> slim|full}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

docker image inspect "$IMAGE" >/dev/null

# The entrypoint is the image's own: it starts as root, applies the switches and drops to the
# runtime user, so this exercises the real start path.
docker run --rm --network none \
  -e PIPESHUB_OFFLINE=1 \
  -v "$HERE/offline_image_smoke.py:/smoke/offline_image_smoke.py:ro" \
  -w /app/python \
  "$IMAGE" python /smoke/offline_image_smoke.py "$VARIANT"

# The Node API reads the same switch itself: bypass the entrypoint (which would set
# TELEMETRY_COLLECTION_FLAG for it) so this tests Node's own handling.
docker run --rm --network none -e PIPESHUB_OFFLINE=1 -w /app/backend --entrypoint node "$IMAGE" \
  -e "const f=require('/app/backend/dist/libs/services/telemetry/flag.js'); if (f.isTelemetryEnabled()) { console.error('Node telemetry is on'); process.exit(1) } console.log('ok: Node telemetry is off')"

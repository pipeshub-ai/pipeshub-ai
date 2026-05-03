#!/usr/bin/env bash
# Desktop build for macOS (DMG in dist-electron/). Run from repo root:
#   ./scripts/electron/build-electron-mac.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

npm run build
node scripts/electron/electron-prepare.mjs
npx electron-builder --mac --config electron-builder.yml --publish never

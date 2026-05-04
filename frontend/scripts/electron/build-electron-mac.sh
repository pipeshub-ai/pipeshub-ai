#!/usr/bin/env bash
# macOS DMG build (wrapper). Run from frontend/:
#   npm run electron:build:mac
#   bash scripts/electron/build-electron-mac.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
exec node scripts/electron/build-electron.mjs mac

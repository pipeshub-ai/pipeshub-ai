#!/bin/sh
# Licence and marker scan of PipesHub's layers of the DeepSeek Harness image (C2.43): never dsh's own tree, whose
# licences are its lock's (licenses/NOTICE.md). The steps are debian-layer-scan.sh's.
#
#   deployment/sandbox-images/acp-dsh/scan.sh [OUT_DIR]
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$HERE/../debian-layer-scan.sh" acp-dsh "$@"

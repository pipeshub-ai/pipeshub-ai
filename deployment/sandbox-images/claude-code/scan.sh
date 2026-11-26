#!/bin/sh
# Licence and marker scan of PipesHub's layers of the Claude Code image (E9 IM-3cc): never the Claude Code binary,
# which is the admin's, under Anthropic's terms, and not redistributed by PipesHub (D29). The steps are
# debian-layer-scan.sh's.
#
#   deployment/sandbox-images/claude-code/scan.sh [OUT_DIR]
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$HERE/../debian-layer-scan.sh" claude_code "$@"

#!/bin/sh
# Builds the opencode engine image pinned in backend/python/harness_engines/allowlist.json.
#
#   deployment/sandbox-images/opencode/build.sh [--variant engine|tools] [--asset PATH] [--tag TAG]
#   deployment/sandbox-images/opencode/build.sh --candidate VERSION --digest sha256:HEX [--variant engine|tools] --asset PATH
#
# The steps (asset and binary verified against the allowlist before docker build) are engine-build.sh's.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$HERE/../engine-build.sh" opencode "$@"

#!/bin/sh
# Builds the Gemini CLI (ACP) engine image pinned in backend/python/harness_engines/allowlist.json.
#
#   deployment/sandbox-images/acp-gemini/build.sh [--asset PATH] [--tag TAG]
#
# The steps (the release bundle and its entry point verified against the allowlist before docker build) are
# engine-build.sh's.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$HERE/../engine-build.sh" acp-gemini "$@"

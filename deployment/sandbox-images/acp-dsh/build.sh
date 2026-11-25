#!/bin/sh
# Builds the DeepSeek Harness (ACP) engine image pinned in backend/python/harness_engines/allowlist.json.
#
#   deployment/sandbox-images/acp-dsh/build.sh [--asset PATH] [--tag TAG] [--variant engine|tools]
#
# The steps (the registry tarball and its bin.js verified against the allowlist, and the dependency lock against
# its lockSha256, before docker build) are engine-build.sh's. Unsigned: its profile runs only on a dev or test
# install with the engine isolation dev override until the release signs acp_agents.json (OB-1).
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$HERE/../engine-build.sh" acp-dsh "$@"

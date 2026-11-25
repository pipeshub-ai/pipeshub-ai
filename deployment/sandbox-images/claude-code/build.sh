#!/bin/sh
# Builds the Claude Code engine image from the admin's own copy of the binary.
#
#   deployment/sandbox-images/claude-code/build.sh --binary PATH [--tag TAG]
#   deployment/sandbox-images/claude-code/build.sh --binary PATH --candidate VERSION --digest sha256:HEX
#
# PATH is the `claude` executable the admin installed from Anthropic (for example
# ~/.local/share/claude/versions/<version>). It must hash to the binaryDigest the allowlist pins for the
# tested version, or the build stops before docker runs. Nothing is downloaded and the image is not pushed
# anywhere by this script: it stays where the admin builds it (D29). The steps are engine-build.sh's.
# With --candidate and --digest (CC1, PipesHub's admission of a new version), the binary is verified against that
# digest instead and the image is tagged VERSION-candidate, which no deployment runs.
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$HERE/../engine-build.sh" claude_code "$@"

#!/usr/bin/env bash
# query_rss.sh — total RSS in MB of the query service (parent + workers).
#
# Prints a single number so it can be sampled in a loop. `docker stats` is not
# usable here: it reports the whole container, which also holds the Node,
# indexing and connector processes.
set -uo pipefail
# shellcheck source=_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
require_target || exit 1

query_rss_mb

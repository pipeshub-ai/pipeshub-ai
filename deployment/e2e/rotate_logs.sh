#!/usr/bin/env bash
# Keeps each <name>.log in the e2e stack's log directory under a size bound: a person's stack lives for days, and on
# 2026-10-16 node.log alone filled the host disk. A file over the bound is copied to <name>.log.1 (older copies shift
# to .2 ... .KEEP) and truncated in place, so the processes keep writing to the file they opened (with >>) and
# `tail -F <name>.log` keeps following it.
#
#   rotate_logs.sh DIR          check every E2E_LOG_CHECK_SECONDS (30) until killed
#   rotate_logs.sh DIR --once   check once (tests)
#
# E2E_LOG_MAX_MB (200) is the bound per file, E2E_LOG_KEEP (3) the rotated copies kept per file.
# shortcut: lines written between the copy and the truncate are lost; acceptable for debugging logs, use a
# reopening writer if a log ever becomes evidence that must be complete.
set -uo pipefail

dir=${1:?usage: rotate_logs.sh DIR [--once]}
max=$(( ${E2E_LOG_MAX_MB:-200} * 1024 * 1024 ))
keep=${E2E_LOG_KEEP:-3}

rotate_once() {
  local f i
  for f in "$dir"/*.log; do
    [[ -f $f ]] || continue
    (( $(stat -c %s "$f" 2>/dev/null || echo 0) > max )) || continue
    if (( keep > 0 )); then
      for (( i = keep - 1; i >= 1; i-- )); do
        [[ -f $f.$i ]] && mv -f "$f.$i" "$f.$((i + 1))"
      done
      # Truncated even when the copy fails: on a full disk, freeing the space matters more than this log.
      cp "$f" "$f.1"
    fi
    : >"$f"
  done
}

if [[ "${2:-}" == "--once" ]]; then
  rotate_once
  exit 0
fi
while true; do
  rotate_once
  sleep "${E2E_LOG_CHECK_SECONDS:-30}"
done

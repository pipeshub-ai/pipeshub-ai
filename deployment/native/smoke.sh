#!/usr/bin/env bash
# Starts a built bundle for real into a fresh data folder: PostgreSQL, the schema steps, every service, the API's health.
# Then status, health and stop, and checks nothing it started is left running.
#   smoke.sh <bundle-dir>
set -euo pipefail
b=$(cd "$1" && pwd); d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
run() { "$b/pipeshub-native" "$@" --data-dir "$d" --json; }
if ! run start --detach; then tail -n 50 "$d"/logs/*.log >&2 || true; exit 1; fi
run status
run health || { tail -n 50 "$d"/logs/*.log >&2; exit 1; }
run stop
left=$(ps -eo pid=,args= | grep -F "$d" | grep -v grep || true)
[ -z "$left" ] || { echo "left running after stop:"; echo "$left"; exit 1; }
echo "smoke: ok"

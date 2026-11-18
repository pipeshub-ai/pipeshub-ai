#!/usr/bin/env bash
# Starts a built bundle for real into a fresh data folder: PostgreSQL, the schema steps, every service, the API's health.
# Checks that each of the launcher's ports is served on 127.0.0.1 and that nothing new listens beyond loopback, then status,
# health and stop, a warm start and stop, and that nothing it started is left running.
#   smoke.sh <bundle-dir>
set -euo pipefail
b=$(cd "$1" && pwd); d=$(mktemp -d)
run() { "$b/pipeshub-native" "$@" --data-dir "$d" --json; }
# Whatever happens, stop what this run started before deleting its data folder.
trap 'run stop >/dev/null 2>&1 || true; rm -rf "$d"' EXIT

# Every TCP listener on this machine as address:port. By address, not by owning process: the services make themselves
# non-dumpable, which hides their sockets in /proc/<pid>/fd.
listeners() {
  if [ "$(uname -s)" = Linux ]; then
    "$b/py/bin/python3" -I - <<'PY'
import socket
for table, family in (("/proc/net/tcp", socket.AF_INET), ("/proc/net/tcp6", socket.AF_INET6)):
    try:
        lines = open(table).read().splitlines()[1:]
    except OSError:
        continue
    for line in lines:
        f = line.split()
        if f[3] != "0A":
            continue
        addr, port = f[1].split(":")
        raw = bytes.fromhex(addr)
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
        print(f"{socket.inet_ntop(family, raw)}:{int(port, 16)}")
PY
  else
    netstat -an -p tcp | awk '$6 == "LISTEN" { a = $4; sub(/\.[0-9*]+$/, "", a); p = $4; sub(/.*\./, "", p); print a ":" p }'
  fi
}
before=$(listeners | sort -u)

if ! run start --detach; then tail -n 50 "$d"/logs/*.log >&2 || true; exit 1; fi
run status
run health || { tail -n 50 "$d"/logs/*.log >&2; exit 1; }
ports=$("$b/node/bin/node" -e 'const s = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")); console.log(Object.values(s.ports).join(" "))' "$d/launcher.json")

# The Python services come up after the API answers: wait until every port of the launcher is served on 127.0.0.1.
missing=$ports
for _ in $(seq 1 90); do
  now=$(listeners | sort -u)
  missing=$(for p in $ports; do echo "$now" | grep -qx "127.0.0.1:$p" || echo "$p"; done)
  [ -z "$missing" ] && break
  sleep 2
done
[ -z "$missing" ] || { echo "smoke: not served on 127.0.0.1: $(echo $missing)"; tail -n 30 "$d"/logs/*.log >&2; exit 1; }
wide=$(comm -13 <(echo "$before") <(listeners | sort -u) | grep -v '^127\.0\.0\.1:' || true)
[ -z "$wide" ] || { echo "smoke: new listeners beyond loopback: $(echo $wide)"; exit 1; }
echo "smoke: every port on 127.0.0.1 only: $(echo $ports)"

run stop
# A warm start on the same folder: every login now exists, and the createdb step sets its password from secrets.json again.
if ! run start --detach; then tail -n 50 "$d"/logs/*.log >&2 || true; exit 1; fi
run health || { tail -n 50 "$d"/logs/*.log >&2; exit 1; }
run stop
left=$(ps -eo pid=,args= | grep -F "$d" | grep -v grep || true)
[ -z "$left" ] || { echo "left running after stop:"; echo "$left"; exit 1; }
echo "smoke: ok"

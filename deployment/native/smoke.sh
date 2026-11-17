#!/usr/bin/env bash
# Starts a built bundle for real into a fresh data folder: PostgreSQL, the schema steps, every service, the API's health.
# Checks that every listening socket of the bundle's processes is on 127.0.0.1, then status, health and stop, and that
# nothing it started is left running.
#   smoke.sh <bundle-dir>
set -euo pipefail
b=$(cd "$1" && pwd); d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
run() { "$b/pipeshub-native" "$@" --data-dir "$d" --json; }
if ! run start --detach; then tail -n 50 "$d"/logs/*.log >&2 || true; exit 1; fi
run status
run health || { tail -n 50 "$d"/logs/*.log >&2; exit 1; }

# The TCP listeners of every process started from this bundle or on this data folder, as address:port.
listeners() {
  if [ "$(uname -s)" = Linux ]; then
    "$b/py/bin/python3" -I - "$b" "$d" <<'PY'
import os, sys, socket, struct
roots = sys.argv[1:]
inodes = set()
for pid in filter(str.isdigit, os.listdir("/proc")):
    try:
        cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace")
        if not any(r in cmd for r in roots):
            continue
        for fd in os.listdir(f"/proc/{pid}/fd"):
            link = os.readlink(f"/proc/{pid}/fd/{fd}")
            if link.startswith("socket:["):
                inodes.add(link[8:-1])
    except OSError:
        continue
for table, family in (("/proc/net/tcp", socket.AF_INET), ("/proc/net/tcp6", socket.AF_INET6)):
    for line in open(table).read().splitlines()[1:]:
        f = line.split()
        if f[3] != "0A" or f[9] not in inodes:
            continue
        addr, port = f[1].split(":")
        raw = bytes.fromhex(addr)
        raw = b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4))
        print(f"{socket.inet_ntop(family, raw)}:{int(port, 16)}")
PY
  else
    pids=$(ps -axo pid=,args= | grep -F -e "$b" -e "$d" | grep -v grep | awk '{print $1}' | paste -sd, -)
    [ -n "$pids" ] && lsof -nP -a -p "$pids" -iTCP -sTCP:LISTEN -Fn 2>/dev/null | sed -n 's/^n//p'
  fi
}
# The Python services come up after the API answers: wait for all seven listeners (API, PostgreSQL, five services).
for _ in $(seq 1 90); do [ "$(listeners | sort -u | wc -l)" -ge 7 ] && break; sleep 2; done
found=$(listeners | sort -u)
echo "listening: $(echo $found)"
[ "$(echo "$found" | wc -l)" -ge 7 ] || { echo "smoke: expected 7 listeners"; tail -n 30 "$d"/logs/*.log >&2; exit 1; }
wide=$(echo "$found" | grep -v '^127\.0\.0\.1:' || true)
[ -z "$wide" ] || { echo "smoke: listening beyond loopback: $wide"; exit 1; }

run stop
left=$(ps -eo pid=,args= | grep -F "$d" | grep -v grep || true)
[ -z "$left" ] || { echo "left running after stop:"; echo "$left"; exit 1; }
echo "smoke: ok"

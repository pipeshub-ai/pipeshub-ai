#!/usr/bin/env bash
# The whole native bundle: PostgreSQL, Python, Node, the app and the launcher, plus a `pipeshub-native` entry script.
#   bundle.sh <bundle-dir> [--archive <file.tar.gz>]
# On Linux, CI runs build-pg.sh and build-py.sh inside manylinux_2_28 (glibc 2.28) so the bundle runs on older systems;
# each step can also be run on its own. On macOS every Mach-O file is signed ad hoc (codesign -s -): Apple silicon refuses
# to run unsigned code. A Developer ID signature and notarisation are a separate release step.
. "$(dirname "$0")/lib.sh"
out=$(abspath "$1"); archive=${3:-}
[ -x "$out/pg/bin/postgres" ] || "$here/build-pg.sh" "$out/pg"
[ -x "$out/py/bin/python3" ] || "$here/build-py.sh" "$out"
[ -x "$out/node/bin/node" ] || "$here/build-node.sh" "$out"
rm -rf "$out/launcher"; mkdir -p "$out/launcher"
cp -R "$here/launcher/src" "$here/launcher/package.json" "$here/README.md" "$out/launcher/"
cat > "$out/pipeshub-native" <<'SH'
#!/bin/sh
# start | stop | status | health  (see launcher/README.md)
here=$(cd "$(dirname "$0")" && pwd)
exec "$here/node/bin/node" "$here/launcher/src/main.ts" "$@"
SH
chmod 0755 "$out/pipeshub-native"
[ "$os" = darwin ] && adhoc_sign "$out"
du -sh "$out"
if [ -n "$archive" ]; then tar -C "$(dirname "$out")" -czf "$archive" "$(basename "$out")"; ls -la "$archive"; fi

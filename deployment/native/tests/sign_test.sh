#!/usr/bin/env bash
# adhoc_sign (lib.sh): signs every Mach-O file under a folder, skips everything else, and fails only when signing fails.
# `file` and `codesign` are stubbed, so this runs anywhere.   bash deployment/native/tests/sign_test.sh
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
t=$(mktemp -d); trap 'rm -rf "$t"' EXIT
mkdir -p "$t/bin" "$t/b/lib"
cat > "$t/bin/file" <<'SH'
#!/bin/sh
case "$2" in *.dylib|*.node|*/postgres) echo "Mach-O 64-bit arm64";; *) echo "POSIX shell script, ASCII text";; esac
SH
cat > "$t/bin/codesign" <<'SH'
#!/bin/sh
for a; do f=$a; done; echo "$f" >> "$SIGNED"; [ -f "$f.fail" ] && exit 1; exit 0
SH
chmod +x "$t/bin/file" "$t/bin/codesign"
: > "$t/b/lib/libssl.dylib"; : > "$t/b/lib/sharp.node"; printf '#!/bin/sh\n' > "$t/b/postgres"; chmod +x "$t/b/postgres"
printf '#!/bin/sh\n' > "$t/b/zz-entry-script"; chmod +x "$t/b/zz-entry-script"   # a script found last must not fail the run
export SIGNED="$t/signed" PATH="$t/bin:$PATH"
fail=0
( . "$here/lib.sh"; adhoc_sign "$t/b" ) || { echo "FAIL: adhoc_sign failed although every Mach-O file was signed"; fail=1; }
[ "$(sort "$SIGNED" | sed "s|$t/b/||" | tr '\n' ' ')" = "lib/libssl.dylib lib/sharp.node postgres " ] || { echo "FAIL: signed $(cat "$SIGNED" | tr '\n' ' ')"; fail=1; }
: > "$t/b/lib/sharp.node.fail"; : > "$SIGNED"
if ( . "$here/lib.sh"; adhoc_sign "$t/b" ) 2>/dev/null; then echo "FAIL: a codesign failure did not fail adhoc_sign"; fail=1; fi
[ $fail = 0 ] && echo "sign_test: ok"
exit $fail

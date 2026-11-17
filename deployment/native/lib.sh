# Shared by the bundle scripts (bash 3.2 safe: macOS).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
. "$here/versions.env"
abspath() { mkdir -p "$1" && (cd "$1" && pwd); }
sha256_of() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
# fetch <url> <sha256> <file>: download once into $SRC_CACHE (default: a temp folder) and refuse a file whose checksum differs.
fetch() {
  local cache=${SRC_CACHE:-${TMPDIR:-/tmp}/pipeshub-native-src}; mkdir -p "$cache"
  local f="$cache/$3"
  if [ ! -f "$f" ]; then curl -fsSL --retry 3 -o "$f.part" "$1"; mv "$f.part" "$f"; fi
  local got; got=$(sha256_of "$f")
  [ "$got" = "$2" ] || { echo "checksum mismatch for $3: got $got, want $2" >&2; rm -f "$f"; exit 1; }
  echo "$f"
}
os=$(uname -s | tr '[:upper:]' '[:lower:]'); arch=$(uname -m); jobs=$( (nproc || sysctl -n hw.ncpu) 2>/dev/null )

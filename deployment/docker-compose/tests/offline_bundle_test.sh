#!/usr/bin/env bash
# ==============================================================================
# R41SUPPLY-FU-7 (ADR-SEC-06 D2): install.sh --offline-bundle checks the
# release's signature on the bundle's manifest offline, then each part's
# checksum, and only then runs docker load. Stub docker and cosign; no network.
#
# Run: bash deployment/docker-compose/tests/offline_bundle_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/../install.sh"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PASS=0; FAIL=0
pass() { printf "  ok   - %s\n" "$1"; PASS=$((PASS + 1)); }
fail() { printf "  FAIL - %s\n" "$1"; FAIL=$((FAIL + 1)); }
check() { if [[ "$2" == *"$3"* ]]; then pass "$1"; else fail "$1"; printf "         expected: %s\n         got: %s\n" "$3" "$2"; fi; }
lacks() { if [[ "$2" != *"$3"* ]]; then pass "$1"; else fail "$1"; printf "         unexpected: %s\n" "$3"; fi; }

extract_fn() { awk -v fn="$1" '$0 ~ "^"fn"\\(\\) \\{"{g=1} g{print} g&&/^\}/{exit}' "$2"; }
eval "$(grep -E '^(PIPESHUB_SIGNER_IDENTITY|PIPESHUB_SIGNER_ISSUER|PIPESHUB_SIGSTORE_ROOT_SHA256|OFFLINE_BUNDLE_SCHEMA|IMAGE_VERIFY_DEFAULT)=' "$INSTALLER")"
for fn in image_verify_mode sha256_file offline_bundle_trusted_root verify_offline_bundle_signature refuse_bundle load_offline_bundle; do
  body="$(extract_fn "$fn" "$INSTALLER")"
  [[ -n "$body" ]] || { fail "install.sh defines $fn"; continue; }
  eval "$body"
done
info() { printf '  > %s\n' "$*"; }; success() { printf '  + %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }; error() { printf '  x %s\n' "$*" >&2; }

# docker: `load` keeps what it was fed and marks the archive's tags as present; `image inspect` answers for them.
STUB="$TMP_ROOT/bin"; mkdir -p "$STUB"
cat >"$STUB/docker" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  load) cat >"$STUB_LOADED"; echo "docker load" >>"$STUB_LOG"; cat "$STUB_TAGS" >>"$STUB_PRESENT" ;;
  image) grep -qxF "${@: -1}" "$STUB_PRESENT" ;;
  *) echo "docker $*" >>"$STUB_LOG" ;;
esac
EOF
# cosign: accepts a manifest only when its SHA-256 is in $STUB_SIGNED (what "the release signed").
cat >"$STUB/cosign" <<'EOF'
#!/usr/bin/env bash
echo "cosign $*" >>"$STUB_LOG"
sum="$(sha256sum "${@: -1}" | cut -d' ' -f1)"
grep -qxF "$sum" "$STUB_SIGNED" && exit 0
echo "Error: none of the expected identities matched" >&2
exit 1
EOF
chmod +x "$STUB/docker" "$STUB/cosign"
export STUB_LOG="$TMP_ROOT/log" STUB_LOADED="$TMP_ROOT/loaded" STUB_TAGS="$TMP_ROOT/tags" STUB_PRESENT="$TMP_ROOT/present" \
  STUB_SIGNED="$TMP_ROOT/signed"
ORIGINAL_PATH="$PATH"
PATH="$STUB:$PATH"

# A bundle as release-publish.yml writes one: two gzip parts, the manifest, its signature, the trusted root.
make_bundle() { # args: dir
  local dir="$1" name="pipeshub-offline-1.2.3-amd64"
  mkdir -p "$dir"
  head -c 4000 /dev/urandom >"$dir/archive"
  gzip -c "$dir/archive" | split -b 1500 -d -a 2 - "$dir/${name}.tar.gz."
  {
    echo "pipeshub-offline-bundle 1"; echo "version 1.2.3"; echo "arch amd64"
    echo "image pipeshubai/pipeshub-ai:1.2.3"; echo "image pipeshubai/pipeshub-sandbox:1.2.3"
    for p in "$dir/${name}.tar.gz."*; do echo "part $(basename "$p") $(sha256sum "$p" | cut -d' ' -f1)"; done
  } >"$dir/${name}.manifest"
  echo '{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}' >"$dir/${name}.manifest.sigstore.json"
  cp "$SCRIPT_DIR/../sigstore-trusted-root.json" "$dir/"
  sha256sum "$dir/${name}.manifest" | cut -d' ' -f1 >>"$STUB_SIGNED"
  printf '%s\n' "$dir/${name}.manifest"
}
printf 'pipeshubai/pipeshub-ai:1.2.3\npipeshubai/pipeshub-sandbox:1.2.3\n' >"$STUB_TAGS"
: >"$STUB_SIGNED"

run() { # args: manifest mode -> output; rc in $TMP_ROOT/rc. Each run starts from an empty Docker.
  : >"$STUB_LOG"; : >"$STUB_PRESENT"; rm -f "$STUB_LOADED"
  ( SCRIPT_DIR="$TMP_ROOT/no-installer-dir"; load_offline_bundle "$1" "$2" ) >"$TMP_ROOT/out" 2>&1
  echo $? >"$TMP_ROOT/rc"
  cat "$TMP_ROOT/out"
}
refused() { # args: desc output needle
  check "$1: refused" "$(cat "$TMP_ROOT/rc")" "1"
  check "$1: says why" "$2" "$3"
  check "$1: says nothing was loaded" "$2" "Nothing was loaded"
  lacks "$1: docker load never ran" "$(cat "$STUB_LOG")" "docker load"
}

echo "== A signed, intact bundle loads =="
GOOD="$(make_bundle "$TMP_ROOT/good")"
IMAGE_TAG=""
out="$(run "$GOOD" require)"
check "it loads" "$(cat "$TMP_ROOT/rc")" "0"
check "the signature is checked first" "$out" "Signature verified: offline bundle"
check "docker load got the parts in order, unpacked" "$(cmp -s "$STUB_LOADED" "$TMP_ROOT/good/archive" && echo same)" "same"
check "each named image is there afterwards" "$out" "Loaded from the signed bundle: pipeshubai/pipeshub-sandbox:1.2.3"
args="$(grep '^cosign' "$STUB_LOG")"
check "cosign verifies the blob" "$args" "cosign verify-blob --bundle $GOOD.sigstore.json"
check "offline" "$args" "--offline"
check "against the pinned trusted root" "$args" "--trusted-root $TMP_ROOT/good/sigstore-trusted-root.json"
check "on the release workflow's identity" "$args" "--certificate-identity-regexp $PIPESHUB_SIGNER_IDENTITY"
check "and GitHub's issuer" "$args" "--certificate-oidc-issuer $PIPESHUB_SIGNER_ISSUER"
check "in the bundle format the release writes" "$args" "--new-bundle-format"
IMAGE_TAG="1.2.3"; run "$GOOD" require >/dev/null
check "the version asked for matches" "$(cat "$TMP_ROOT/rc")" "0"

echo "== Anything else is refused before docker load =="
IMAGE_TAG=""
B="$(make_bundle "$TMP_ROOT/unsigned")"; rm "$B.sigstore.json"
refused "no signature" "$(run "$B" require)" "it is not signed"
refused "no signature under warn" "$(run "$B" warn)" "it is not signed"

B="$(make_bundle "$TMP_ROOT/forged")"
sed -i 's/^version 1.2.3$/version 1.2.4/' "$B"
refused "a manifest the release did not sign" "$(run "$B" require)" "its signature is not a PipesHub release signature"

B="$(make_bundle "$TMP_ROOT/tampered")"
first="$(ls "$TMP_ROOT/tampered/"*.tar.gz.00)"; printf 'x' >>"$first"
refused "a part changed after signing" "$(run "$B" require)" "does not match its checksum"
refused "a changed part under off" "$(run "$B" off)" "does not match its checksum"

B="$(make_bundle "$TMP_ROOT/missing")"; rm "$TMP_ROOT/missing/"*.tar.gz.01
refused "a missing part" "$(run "$B" require)" "is missing"

B="$(make_bundle "$TMP_ROOT/escape")"
sed -i 's/^part \(pipeshub-offline-1.2.3-amd64.tar.gz.00\)/part ..\/\1/' "$B"; sha256sum "$B" | cut -d' ' -f1 >>"$STUB_SIGNED"
refused "a part outside the bundle's folder" "$(run "$B" require)" "outside its folder"

B="$(make_bundle "$TMP_ROOT/root")"; echo '{}' >"$TMP_ROOT/root/sigstore-trusted-root.json"
refused "a trusted root that is not the pinned one" "$(run "$B" require)" "is not the one this installer pins"

IMAGE_TAG="2.0.0"
refused "another version than asked" "$(run "$GOOD" require)" "it holds version 1.2.3, but this install asks for 2.0.0"
IMAGE_TAG=""

B="$(make_bundle "$TMP_ROOT/nonbundle")"; sed -i '1s/.*/something else/' "$B"
refused "a file that is not a manifest" "$(run "$B" require)" "not a PipesHub offline bundle manifest"
refused "a manifest that does not exist" "$(run "$TMP_ROOT/nowhere.manifest" require)" "does not exist"

printf 'pipeshubai/pipeshub-ai:1.2.3\n' >"$STUB_TAGS"
out="$(run "$GOOD" require)"
check "an archive without a named image is refused" "$(cat "$TMP_ROOT/rc")" "1"
check "and says which" "$out" "the archive did not hold pipeshubai/pipeshub-sandbox:1.2.3"
printf 'pipeshubai/pipeshub-ai:1.2.3\npipeshubai/pipeshub-sandbox:1.2.3\n' >"$STUB_TAGS"

echo "== Without cosign on the host =="
NOCOSIGN="$TMP_ROOT/nocosign"; mkdir -p "$NOCOSIGN"; ln -s "$STUB/docker" "$NOCOSIGN/docker"
for tool in sha256sum cut head sed dirname cat gzip tail mktemp rm tr; do
  p="$(PATH="$ORIGINAL_PATH" command -v "$tool")" && ln -sf "$p" "$NOCOSIGN/$tool"
done
out="$(PATH="$NOCOSIGN"; run "$GOOD" require)"
refused "no cosign" "$out" "needs cosign on this host"

echo "== A self-signed bundle and the off switch =="
B="$(make_bundle "$TMP_ROOT/own")"; rm "$TMP_ROOT/own/sigstore-trusted-root.json"
out="$(PIPESHUB_IMAGE_PUBKEY=/keys/own.pub run "$B" require)"
check "a key-signed bundle needs no trusted root" "$(cat "$TMP_ROOT/rc")" "0"
check "and is checked with that key" "$(grep '^cosign' "$STUB_LOG")" "--key /keys/own.pub"
B="$(make_bundle "$TMP_ROOT/offmode")"; rm "$B.sigstore.json"
out="$(run "$B" off)"
check "off loads an unsigned bundle whose checksums hold" "$(cat "$TMP_ROOT/rc")" "0"
check "and says the signature was not checked" "$out" "signature is not checked (PIPESHUB_IMAGE_VERIFY=off)"

echo "== The pins =="
check "the trusted root beside install.sh is the pinned one" \
  "$(sha256_file "$SCRIPT_DIR/../sigstore-trusted-root.json")" "$PIPESHUB_SIGSTORE_ROOT_SHA256"
check "the release writes the manifest schema install.sh reads" \
  "$(grep -c "echo \"$OFFLINE_BUNDLE_SCHEMA\"" "$SCRIPT_DIR/../../../.github/workflows/release-publish.yml")" "1"
check "the release checks the bundle with install.sh's own function" \
  "$(grep -c 'verify_offline_bundle_signature "\$m" "\$root"' "$SCRIPT_DIR/../../../.github/workflows/release-publish.yml")" "1"
out="$(cd "$TMP_ROOT" && bash "$INSTALLER" --offline-bundle "$GOOD" --build 2>&1)"
check "--offline-bundle with --build is refused" "$out" "cannot be combined with --build"

echo "Results: ${PASS} passed, ${FAIL} failed"
[[ "$FAIL" -eq 0 ]]

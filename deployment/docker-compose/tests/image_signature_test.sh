#!/usr/bin/env bash
# ==============================================================================
# SEC-D-04: install.sh checks a release image's signature on the digest it
# pulled and pins that digest in .env. Pure bash with stub docker and cosign;
# no network. IMAGE_SIG_LIVE=1 with COSIGN_BIN and CRANE_BIN adds a real round
# trip: a throwaway key (generated here, deleted on exit), a local in-memory
# registry, a signed image that is accepted and a re-tagged unsigned one that is
# refused.
#
# Run: bash deployment/docker-compose/tests/image_signature_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/../install.sh"
TMP_ROOT="$(mktemp -d)"
REGISTRY_PID=""
cleanup() { [[ -n "$REGISTRY_PID" ]] && kill "$REGISTRY_PID" 2>/dev/null; rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

PASS=0; FAIL=0
pass() { printf "  ok   - %s\n" "$1"; PASS=$((PASS + 1)); }
fail() { printf "  FAIL - %s\n" "$1"; FAIL=$((FAIL + 1)); }
check() { if [[ "$2" == *"$3"* ]]; then pass "$1"; else fail "$1"; printf "         expected: %s\n         got: %s\n" "$3" "$2"; fi; }
lacks() { if [[ "$2" != *"$3"* ]]; then pass "$1"; else fail "$1"; printf "         unexpected: %s\n" "$3"; fi; }

extract_fn() { awk -v fn="$1" '$0 ~ "^"fn"\\(\\) \\{"{g=1} g{print} g&&/^\}/{exit}' "$2"; }
# The constants and helpers the functions read.
eval "$(grep -E '^(PIPESHUB_COSIGN_IMAGE|PIPESHUB_SIGNER_IDENTITY|PIPESHUB_SIGNER_ISSUER|IMAGE_VERIFY_DEFAULT)=' "$INSTALLER")"
for fn in image_verify_mode image_pinned_ref cosign_run signer_identity verify_image_signature refuse_image pin_release_image env_file_set pin_release_images; do
  eval "$(extract_fn "$fn" "$INSTALLER")"
done
info() { printf '  > %s\n' "$*"; }; success() { printf '  + %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }; error() { printf '  x %s\n' "$*" >&2; }

echo "== The mode fails closed =="
check "default mode" "$(image_verify_mode '')" "$IMAGE_VERIFY_DEFAULT"
for m in require warn off; do check "mode $m" "$(image_verify_mode "$m")" "$m"; done
for m in REQUIRE Off 'off ' 'warn;off' '$(id)' no; do check "unknown mode '$m' is require" "$(image_verify_mode "$m")" "require"; done

echo "== The signer identity is the release workflow at a version tag =="
id_ok() { [[ "$1" =~ $PIPESHUB_SIGNER_IDENTITY ]] && echo yes || echo no; }
check "release workflow at v1.2.3" "$(id_ok 'https://github.com/pipeshub-ai/pipeshub-ai/.github/workflows/release-publish.yml@refs/tags/v1.2.3')" yes
check "a branch is not a release" "$(id_ok 'https://github.com/pipeshub-ai/pipeshub-ai/.github/workflows/release-publish.yml@refs/heads/main')" no
check "another workflow is not the release" "$(id_ok 'https://github.com/pipeshub-ai/pipeshub-ai/.github/workflows/e2e.yml@refs/tags/v1.2.3')" no
check "a fork is not the release" "$(id_ok 'https://github.com/evil/pipeshub-ai/.github/workflows/release-publish.yml@refs/tags/v1.2.3')" no
check "a look-alike owner is not the release" "$(id_ok 'https://github.com/pipeshub-ai-x/pipeshub-ai/.github/workflows/release-publish.yml@refs/tags/v1')" no
check "a prefix host is not github.com" "$(id_ok 'https://github.com.evil.example/pipeshub-ai/pipeshub-ai/.github/workflows/release-publish.yml@refs/tags/v1')" no
check "the dots are literal" "$(id_ok 'https://githubXcom/pipeshub-ai/pipeshub-ai/.github/workflows/release-publish.yml@refs/tags/v1')" no
check "the issuer is GitHub Actions OIDC" "$PIPESHUB_SIGNER_ISSUER" "https://token.actions.githubusercontent.com"
check "cosign runs from a digest-pinned image" "$PIPESHUB_COSIGN_IMAGE" "@sha256:"

# Stub docker: RepoDigests per image from $STUB_DIGESTS ("ref digestline" pairs).
STUB="$TMP_ROOT/bin"; mkdir -p "$STUB"
cat >"$STUB/docker" <<'STUBEOF'
#!/usr/bin/env bash
if [[ "$1 $2" == "image inspect" ]]; then
  ref="${@: -1}"
  while read -r r d; do [[ "$r" == "$ref" ]] && printf '%s\n' "$d"; done <"$STUB_DIGESTS"
  exit 0
fi
echo "docker $*" >>"$STUB_LOG"
exit 0
STUBEOF
# Stub cosign: verifies a ref only if it is listed in $STUB_SIGNED; logs its arguments.
cat >"$STUB/cosign" <<'STUBEOF'
#!/usr/bin/env bash
echo "cosign $*" >>"$STUB_LOG"
ref="${@: -1}"
grep -qxF "$ref" "$STUB_SIGNED" && exit 0
echo "Error: no matching signatures" >&2
exit 1
STUBEOF
chmod +x "$STUB/docker" "$STUB/cosign"
export STUB_DIGESTS="$TMP_ROOT/digests" STUB_SIGNED="$TMP_ROOT/signed" STUB_LOG="$TMP_ROOT/log"
D1="sha256:$(printf 'a%.0s' {1..64})"; D2="sha256:$(printf 'b%.0s' {1..64})"
cat >"$STUB_DIGESTS" <<DIG
pipeshubai/pipeshub-ai:1.2.3 pipeshubai/pipeshub-ai@$D1
pipeshubai/pipeshub-ai:1.2.3 other/mirror@$D2
pipeshubai/pipeshub-sandbox:1.2.3 pipeshubai/pipeshub-sandbox@$D2
DIG
printf 'pipeshubai/pipeshub-ai:1.2.3@%s\n' "$D1" >"$STUB_SIGNED"
: >"$STUB_LOG"
PATH="$STUB:$PATH"

echo "== The digest pinned is the pulled image's own =="
check "tag resolves to its repository's digest" "$(image_pinned_ref pipeshubai/pipeshub-ai:1.2.3)" "pipeshubai/pipeshub-ai:1.2.3@$D1"
lacks "another repository's digest is ignored" "$(image_pinned_ref pipeshubai/pipeshub-ai:1.2.3)" "$D2"
check "a digest reference is kept" "$(image_pinned_ref "x/y@$D2")" "x/y@$D2"
check "a locally built image has no pin" "[$(image_pinned_ref pipeshubai/pipeshub-ai:local)]" "[]"

_COSIGN_ERR="$TMP_ROOT/cosign.err"
echo "== require: a signed digest runs, anything else is refused =="
out="$(pin_release_image app pipeshubai/pipeshub-ai:1.2.3 require true 2>&1)"; rc=$?
check "signed image is pinned" "$out" "pipeshubai/pipeshub-ai:1.2.3@$D1"
check "signed image passes" "$rc" "0"
check "keyless check names the release identity" "$(cat "$STUB_LOG")" "--certificate-identity-regexp $PIPESHUB_SIGNER_IDENTITY --certificate-oidc-issuer $PIPESHUB_SIGNER_ISSUER pipeshubai/pipeshub-ai:1.2.3@$D1"
lacks "keyless check never skips the transparency log" "$(cat "$STUB_LOG")" "insecure-ignore-tlog"
out="$(pin_release_image sandbox pipeshubai/pipeshub-sandbox:1.2.3 require true 2>&1)"; rc=$?
check "an unsigned (re-pushed) image is refused" "$rc" "1"
check "the refusal says what happened" "$out" "Refusing to start PipesHub: the sandbox image pipeshubai/pipeshub-sandbox:1.2.3@$D2 has no valid PipesHub release signature"
check "the refusal says nothing started" "$out" "This run started nothing"
check "the refusal says what to do" "$out" "What to do: re-run install.sh"
check "the refusal shows cosign's reason" "$out" "no matching signatures"
lacks "a refused image is not pinned" "$out" $'\n'"pipeshubai/pipeshub-sandbox:1.2.3@$D2"$'\n'
out="$(pin_release_image app pipeshubai/pipeshub-ai:local require true 2>&1)"; rc=$?
check "require refuses an image with no registry digest" "$rc" "1"
check "and says why" "$out" "has no registry digest"

echo "== warn and off pin the digest but do not stop =="
out="$(pin_release_image sandbox pipeshubai/pipeshub-sandbox:1.2.3 warn true 2>&1)"; rc=$?
check "warn runs an unsigned image" "$rc" "0"
check "warn says so plainly" "$out" "has no valid PipesHub release signature; running it because PIPESHUB_IMAGE_VERIFY=warn"
check "warn still pins the digest" "$out" "pipeshubai/pipeshub-sandbox:1.2.3@$D2"
: >"$STUB_LOG"
out="$(pin_release_image sandbox pipeshubai/pipeshub-sandbox:1.2.3 off true 2>&1)"
check "off pins the digest" "$out" "pipeshubai/pipeshub-sandbox:1.2.3@$D2"
check "off runs no cosign" "[$(cat "$STUB_LOG")]" "[]"
out="$(pin_release_image sandbox pipeshubai/pipeshub-sandbox:1.2.3 require false 2>&1)"; rc=$?
check "a run that did not pull pins what is cached" "$out" "@$D2"
check "and is not refused for a check it could not make" "$rc" "0"

echo "== a key-signed mirror =="
: >"$STUB_LOG"
PIPESHUB_IMAGE_PUBKEY="$TMP_ROOT/k.pub" PIPESHUB_IMAGE_VERIFY_NO_TLOG=1 verify_image_signature "pipeshubai/pipeshub-ai:1.2.3@$D1"
check "a key replaces the keyless identity" "$(cat "$STUB_LOG")" "verify --key $TMP_ROOT/k.pub --insecure-ignore-tlog=true pipeshubai/pipeshub-ai:1.2.3@$D1"
lacks "and the identity flags are gone" "$(cat "$STUB_LOG")" "certificate-identity"

echo "== .env gets the pins, mode 600, nothing else changed =="
ENV_FILE="$TMP_ROOT/.env"; printf 'IMAGE_TAG=1.2.3\nPIPESHUB_APP_PINNED=old\nSECRET_KEY=keep\n' >"$ENV_FILE"; chmod 644 "$ENV_FILE"
_APP_IMAGE=pipeshubai/pipeshub-ai:1.2.3 _SANDBOX_IMAGE=pipeshubai/pipeshub-sandbox:1.2.3
PIPESHUB_IMAGE_VERIFY=warn pin_release_images true true >/dev/null 2>&1
envtext="$(cat "$ENV_FILE")"
check "the app pin is written" "$envtext" "PIPESHUB_APP_PINNED=pipeshubai/pipeshub-ai:1.2.3@$D1"
check "the sandbox pin is written" "$envtext" "PIPESHUB_SANDBOX_PINNED=pipeshubai/pipeshub-sandbox:1.2.3@$D2"
lacks "the old pin is replaced" "$envtext" "PIPESHUB_APP_PINNED=old"
check "other keys are kept" "$envtext" "SECRET_KEY=keep"
check "the file stays private" "$(stat -c %a "$ENV_FILE")" "600"
status=0; (PIPESHUB_IMAGE_VERIFY=require pin_release_images true true >/dev/null 2>&1) || status=$?
check "require stops the install on the unsigned sandbox" "$status" "1"

echo "== Compose runs the pinned references =="
compose="$(cat "$SCRIPT_DIR/../docker-compose.yml")"
check "app image" "$compose" 'image: ${PIPESHUB_APP_PINNED:-${PIPESHUB_IMAGE:-pipeshubai/pipeshub-ai}:${IMAGE_TAG:-latest}}'
check "sandbox image" "$compose" 'image: ${PIPESHUB_SANDBOX_PINNED:-${SANDBOX_DOCKER_IMAGE:-pipeshubai/pipeshub-sandbox:${IMAGE_TAG:-latest}}}'
check "the broker runs sandboxes from the pinned sandbox" "$compose" 'SANDBOX_DOCKER_IMAGE: ${PIPESHUB_SANDBOX_PINNED:-'
# R44STOP-FU-3: the proxy's broker profile lets the egress firewall's root helper run from that image alone.
proxy_block="$(awk '/^  docker-socket-proxy:/{g=1;next} g&&/^  [a-z]/{exit} g{print}' "$SCRIPT_DIR/../docker-compose.yml")"
check "the proxy holds the firewall helper to the pinned sandbox" "$proxy_block" 'SANDBOX_DOCKER_IMAGE: ${PIPESHUB_SANDBOX_PINNED:-${SANDBOX_DOCKER_IMAGE:-pipeshubai/pipeshub-sandbox:${IMAGE_TAG:-latest}}}'
inner="$(cat "$INSTALLER")"
check "the installer pins after every prebuilt pull" "$inner" 'pin_release_images "$_DO_PULL"'
check "the pull fetches the tag, not an old pin" "$inner" $'# The pull must fetch the tag, not the digest an earlier run pinned.\n  env_file_set PIPESHUB_APP_PINNED ""'

if [[ "${IMAGE_SIG_LIVE:-}" == 1 ]]; then
  echo "== Live: a throwaway key, a local registry, a signed and a re-tagged unsigned image =="
  : "${COSIGN_BIN:?COSIGN_BIN}" "${CRANE_BIN:?CRANE_BIN}"
  live="$TMP_ROOT/live"; mkdir -p "$live/bin"
  ln -s "$COSIGN_BIN" "$live/bin/cosign"
  port=$((20000 + RANDOM % 20000))
  PORT=$port "$CRANE_BIN" registry serve --address "127.0.0.1:$port" >"$live/registry.log" 2>&1 &
  REGISTRY_PID=$!
  for _ in $(seq 1 50); do curl -fsS "http://127.0.0.1:$port/v2/" >/dev/null 2>&1 && break; kill -0 "$REGISTRY_PID" || break; sleep 0.1; done
  reg="127.0.0.1:$port"
  printf 'signed\n' >"$live/a.txt"; tar -C "$live" -cf "$live/a.tar" a.txt
  printf 'attacker\n' >"$live/b.txt"; tar -C "$live" -cf "$live/b.tar" b.txt
  "$CRANE_BIN" append --insecure -f "$live/a.tar" -t "$reg/pipeshubai/pipeshub-ai:9.9.9" >/dev/null
  good="$reg/pipeshubai/pipeshub-ai@$("$CRANE_BIN" digest --insecure "$reg/pipeshubai/pipeshub-ai:9.9.9")"
  (cd "$live" && COSIGN_PASSWORD=throwaway "$COSIGN_BIN" generate-key-pair >/dev/null 2>&1)
  COSIGN_PASSWORD=throwaway "$COSIGN_BIN" sign --yes --key "$live/cosign.key" --tlog-upload=false \
    --allow-insecure-registry "$good" >/dev/null 2>&1 && pass "a throwaway key signed the image" || fail "a throwaway key signed the image"
  "$CRANE_BIN" append --insecure -f "$live/b.tar" -t "$reg/pipeshubai/pipeshub-ai:9.9.9" >/dev/null
  bad="$reg/pipeshubai/pipeshub-ai@$("$CRANE_BIN" digest --insecure "$reg/pipeshubai/pipeshub-ai:9.9.9")"
  export COSIGN_ALLOW_INSECURE_REGISTRY=true
  ok=0; PATH="$live/bin:$PATH" PIPESHUB_IMAGE_PUBKEY="$live/cosign.pub" PIPESHUB_IMAGE_VERIFY_NO_TLOG=1 \
    verify_image_signature "$good" || ok=$?
  check "the signed digest verifies" "$ok" "0"
  ok=0; PATH="$live/bin:$PATH" PIPESHUB_IMAGE_PUBKEY="$live/cosign.pub" PIPESHUB_IMAGE_VERIFY_NO_TLOG=1 \
    _COSIGN_ERR="$live/err" verify_image_signature "$bad" || ok=$?
  check "the re-tagged unsigned image is refused" "$ok" "1"
  (cd "$live" && COSIGN_PASSWORD=other "$COSIGN_BIN" generate-key-pair --output-key-prefix other >/dev/null 2>&1)
  ok=0; PATH="$live/bin:$PATH" PIPESHUB_IMAGE_PUBKEY="$live/other.pub" PIPESHUB_IMAGE_VERIFY_NO_TLOG=1 \
    verify_image_signature "$good" || ok=$?
  check "a signature by another key is refused" "$ok" "1"
  out="$(PATH="$live/bin:$PATH" PIPESHUB_IMAGE_PUBKEY="$live/cosign.pub" PIPESHUB_IMAGE_VERIFY_NO_TLOG=1 \
    _COSIGN_ERR="$live/err" pin_release_image app "$bad" require true 2>&1)"; rc=$?
  check "install refuses the re-tagged image" "$rc" "1"
  check "with the plain refusal" "$out" "has no valid PipesHub release signature"
  rm -f "$live/cosign.key" "$live/other.key"
fi

printf "Results: %s passed, %s failed\n" "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]

#!/usr/bin/env bash
# install-deps.sh installs only pinned releases whose bytes match the SHA-256 it
# carries (R41SUPPLY-FU-10). curl and uname are stubbed: no network, nothing
# leaves the temporary directory.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$ROOT/deployment/helm/aws/install-deps.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS_FILE="$TMP/pass"; FAIL_FILE="$TMP/fail"; : >"$PASS_FILE"; : >"$FAIL_FILE"
pass() { printf '  ok   - %s\n' "$1"; echo x >>"$PASS_FILE"; }
fail() { printf '  FAIL - %s\n' "$1"; printf '%s\n' "$1" >>"$FAIL_FILE"; }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

STUB="$TMP/stub"; mkdir -p "$STUB"
CALLS="$TMP/calls"
# curl serves an attacker's bytes for every file, and the matching checksum for
# every checksum URL: a script that trusts the server's sum installs them.
cat >"$STUB/curl" <<'EOF'
#!/usr/bin/env bash
out=""; url=""
while [[ $# -gt 0 ]]; do
  case "$1" in -o) out="$2"; shift ;; -*) ;; *) url="$1" ;; esac
  shift
done
echo "$url" >>"$CALLS"
evil="$(printf 'evil %s\n' "${url%.sha256*}" | sha256sum | cut -d' ' -f1)"
case "$url" in
  *stable*.txt) body="v1.36.6" ;;
  *helm-latest-version) body="v4.3.2" ;;
  *.sha256|*.sha256sum) body="$evil" ;;
  *checksums.txt) body="$evil  eksctl_Linux_amd64.tar.gz" ;;
  *) body="evil ${url}" ;;
esac
if [[ -n "$out" ]]; then printf '%s\n' "$body" >"$out"; else printf '%s\n' "$body"; fi
EOF
cat >"$STUB/uname" <<'EOF'
#!/usr/bin/env bash
case "$1" in -s) echo Linux ;; -m) echo x86_64 ;; *) echo Linux ;; esac
EOF
# Tools the script must find missing; tar and unzip would unpack the bad bytes.
for t in tar unzip; do printf '#!/usr/bin/env bash\necho "%s $*" >>"$CALLS"\n' "$t" >"$STUB/$t"; done
chmod +x "$STUB"/*

run() { # tool... -> runs install-deps with only the stubs and the base userland on PATH
  : >"$CALLS"
  local home="$TMP/home-$RANDOM"
  mkdir -p "$home"
  env -i HOME="$home" PATH="$STUB:/usr/bin:/bin" CALLS="$CALLS" K8S_VERSION="${K8S_VERSION:-}" \
    PIPESHUB_HOME="$home/.pipeshub" bash "$SCRIPT" --yes >"$TMP/out" 2>&1
  echo $? >"$TMP/rc"
  BIN="$home/.pipeshub/bin"
}

# Only the tool under test is missing: shadow the others with stand-ins that pass `needs`.
shadow_except() {
  rm -f "$STUB/aws" "$STUB/eksctl" "$STUB/kubectl" "$STUB/helm"
  [[ "$1" == aws ]] || printf '#!/usr/bin/env bash\necho aws-cli/2.40.3\n' >"$STUB/aws"
  [[ "$1" == eksctl ]] || printf '#!/usr/bin/env bash\necho 0.231.0\n' >"$STUB/eksctl"
  [[ "$1" == kubectl ]] || printf '#!/usr/bin/env bash\necho ok\n' >"$STUB/kubectl"
  [[ "$1" == helm ]] || printf '#!/usr/bin/env bash\necho v4.3.2\n' >"$STUB/helm"
  chmod +x "$STUB"/*
}

echo "== install-deps refuses bytes that do not match its pins =="
for tool in kubectl helm eksctl aws; do
  shadow_except "$tool"
  run
  check "$tool: tampered download fails" '[[ "$(cat "$TMP/rc")" != 0 ]]'
  check "$tool: it says the checksum did not match" 'grep -q "checksum mismatch" "$TMP/out"'
  check "$tool: nothing is installed" '[[ ! -e "$BIN/$tool" ]]'
  check "$tool: nothing is unpacked" '! grep -qE "^(tar|unzip) " "$CALLS"'
  check "$tool: no checksum is read from the download server" '! grep -qE "\.sha256|checksums\.txt" "$CALLS"'
  check "$tool: no moving latest version is read" '! grep -qE "stable(-[0-9.]+)?\.txt|latest" "$CALLS"'
done

echo "== kubectl follows the cluster's minor version, and only a pinned one =="
shadow_except kubectl
K8S_VERSION=1.34 run
check "1.34 fetches the pinned v1.34.13" 'grep -q "/release/v1.34.13/bin/linux/amd64/kubectl" "$CALLS"'
K8S_VERSION=1.20 run
check "an unpinned minor is refused" '[[ "$(cat "$TMP/rc")" != 0 ]] && grep -q "no pinned kubectl for Kubernetes 1.20" "$TMP/out"'
check "and nothing is downloaded for it" '[[ ! -s "$CALLS" ]]'

echo "== the pin table =="
eval "$(sed -n '/^KUBECTL_VERSIONS=/p; /^HELM_VERSION=/p; /^EKSCTL_VERSION=/p; /^AWS_CLI_VERSION=/p' "$SCRIPT")"
eval "$(awk '/^pinned_sha256\(\) \{/{g=1} g{print} g && $0 == "}"{exit}' "$SCRIPT")"
missing=()
for entry in $KUBECTL_VERSIONS; do
  for p in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64; do
    [[ "$(pinned_sha256 "kubectl-${entry#*:}-$p")" =~ ^[0-9a-f]{64}$ ]] || missing+=("kubectl-${entry#*:}-$p")
  done
done
for p in linux-amd64 linux-arm64 darwin-amd64 darwin-arm64; do
  [[ "$(pinned_sha256 "helm-${HELM_VERSION}-$p.tar.gz")" =~ ^[0-9a-f]{64}$ ]] || missing+=("helm-$p")
done
for p in Linux_amd64 Linux_arm64 Darwin_amd64 Darwin_arm64; do
  [[ "$(pinned_sha256 "eksctl-${EKSCTL_VERSION}-$p.tar.gz")" =~ ^[0-9a-f]{64}$ ]] || missing+=("eksctl-$p")
done
for f in "awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip" "awscli-exe-linux-aarch64-${AWS_CLI_VERSION}.zip" \
         "AWSCLIV2-${AWS_CLI_VERSION}.pkg"; do
  [[ "$(pinned_sha256 "$f")" =~ ^[0-9a-f]{64}$ ]] || missing+=("$f")
done
check "every version and platform has a SHA-256 (missing: ${missing[*]:-none})" '[[ ${#missing[@]} -eq 0 ]]'
cluster_minor="$(sed -n 's/^  version: "\(.*\)"$/\1/p' "$ROOT/deployment/helm/aws/cluster.yaml")"
check "cluster.yaml's Kubernetes ${cluster_minor} has a pinned kubectl" '[[ " $KUBECTL_VERSIONS " == *" ${cluster_minor}:"* ]]'

passed=$(wc -l <"$PASS_FILE"); failed=$(wc -l <"$FAIL_FILE")
echo "Results: ${passed} passed, ${failed} failed"
[[ "$failed" -eq 0 ]]

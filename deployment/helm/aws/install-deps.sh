#!/usr/bin/env bash
# Install the command-line tools deploy.sh needs: aws (v2), eksctl, kubectl, helm.
# macOS and Linux, amd64 and arm64. Windows: run inside WSL2.
# Installs only what is missing or too old, into ~/.pipeshub/bin, without sudo.
# Downloads are pinned official releases, each checked against the SHA-256 this
# script carries (never one fetched beside the download), and the macOS AWS CLI
# package against its signature as well.
#
#   ./deployment/helm/aws/install-deps.sh           # install what is missing
#   ./deployment/helm/aws/install-deps.sh --check   # only report
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PIPESHUB_HOME="${PIPESHUB_HOME:-${HOME}/.pipeshub}"
BIN_DIR="${PIPESHUB_BIN_DIR:-${PIPESHUB_HOME}/bin}"
CHECK_ONLY=false
ASSUME_YES=false

MIN_EKSCTL=0.200.0
MIN_HELM=3.11.0

# The versions installed, and their SHA-256 by platform. A checksum read from the
# server the file comes from proves only the transfer; these prove the bytes. To
# move a version, take the sums from the release and change the version and its
# lines together (scripts/supply_chain.py DOWNLOAD-HASH holds this file to it).
HELM_VERSION=v4.3.2
EKSCTL_VERSION=v0.231.0
AWS_CLI_VERSION=2.40.3
# kubectl by the cluster's minor version (one minor of skew is supported).
KUBECTL_VERSIONS="1.33:v1.33.13 1.34:v1.34.13 1.35:v1.35.10 1.36:v1.36.6"

pinned_sha256() { # name -> pinned SHA-256, or empty
  case "$1" in
    kubectl-v1.33.13-linux-amd64)  echo 316d712726d857c20744c57cb36aa47cfc79bc0af7e82cebf3780244b654c073 ;;
    kubectl-v1.33.13-linux-arm64)  echo 9fe8aadd5aab9421978c7ac95de6fad304a3c4c0ce9dbf4127579779e0699080 ;;
    kubectl-v1.33.13-darwin-amd64) echo 5c55bdb8896681b6921cad3cff20a09b424887bb5bcb771de84b42e4322faeeb ;;
    kubectl-v1.33.13-darwin-arm64) echo 17e3d0a6d9908914c67370b329b6aa9489de7ca035ecd5b7773456cdbe318bbd ;;
    kubectl-v1.34.13-linux-amd64)  echo b4bb494e9466a7a94bb851414548bc0d3e2f8da90757e6eccc3371e648648591 ;;
    kubectl-v1.34.13-linux-arm64)  echo 66e8283d52d9bc1f9c0e9daade7c971cd20db5a379306862897dfb25395f3e03 ;;
    kubectl-v1.34.13-darwin-amd64) echo 7655b001c24adffac62f7905bde27ccdd2f59cc32bb947f72a8f6e984ca69cb1 ;;
    kubectl-v1.34.13-darwin-arm64) echo c246beefae38db97fd82977864fe9253cd14a3f66325827e56082747bd72143d ;;
    kubectl-v1.35.10-linux-amd64)  echo c49a28f1f4efc4d475ed05f47f2ce8e563bf7bef69ed77fe1b924ea6d83b6327 ;;
    kubectl-v1.35.10-linux-arm64)  echo 283f1be6a46b3695b625e25030106b8ffe18bd0d3a900f5b65c259ca407f04f5 ;;
    kubectl-v1.35.10-darwin-amd64) echo 4787e20d9029871845d8a57bf56f533870f5d98d9cf3b82b6f5e2ad5eaeee3a3 ;;
    kubectl-v1.35.10-darwin-arm64) echo 77ec066f3404edb81760865c9cd812d03c25e9a89aa0349fd3c1aa60bd5fb068 ;;
    kubectl-v1.36.6-linux-amd64)   echo 29818b6729dc9262b692307470d0558d027725e738be5dd47c760261868a2634 ;;
    kubectl-v1.36.6-linux-arm64)   echo 35507ef408c3d38849e5d3203e18f8e861db822aa664101a9ced0869edd06046 ;;
    kubectl-v1.36.6-darwin-amd64)  echo 2e22d382f2423c88d9d076246f860bb3d5c743dd279ca63e113474bb88382426 ;;
    kubectl-v1.36.6-darwin-arm64)  echo 1cf735c3d35ccfec28d0618784cb06a4614d2c42aade28b6be0eab76a2ffb623 ;;
    helm-v4.3.2-linux-amd64.tar.gz)  echo d1c0278d46beab79582d7e50f5b3dc206944ba8e577cde618513fd07daa53983 ;;
    helm-v4.3.2-linux-arm64.tar.gz)  echo df31cb61680ef728f82c770e5280dd565617bee6b039d37e85c677288be4bbb0 ;;
    helm-v4.3.2-darwin-amd64.tar.gz) echo 9e0068e9ae18c441d27388190ee7f53ae65eba44bfe910b40bdac44c4b51fa71 ;;
    helm-v4.3.2-darwin-arm64.tar.gz) echo 6f3421de3a8cc2ac578c8cfb33b8edde290dde2871bc1bab12e24f2e5caeaa90 ;;
    eksctl-v0.231.0-Linux_amd64.tar.gz)  echo 13cd1c4ae6ad3f74ea27aa1117c72e34ffa4ca8ff6d709261f7b1d7f411dc435 ;;
    eksctl-v0.231.0-Linux_arm64.tar.gz)  echo aeb095535b4aebe308ca380d26c6a1325012fda9cd2c22b7cdffee2b5de73615 ;;
    eksctl-v0.231.0-Darwin_amd64.tar.gz) echo 92a3ab9b04033ea69dd4f833f3c89bcefdf0cdff5f54e5bee44342b1911aa114 ;;
    eksctl-v0.231.0-Darwin_arm64.tar.gz) echo 1e368bf18faf28bd3042bbf1ae1cc2430de8a80d60995898ccfca0423349f140 ;;
    awscli-exe-linux-x86_64-2.40.3.zip)  echo 4770c06c4ecfc8ba94b60d89e6c7018ab7ea2cf5341b1ef05f6f93c5d6e8b705 ;;
    awscli-exe-linux-aarch64-2.40.3.zip) echo 4c5f080c6c294eb0da77734c4520f5b5089cf45a4ee757ca34ddf7f9e512e66a ;;
    AWSCLIV2-2.40.3.pkg)                 echo be032742ff205f7f2d7c189010bbb1f3891d0a67be0b1df0fe9d3e7f5fc5ad30 ;;
  esac
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=true; shift ;;
    -y|--yes) ASSUME_YES=true; shift ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "install-deps: unknown option $1" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then C_CYAN=$'\033[36m'; C_RED=$'\033[31m'; C_RESET=$'\033[0m'; else C_CYAN=""; C_RED=""; C_RESET=""; fi
note() { printf '%s>%s %s\n' "$C_CYAN" "$C_RESET" "$*"; }
die() { printf '%sinstall-deps:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

case "$(uname -s)" in
  Darwin) OS=darwin ;;
  Linux) OS=linux ;;
  MINGW*|MSYS*|CYGWIN*)
    die "Windows is supported through WSL2. In PowerShell run: wsl --install -d Ubuntu. Then clone the repository inside Ubuntu and run this script there." ;;
  *) die "unsupported operating system: $(uname -s)" ;;
esac
case "$(uname -m)" in
  x86_64|amd64) ARCH=amd64 ;;
  arm64|aarch64) ARCH=arm64 ;;
  *) die "unsupported CPU architecture: $(uname -m)" ;;
esac

ORIGINAL_PATH="$PATH"
export PATH="${BIN_DIR}:${PATH}"

# Pure bash so it works with BSD and GNU userlands alike.
version_ge() { # a b -> a >= b
  local IFS=. i
  local -a a b
  read -r -a a <<<"${1#v}"
  read -r -a b <<<"${2#v}"
  for i in 0 1 2; do
    (( 10#${a[i]:-0} > 10#${b[i]:-0} )) && return 0
    (( 10#${a[i]:-0} < 10#${b[i]:-0} )) && return 1
  done
  return 0
}

# Checks the file against the SHA-256 pinned for <name>; a file with no pin is refused.
verify_pinned_sha256() { # file name
  local expected
  expected="$(pinned_sha256 "$2")"
  [[ -n "$expected" ]] || die "no pinned checksum for $2 in install-deps.sh; nothing was installed"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s  %s\n' "$expected" "$1" | sha256sum -c --status - \
      || die "checksum mismatch for $2 (expected $expected); nothing was installed"
  else
    printf '%s  %s\n' "$expected" "$1" | shasum -a 256 -c --status - \
      || die "checksum mismatch for $2 (expected $expected); nothing was installed"
  fi
}

# Prints why a tool must be installed, or nothing if the installed one is fine.
needs() {
  local v
  case "$1" in
    aws)
      command -v aws >/dev/null 2>&1 || { echo "missing"; return; }
      v="$(aws --version 2>&1 | sed -n 's|^aws-cli/\([0-9.]*\).*|\1|p')"
      version_ge "${v:-0}" 2.0.0 || echo "version ${v:-unknown} is too old, need AWS CLI v2" ;;
    eksctl)
      command -v eksctl >/dev/null 2>&1 || { echo "missing"; return; }
      v="$(eksctl version 2>/dev/null | head -1)"
      version_ge "${v:-0}" "$MIN_EKSCTL" || echo "version ${v:-unknown} is older than ${MIN_EKSCTL}" ;;
    helm)
      command -v helm >/dev/null 2>&1 || { echo "missing"; return; }
      v="$(helm version --template '{{.Version}}' 2>/dev/null)"
      version_ge "${v:-0}" "$MIN_HELM" || echo "version ${v:-unknown} is older than ${MIN_HELM}" ;;
    kubectl)
      command -v kubectl >/dev/null 2>&1 || echo "missing" ;;
  esac
}

# kubectl supports one minor version of skew with the cluster, so match the cluster's minor.
k8s_minor() {
  local v="${K8S_VERSION:-}"
  [[ -n "$v" ]] || v="$(sed -n 's/^  version: "\(.*\)"$/\1/p' "${ROOT}/deployment/helm/aws/cluster.yaml" 2>/dev/null || true)"
  echo "${v:-}"
}

kubectl_version() { # cluster minor -> pinned kubectl version, or empty
  local entry
  for entry in $KUBECTL_VERSIONS; do
    [[ "${entry%%:*}" == "$1" ]] && { echo "${entry#*:}"; return 0; }
  done
  return 0
}

install_kubectl() {
  local minor version url
  minor="$(k8s_minor | cut -d. -f1-2)"
  [[ -n "$minor" ]] || { minor="${KUBECTL_VERSIONS##* }"; minor="${minor%%:*}"; }
  version="$(kubectl_version "$minor")"
  [[ -n "$version" ]] || die "no pinned kubectl for Kubernetes ${minor} (pinned: ${KUBECTL_VERSIONS}). Install kubectl ${minor} yourself, or add its version and checksums to install-deps.sh"
  url="https://dl.k8s.io/release/${version}/bin/${OS}/${ARCH}/kubectl"
  curl -fsSL -o "$TMP/kubectl" "$url"
  verify_pinned_sha256 "$TMP/kubectl" "kubectl-${version}-${OS}-${ARCH}"
  install -m 0755 "$TMP/kubectl" "$BIN_DIR/kubectl"
  note "kubectl ${version}"
}

install_helm() {
  local file="helm-${HELM_VERSION}-${OS}-${ARCH}.tar.gz"
  curl -fsSL -o "$TMP/$file" "https://get.helm.sh/${file}"
  verify_pinned_sha256 "$TMP/$file" "$file"
  tar -xzf "$TMP/$file" -C "$TMP"
  install -m 0755 "$TMP/${OS}-${ARCH}/helm" "$BIN_DIR/helm"
  note "helm ${HELM_VERSION}"
}

install_eksctl() {
  local platform file
  platform="$(uname -s)_${ARCH}"
  file="eksctl_${platform}.tar.gz"
  curl -fsSL -o "$TMP/$file" "https://github.com/eksctl-io/eksctl/releases/download/${EKSCTL_VERSION}/${file}"
  verify_pinned_sha256 "$TMP/$file" "eksctl-${EKSCTL_VERSION}-${platform}.tar.gz"
  tar -xzf "$TMP/$file" -C "$TMP"
  install -m 0755 "$TMP/eksctl" "$BIN_DIR/eksctl"
  note "eksctl $("$BIN_DIR/eksctl" version)"
}

install_aws() {
  if [[ "$OS" == darwin ]]; then
    curl -fsSL -o "$TMP/AWSCLIV2.pkg" "https://awscli.amazonaws.com/AWSCLIV2-${AWS_CLI_VERSION}.pkg"
    verify_pinned_sha256 "$TMP/AWSCLIV2.pkg" "AWSCLIV2-${AWS_CLI_VERSION}.pkg"
    local signature
    signature="$(pkgutil --check-signature "$TMP/AWSCLIV2.pkg")"
    grep -q "Developer ID Installer: AMZN Mobile LLC (94KV3E626L)" <<<"$signature" \
      && grep -q "trusted by the Apple notary service" <<<"$signature" \
      || die "AWSCLIV2.pkg is not signed and notarized as AWS's installer (AMZN Mobile LLC, 94KV3E626L)"
    cat >"$TMP/choices.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <array>
    <dict>
      <key>choiceAttribute</key>
      <string>customLocation</string>
      <key>attributeSetting</key>
      <string>${PIPESHUB_HOME}</string>
      <key>choiceIdentifier</key>
      <string>default</string>
    </dict>
  </array>
</plist>
EOF
    installer -pkg "$TMP/AWSCLIV2.pkg" -target CurrentUserHomeDirectory -applyChoiceChangesXML "$TMP/choices.xml" >/dev/null
    ln -sf "${PIPESHUB_HOME}/aws-cli/aws" "$BIN_DIR/aws"
    ln -sf "${PIPESHUB_HOME}/aws-cli/aws_completer" "$BIN_DIR/aws_completer"
  else
    command -v unzip >/dev/null 2>&1 || die "unzip is required to install the AWS CLI. $(pkg_hint unzip)"
    local machine=x86_64
    [[ "$ARCH" == arm64 ]] && machine=aarch64
    curl -fsSL -o "$TMP/awscliv2.zip" "https://awscli.amazonaws.com/awscli-exe-linux-${machine}-${AWS_CLI_VERSION}.zip"
    verify_pinned_sha256 "$TMP/awscliv2.zip" "awscli-exe-linux-${machine}-${AWS_CLI_VERSION}.zip"
    unzip -q "$TMP/awscliv2.zip" -d "$TMP"
    "$TMP/aws/install" --install-dir "${PIPESHUB_HOME}/aws-cli" --bin-dir "$BIN_DIR" --update >/dev/null
  fi
  note "$("$BIN_DIR/aws" --version)"
}

pkg_hint() { # packages...
  if [[ "$OS" == darwin ]]; then echo "Install with: xcode-select --install"
  elif command -v apt-get >/dev/null 2>&1; then echo "Install with: sudo apt-get install -y $*"
  elif command -v dnf >/dev/null 2>&1; then echo "Install with: sudo dnf install -y $*"
  elif command -v yum >/dev/null 2>&1; then echo "Install with: sudo yum install -y $*"
  elif command -v zypper >/dev/null 2>&1; then echo "Install with: sudo zypper install -y $*"
  elif command -v apk >/dev/null 2>&1; then echo "Install with: sudo apk add $*"
  else echo "Install them with your package manager."; fi
}

# Base utilities come from the OS; this script does not use sudo.
base_missing=()
for tool in curl tar openssl base64 awk sed; do
  command -v "$tool" >/dev/null 2>&1 || base_missing+=("$tool")
done
if [[ ${#base_missing[@]} -gt 0 ]]; then
  die "missing system tools: ${base_missing[*]}. $(pkg_hint "${base_missing[@]}")"
fi

todo=()
for tool in aws eksctl kubectl helm; do
  reason="$(needs "$tool")"
  if [[ -n "$reason" ]]; then
    printf '  %-8s %s\n' "$tool" "$reason"
    todo+=("$tool")
  else
    printf '  %-8s ok (%s)\n' "$tool" "$(command -v "$tool")"
  fi
done

if [[ ${#todo[@]} -eq 0 ]]; then
  note "all tools are installed"
  exit 0
fi
$CHECK_ONLY && exit 1

if ! $ASSUME_YES; then
  reply=""
  read -r -p "Install ${todo[*]} into ${BIN_DIR}? [Y/n] " reply || true
  [[ -z "$reply" || "$reply" =~ ^[Yy]$ ]] || die "aborted"
fi

mkdir -p "$BIN_DIR"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
for tool in "${todo[@]}"; do
  "install_${tool}"
done

case ":${ORIGINAL_PATH}:" in
  *":${BIN_DIR}:"*) ;;
  *)
    note "deploy.sh finds tools in ${BIN_DIR} on its own. To use them in your shell too, add this to ~/.zshrc or ~/.bashrc:"
    printf '    export PATH="%s:$PATH"\n' "$BIN_DIR"
    ;;
esac

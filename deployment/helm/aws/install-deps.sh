#!/usr/bin/env bash
# Install the command-line tools deploy.sh needs: aws (v2), eksctl, kubectl, helm.
# macOS and Linux, amd64 and arm64. Windows: run inside WSL2.
# Installs only what is missing or too old, into ~/.pipeshub/bin, without sudo.
# Downloads are official releases; kubectl, helm and eksctl are checked against
# their published SHA-256 sums, and the macOS AWS CLI package against its signature.
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

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

verify_sha256() { # file expected
  local got
  got="$(sha256_of "$1")"
  [[ "$got" == "$2" ]] || die "checksum mismatch for $(basename "$1"): expected $2, got $got"
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

install_kubectl() {
  local minor version url
  minor="$(k8s_minor)"
  if [[ -n "$minor" ]]; then
    version="$(curl -fsSL "https://dl.k8s.io/release/stable-${minor}.txt")"
  else
    version="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
  fi
  url="https://dl.k8s.io/release/${version}/bin/${OS}/${ARCH}/kubectl"
  curl -fsSL -o "$TMP/kubectl" "$url"
  verify_sha256 "$TMP/kubectl" "$(curl -fsSL "${url}.sha256" | awk '{print $1}')"
  install -m 0755 "$TMP/kubectl" "$BIN_DIR/kubectl"
  note "kubectl ${version}"
}

install_helm() {
  local version file
  version="$(curl -fsSL https://get.helm.sh/helm-latest-version)"
  [[ "$version" == v* ]] || die "could not read the latest Helm version"
  file="helm-${version}-${OS}-${ARCH}.tar.gz"
  curl -fsSL -o "$TMP/$file" "https://get.helm.sh/${file}"
  verify_sha256 "$TMP/$file" "$(curl -fsSL "https://get.helm.sh/${file}.sha256sum" | awk '{print $1}')"
  tar -xzf "$TMP/$file" -C "$TMP"
  install -m 0755 "$TMP/${OS}-${ARCH}/helm" "$BIN_DIR/helm"
  note "helm ${version}"
}

install_eksctl() {
  local platform file base
  platform="$(uname -s)_${ARCH}"
  file="eksctl_${platform}.tar.gz"
  base="https://github.com/eksctl-io/eksctl/releases/latest/download"
  curl -fsSL -o "$TMP/$file" "${base}/${file}"
  verify_sha256 "$TMP/$file" "$(curl -fsSL "${base}/eksctl_checksums.txt" | awk -v f="$file" '$2 == f {print $1}')"
  tar -xzf "$TMP/$file" -C "$TMP"
  install -m 0755 "$TMP/eksctl" "$BIN_DIR/eksctl"
  note "eksctl $("$BIN_DIR/eksctl" version)"
}

install_aws() {
  if [[ "$OS" == darwin ]]; then
    curl -fsSL -o "$TMP/AWSCLIV2.pkg" https://awscli.amazonaws.com/AWSCLIV2.pkg
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
    curl -fsSL -o "$TMP/awscliv2.zip" "https://awscli.amazonaws.com/awscli-exe-linux-${machine}.zip"
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

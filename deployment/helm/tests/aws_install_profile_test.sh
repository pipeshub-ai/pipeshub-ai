#!/usr/bin/env bash
# ADR-OPS-07 in deployment/helm/aws/install.sh: a new release gets hardened=true, an existing release keeps the value
# it was given (none: the compatibility profile it has run since before the switch), and PIPESHUB_HARDENED decides
# instead. helm is a stub; no cluster and no network.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
INSTALL="$ROOT/deployment/helm/aws/install.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
passed=0
failed=0
check() { # name expected got
  if [[ "$2" == "$3" ]]; then printf '  ok   - %s\n' "$1"; passed=$((passed + 1))
  else printf '  FAIL - %s (expected %s, got %s)\n' "$1" "$2" "$3"; failed=$((failed + 1)); fi
}

awk '$0 ~ /^release_hardened\(\) \{/{g=1} g{print} g && $0 == "}"{exit}' "$INSTALL" >"$TMP/fn.sh"
grep -q '^release_hardened()' "$TMP/fn.sh" || { echo "FAIL - extract release_hardened"; exit 1; }
# shellcheck disable=SC1091
source "$TMP/fn.sh"
die() { echo "DIE $*"; exit 1; }
# shellcheck disable=SC2034 # read by the sourced release_hardened
RELEASE=pipeshub-ai
# shellcheck disable=SC2034
NAMESPACE=pipeshub

# The stub answers `helm status` from RELEASE_EXISTS and `helm get values` with VALUES.
helm() {
  case "$1" in
    status) [[ "${RELEASE_EXISTS:-false}" == true ]] ;;
    get) printf '%s\n' "${VALUES:-}" ;;
    *) return 2 ;;
  esac
}

run() { # RELEASE_EXISTS VALUES PIPESHUB_HARDENED
  RELEASE_EXISTS="$1" VALUES="$2" PIPESHUB_HARDENED="$3" release_hardened
}

check "a new release is hardened" true "$(run false "" "")"
check "an existing release without the value keeps compatibility" false \
  "$(run true $'image:\n  tag: 1.0.0\nsecretManagement:\n  existingSecrets:\n    enabled: true' "")"
check "an existing hardened release stays hardened" true "$(run true $'hardened: true\nimage:\n  tag: 1.0.0' "")"
check "a quoted value reads the same" true "$(run true 'hardened: "true"' "")"
check "an existing release given false keeps false" false "$(run true 'hardened: false' "")"
check "a nested key named hardened is not the switch" false "$(run true $'foo:\n  hardened: true' "")"
check "PIPESHUB_HARDENED=true adopts it on an existing release" true "$(run true "" true)"
check "PIPESHUB_HARDENED=off keeps a new release on compatibility" false "$(run false "" off)"
check "a value that is not a boolean is refused" "DIE" "$(run false "" maybe | head -c3)"

echo "aws_install_profile_test: ${passed} passed, ${failed} failed"
[[ "$failed" -eq 0 ]]

# shellcheck shell=bash
# Sourced by deploy.sh and install.sh. Requires die().
# Turns an account/image reference into Helm values and a pull secret.
# The registry token is read from PIPESHUB_DOCKER_TOKEN and is never printed.

PULL_SECRET_NAME="pipeshub-registry"
export DEFAULT_APP_REPOSITORY="pipeshubai/pipeshub-ai"
export DEFAULT_APP_TAG="0.9.1-slim"

# Sets IMAGE_REPOSITORY, IMAGE_TAG, DOCKER_SERVER, and DOCKER_USERNAME.
# A missing tag uses the public image tag from values-eks.yaml.
parse_image_ref() {
  local ref="$1" username="${2:-}" name tag first rest
  [[ -n "$ref" ]] || die "image name is empty"
  [[ "$ref" != *$'\n'* && "$ref" != *" "* && "$ref" != *$'\t'* ]] || die "invalid image name"
  [[ "$ref" != *@* ]] || die "image digests are not supported; pass account/image:tag"
  name="$ref"
  tag=""
  if [[ "$name" == *:* ]]; then
    [[ "${name%:*}" != *:* ]] || die "invalid image name: ${ref}"
    tag="${name##*:}"
    name="${name%:*}"
  else
    tag="$DEFAULT_APP_TAG"
  fi
  [[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || die "invalid image tag in ${ref}"
  [[ "$name" =~ ^[a-z0-9]([a-z0-9._-]*[a-z0-9])?(/[a-z0-9]([a-z0-9._-]*[a-z0-9])?)+$ ]] \
    || die "image must look like account/image or account/image:tag"
  export IMAGE_REPOSITORY="$name"
  export IMAGE_TAG="$tag"
  first="${name%%/*}"
  if [[ "$first" == *.* ]]; then
    export DOCKER_SERVER="$first"
    rest="${name#*/}"
    export DOCKER_USERNAME="${username:-${rest%%/*}}"
  else
    export DOCKER_SERVER="https://index.docker.io/v1/"
    export DOCKER_USERNAME="${username:-$first}"
  fi
  [[ "$DOCKER_USERNAME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]] \
    || die "pass --docker-username when the token owner is not the account in the image name"
}

# Writes a docker-registry Secret and applies it. Removes the token file either way.
apply_registry_pull_secret() {
  local namespace="$1" tmp status
  [[ -n "$namespace" ]] || die "namespace is required for the registry pull secret"
  [[ -n "${PIPESHUB_DOCKER_TOKEN:-}" ]] || die "Docker token is empty"
  [[ -n "${DOCKER_SERVER:-}" && -n "${DOCKER_USERNAME:-}" ]] || die "registry auth is incomplete"
  command -v python3 >/dev/null 2>&1 || die "python3 is required to store the registry token"
  tmp="$(mktemp)"
  chmod 600 "$tmp"
  trap 'rm -f "$tmp"' EXIT
  if ! PIPESHUB_REGISTRY_SERVER="$DOCKER_SERVER" \
    PIPESHUB_REGISTRY_USERNAME="$DOCKER_USERNAME" \
    python3 - "$tmp" "$namespace" "$PULL_SECRET_NAME" <<'PY'
import base64, json, os, sys
path, namespace, name = sys.argv[1:]
token = os.environ.get("PIPESHUB_DOCKER_TOKEN", "")
user = os.environ.get("PIPESHUB_REGISTRY_USERNAME", "")
server = os.environ.get("PIPESHUB_REGISTRY_SERVER", "")
if not token or not user or not server:
    raise SystemExit(1)
auth = base64.b64encode(f"{user}:{token}".encode()).decode()
config = {"auths": {server: {"username": user, "password": token, "auth": auth}}}
secret = {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": name, "namespace": namespace},
    "type": "kubernetes.io/dockerconfigjson",
    "stringData": {".dockerconfigjson": json.dumps(config)},
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(secret, handle)
PY
  then
    rm -f "$tmp"
    trap - EXIT
    die "could not write the registry pull secret"
  fi
  unset PIPESHUB_DOCKER_TOKEN PIPESHUB_REGISTRY_SERVER PIPESHUB_REGISTRY_USERNAME
  status=0
  kubectl apply -f "$tmp" >/dev/null || status=$?
  rm -f "$tmp"
  trap - EXIT
  [[ "$status" -eq 0 ]] || die "could not create registry pull secret ${PULL_SECRET_NAME}"
}

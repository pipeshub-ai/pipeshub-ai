#!/usr/bin/env bash
# Install PipesHub on an existing EKS cluster. Does not create the cluster.
# PIPESHUB_TOPOLOGY=single-az keeps every pod in one zone of a cluster you
# already run. See docs/deployment/aws-eks.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CHART="${ROOT}/deployment/helm/pipeshub-ai"
NAMESPACE="${NAMESPACE:-pipeshub}"
RELEASE="${RELEASE:-pipeshub-ai}"
SECRET_NAME="${SECRET_NAME:-pipeshub-ai-secrets}"
STORAGE_CLASS="${STORAGE_CLASS:-gp3}"
PIPESHUB_TOPOLOGY="${PIPESHUB_TOPOLOGY:-dedicated}"
PIPESHUB_INGRESS="${PIPESHUB_INGRESS:-true}"

die() { echo "install-eks: $*" >&2; exit 1; }
# shellcheck source=registry-auth.sh
source "${ROOT}/deployment/helm/aws/registry-auth.sh"

export PATH="${PIPESHUB_BIN_DIR:-${PIPESHUB_HOME:-${HOME}/.pipeshub}/bin}:${PATH}"
for tool in aws kubectl helm openssl; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required. Run deployment/helm/aws/install-deps.sh"
done

context="$(kubectl config current-context)"
echo "install-eks: context=${context} namespace=${NAMESPACE} release=${RELEASE}"
echo "install-eks: this will install into the cluster above. Ctrl-C to abort."
sleep 3

case "${PIPESHUB_TOPOLOGY}" in
  dedicated|single-az) ;;
  *) die "PIPESHUB_TOPOLOGY must be dedicated or single-az (got ${PIPESHUB_TOPOLOGY})" ;;
esac
case "${PIPESHUB_INGRESS}" in
  true|false) ;;
  *) die "PIPESHUB_INGRESS must be true or false (got ${PIPESHUB_INGRESS})" ;;
esac

kubectl get storageclass "${STORAGE_CLASS}" >/dev/null 2>&1 || die "StorageClass ${STORAGE_CLASS} is missing. kubectl apply -f deployment/helm/aws/storageclass-gp3.yaml"
binding="$(kubectl get storageclass "${STORAGE_CLASS}" -o jsonpath='{.volumeBindingMode}')"
[[ "${binding}" == WaitForFirstConsumer ]] || die "StorageClass ${STORAGE_CLASS} uses volumeBindingMode ${binding:-<unset>}. It must be WaitForFirstConsumer so each disk is created in the zone of the node its pod lands on."
kubectl get csidriver ebs.csi.aws.com >/dev/null 2>&1 || die "EBS CSI driver is not installed (addon aws-ebs-csi-driver)"
if [[ "${PIPESHUB_INGRESS}" == true ]]; then
  kubectl get deployment -n kube-system aws-load-balancer-controller >/dev/null 2>&1 || die "AWS Load Balancer Controller is not installed"
fi

if [[ "${PIPESHUB_TOPOLOGY}" == single-az ]]; then
  [[ -n "${PIPESHUB_ZONE:-}" ]] || die "PIPESHUB_ZONE is required when PIPESHUB_TOPOLOGY=single-az, for example us-east-1a"
  ready="$(kubectl get nodes -l "topology.kubernetes.io/zone=${PIPESHUB_ZONE}" --no-headers 2>/dev/null | awk '$2 ~ /^Ready/ { n++ } END { print n+0 }')"
  [[ "${ready}" -ge 1 ]] || die "no Ready node in zone ${PIPESHUB_ZONE}"
else
  data_nodes="$(kubectl get nodes -l pipeshub/role=data --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  app_nodes="$(kubectl get nodes -l pipeshub/role=app --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  [[ "${data_nodes}" -ge 4 ]] || die "need at least 4 nodes labeled pipeshub/role=data (found ${data_nodes}). Qdrant and Neo4j each reserve 4 CPU and cannot share a node."
  [[ "${app_nodes}" -ge 2 ]] || die "need at least 2 nodes labeled pipeshub/role=app (found ${app_nodes})"
fi

kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1 || kubectl create namespace "${NAMESPACE}"
kubectl label namespace "${NAMESPACE}" \
  pod-security.kubernetes.io/enforce=privileged \
  pod-security.kubernetes.io/warn=restricted \
  pod-security.kubernetes.io/audit=restricted \
  --overwrite

helm_image_args=()
if [[ -n "${PIPESHUB_IMAGE_REPOSITORY:-}" || -n "${PIPESHUB_DOCKER_TOKEN:-}" ]]; then
  if [[ -z "${PIPESHUB_IMAGE_REPOSITORY:-}" ]]; then
    [[ -n "${PIPESHUB_IMAGE:-}" ]] || die "a Docker token was set but no image. Pass --image account/image[:tag] to deploy.sh"
    parse_image_ref "${PIPESHUB_IMAGE}" "${PIPESHUB_DOCKER_USERNAME:-}"
    PIPESHUB_IMAGE_REPOSITORY="$IMAGE_REPOSITORY"
    PIPESHUB_IMAGE_TAG="$IMAGE_TAG"
  fi
  [[ -n "${PIPESHUB_IMAGE_REPOSITORY:-}" && -n "${PIPESHUB_IMAGE_TAG:-}" ]] || die "image repository and tag are required"
  echo "install-eks: image ${PIPESHUB_IMAGE_REPOSITORY}:${PIPESHUB_IMAGE_TAG}"
  helm_image_args=(
    --set-string "image.repository=${PIPESHUB_IMAGE_REPOSITORY}"
    --set-string "image.tag=${PIPESHUB_IMAGE_TAG}"
  )
  if [[ -n "${PIPESHUB_DOCKER_TOKEN:-}" ]]; then
    # deploy.sh exports PIPESHUB_DOCKER_*. install.sh run on its own only has
    # the values parse_image_ref just set.
    export DOCKER_SERVER="${PIPESHUB_DOCKER_SERVER:-${DOCKER_SERVER:-}}"
    export DOCKER_USERNAME="${PIPESHUB_DOCKER_USERNAME:-${DOCKER_USERNAME:-}}"
    [[ -n "$DOCKER_SERVER" && -n "$DOCKER_USERNAME" ]] || die "registry server and username are required for the pull secret"
    apply_registry_pull_secret "$NAMESPACE"
    helm_image_args+=(--set "imagePullSecrets[0].name=${PULL_SECRET_NAME}")
  fi
fi

if kubectl get secret -n "${NAMESPACE}" "${SECRET_NAME}" >/dev/null 2>&1; then
  echo "install-eks: reusing secret ${SECRET_NAME}"
else
  echo "install-eks: creating secret ${SECRET_NAME}"
  kubectl create secret generic "${SECRET_NAME}" -n "${NAMESPACE}" \
    --from-literal=secret-key="$(openssl rand -hex 32)" \
    --from-literal=mongodb-username=root \
    --from-literal=mongodb-password="$(openssl rand -hex 24)" \
    --from-literal=redis-password="$(openssl rand -hex 24)" \
    --from-literal=neo4j-password="$(openssl rand -hex 24)" \
    --from-literal=qdrant-api-key="$(openssl rand -hex 24)"
fi

helm dependency build "${CHART}"

: "${FRONTEND_PUBLIC_URL:?Set FRONTEND_PUBLIC_URL, for example https://pipeshub.example.com}"
if [[ "${PIPESHUB_INGRESS}" == true ]]; then
  : "${PIPESHUB_HOST:?Set PIPESHUB_HOST, for example pipeshub.example.com}"
fi

helm_args=(
  upgrade --install "${RELEASE}" "${CHART}"
  --namespace "${NAMESPACE}"
  -f "${CHART}/values-eks.yaml"
)
if [[ "${PIPESHUB_TOPOLOGY}" == single-az ]]; then
  helm_args+=(-f "${CHART}/values-eks-single-az.yaml")
fi
if [[ -n "${PIPESHUB_EXTRA_VALUES:-}" ]]; then
  IFS=':' read -r -a extra_files <<<"${PIPESHUB_EXTRA_VALUES}"
  for extra_file in "${extra_files[@]}"; do
    [[ -n "${extra_file}" ]] || continue
    [[ -f "${extra_file}" ]] || die "PIPESHUB_EXTRA_VALUES file not found: ${extra_file}"
    helm_args+=(-f "${extra_file}")
  done
fi
helm_args+=(
  --set secretManagement.existingSecrets.enabled=true
  --set secretManagement.existingSecrets.appSecretName="${SECRET_NAME}"
  --set "config.frontendPublicUrl=${FRONTEND_PUBLIC_URL}"
  --set "config.allowedOrigins=${ALLOWED_ORIGINS:-${FRONTEND_PUBLIC_URL}}"
)
for storage_key in \
  global.storageClass \
  mongodb.persistence.storageClass \
  redis.persistence.storageClass \
  qdrant.persistence.storageClass \
  neo4j.persistence.storageClass
do
  helm_args+=(--set-string "${storage_key}=${STORAGE_CLASS}")
done
if [[ -n "${PIPESHUB_ZONE:-}" ]]; then
  # A bare dot would make Helm treat topology, kubernetes, and io as nested keys.
  zone_key='topology\.kubernetes\.io/zone'
  for selector in \
    nodeSelector \
    mongodb.builtin.nodeSelector \
    redis.builtin.nodeSelector \
    qdrant.nodeSelector \
    neo4j.nodeSelector
  do
    helm_args+=(--set-string "${selector}.${zone_key}=${PIPESHUB_ZONE}")
  done
fi
if [[ "${PIPESHUB_INGRESS}" == false ]]; then
  helm_args+=(--set ingress.enabled=false)
else
  helm_args+=(
    --set "ingress.hosts[0].host=${PIPESHUB_HOST}"
    --set "ingress.hosts[0].paths[0].path=/"
    --set "ingress.hosts[0].paths[0].pathType=Prefix"
  )
  if [[ -n "${ALB_CERT_ARN:-}" ]]; then
    helm_args+=(--set "ingress.annotations.alb\\.ingress\\.kubernetes\\.io/certificate-arn=${ALB_CERT_ARN}")
  fi
fi
if [[ ${#helm_image_args[@]} -gt 0 ]]; then
  helm_args+=("${helm_image_args[@]}")
fi
helm_args+=(--wait --timeout 30m)

echo "install-eks: waiting up to 30 minutes for pods to become Ready"
progress_stop="$(mktemp -u)"
(
  while [[ ! -f "${progress_stop}" ]]; do
    sleep 20
    [[ -f "${progress_stop}" ]] && break
    echo "install-eks: $(date +%H:%M:%S) pod status"
    kubectl get pods -n "${NAMESPACE}" --no-headers 2>/dev/null \
      | awk '{printf "install-eks:   %-42s %s %s\n", $1, $2, $3}'
  done
) &
progress_pid=$!

set +e
helm "${helm_args[@]}"
helm_status=$?
set -e
touch "${progress_stop}"
kill "${progress_pid}" 2>/dev/null || true
wait "${progress_pid}" 2>/dev/null || true
rm -f "${progress_stop}"
if [[ "${helm_status}" -ne 0 ]]; then
  echo "install-eks: pods not ready:" >&2
  kubectl get pods -n "${NAMESPACE}" -o wide >&2 || true
  notready="$(kubectl get pods -n "${NAMESPACE}" --no-headers 2>/dev/null | awk '$3 != "Completed" && $3 != "Succeeded" && $2 ~ /^0\// {print "pod/"$1}')"
  for pod in ${notready}; do
    echo "install-eks: ${pod}" >&2
    kubectl describe -n "${NAMESPACE}" "${pod}" | awk '/Events:/,0' >&2 || true
  done
  exit "${helm_status}"
fi

# Images up to 0.10.0 look up the caller's role at endpoints.nodejs, which Node
# never writes, and default to port 3000. The chart runs Node on $PORT (3001),
# so every signed-in query request fails with 503 until the key exists.
# By label: deploy/<name> can resolve to a Redis pod, whose labels its selector also matches.
app_pod="$(kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE},app.kubernetes.io/component=main" \
  --field-selector=status.phase=Running -o name | head -1)"
if [[ -z "${app_pod}" ]] \
  || ! kubectl exec -i -n "${NAMESPACE}" "${app_pod}" -c pipeshub-ai -- sh -c 'cd /app/python && python3 -' <<'PY'; then
import asyncio, logging, os
logging.disable(logging.CRITICAL)
from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore

async def main():
    log = logging.getLogger("install-eks")
    config = ConfigurationService(log, EncryptedKeyValueStore(log))
    try:
        # A failed read must not be mistaken for an empty document: the write
        # below would replace every service endpoint with this one.
        endpoints = await config.get_config("/services/endpoints", use_cache=False, raise_on_error=True) or {}
        if not isinstance(endpoints, dict):
            raise SystemExit("/services/endpoints is not a JSON object")
        if (endpoints.get("nodejs") or {}).get("endpoint"):
            return
        endpoints["nodejs"] = {"endpoint": f"http://localhost:{os.environ.get('PORT', '3000')}"}
        if not await config.set_config("/services/endpoints", endpoints):
            raise SystemExit("could not save /services/endpoints")
        print("install-eks: set the Node endpoint used for sign-in checks")
    finally:
        await config.close()

asyncio.run(main())
PY
  echo "install-eks: could not set endpoints.nodejs. Signed-in requests may fail with 503; see docs/deployment/aws-eks.md" >&2
fi

echo "install-eks: pods"
kubectl get pods -n "${NAMESPACE}"
if [[ "${PIPESHUB_INGRESS}" == true ]]; then
  echo "install-eks: ingress"
  kubectl get ingress -n "${NAMESPACE}"
  echo "install-eks: point DNS at the ALB hostname, then open ${FRONTEND_PUBLIC_URL}"
else
  echo "install-eks: ingress is disabled; publish ${FRONTEND_PUBLIC_URL} with your own ingress"
fi
echo "install-eks: set blob storage to S3 before anyone uploads a file. See docs/deployment/aws-eks.md"

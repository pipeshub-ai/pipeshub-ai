#!/usr/bin/env bash
# Install PipesHub on an existing EKS cluster. Does not create the cluster.
# See docs/deployment/aws-eks.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CHART="${ROOT}/deployment/helm/pipeshub-ai"
NAMESPACE="${NAMESPACE:-pipeshub}"
RELEASE="${RELEASE:-pipeshub-ai}"
SECRET_NAME="${SECRET_NAME:-pipeshub-ai-secrets}"

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

kubectl get storageclass gp3 >/dev/null 2>&1 || die "StorageClass gp3 is missing. kubectl apply -f deployment/helm/aws/storageclass-gp3.yaml"
kubectl get csidriver ebs.csi.aws.com >/dev/null 2>&1 || die "EBS CSI driver is not installed (addon aws-ebs-csi-driver)"
kubectl get deployment -n kube-system aws-load-balancer-controller >/dev/null 2>&1 || die "AWS Load Balancer Controller is not installed"

data_nodes="$(kubectl get nodes -l pipeshub/role=data --no-headers 2>/dev/null | wc -l | tr -d ' ')"
app_nodes="$(kubectl get nodes -l pipeshub/role=app --no-headers 2>/dev/null | wc -l | tr -d ' ')"
[[ "${data_nodes}" -ge 4 ]] || die "need at least 4 nodes labeled pipeshub/role=data (found ${data_nodes}). Qdrant and Neo4j each reserve 4 CPU and cannot share a node."
[[ "${app_nodes}" -ge 2 ]] || die "need at least 2 nodes labeled pipeshub/role=app (found ${app_nodes})"

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
helm upgrade --install "${RELEASE}" "${CHART}" \
  --namespace "${NAMESPACE}" \
  -f "${CHART}/values-eks.yaml" \
  --set secretManagement.existingSecrets.enabled=true \
  --set secretManagement.existingSecrets.appSecretName="${SECRET_NAME}" \
  --set config.frontendPublicUrl="${FRONTEND_PUBLIC_URL:?Set FRONTEND_PUBLIC_URL, for example https://pipeshub.example.com}" \
  --set config.allowedOrigins="${ALLOWED_ORIGINS:-${FRONTEND_PUBLIC_URL}}" \
  --set "ingress.hosts[0].host=${PIPESHUB_HOST:?Set PIPESHUB_HOST, for example pipeshub.example.com}" \
  --set "ingress.hosts[0].paths[0].path=/" \
  --set "ingress.hosts[0].paths[0].pathType=Prefix" \
  ${ALB_CERT_ARN:+--set "ingress.annotations.alb\.ingress\.kubernetes\.io/certificate-arn=${ALB_CERT_ARN}"} \
  ${helm_image_args[@]+"${helm_image_args[@]}"} \
  --wait --timeout 30m
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

echo "install-eks: pods"
kubectl get pods -n "${NAMESPACE}"
echo "install-eks: ingress"
kubectl get ingress -n "${NAMESPACE}"
echo "install-eks: point DNS at the ALB hostname, then open ${FRONTEND_PUBLIC_URL}"
echo "install-eks: set blob storage to S3 before anyone uploads a file. See docs/deployment/aws-eks.md"

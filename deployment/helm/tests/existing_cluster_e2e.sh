#!/usr/bin/env bash
# Live test of deploy.sh --existing-cluster. Builds a stand-in for a customer
# cluster: one untainted node group in one zone, plus the add-ons, StorageClass
# and load balancer controller that mode expects. Creates billable resources.
#
#   deployment/helm/tests/existing_cluster_e2e.sh up      # cluster + prerequisites, about 30 minutes
#   deployment/helm/tests/existing_cluster_e2e.sh deploy  # deploy.sh --existing-cluster
#   deployment/helm/tests/existing_cluster_e2e.sh down    # release, cluster, bucket, disks, policies
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
AWS_DIR="${ROOT}/deployment/helm/aws"

CLUSTER="${CLUSTER:-pipeshub-existing-test}"
REGION="${AWS_REGION:-us-east-1}"
ZONE="${ZONE:-us-east-1a}"
# EKS and the internal ALB both need subnets in a second zone. No nodes go there.
SECOND_ZONE="${SECOND_ZONE:-us-east-1b}"
DOMAIN="${DOMAIN:-sandbox.pipeshub.com}"
K8S_VERSION="${K8S_VERSION:-$(sed -n 's/^  version: "\(.*\)"$/\1/p' "${AWS_DIR}/cluster.yaml")}"

export AWS_PAGER="" AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
step() { printf '\n==> %s\n' "$*"; }

up() {
  local config account policy_arn lbc_version vpc_id
  config="$(mktemp)"
  cat >"$config" <<EOF
apiVersion: eksctl.io/v1alpha5
kind: ClusterConfig
metadata:
  name: ${CLUSTER}
  region: ${REGION}
  version: "${K8S_VERSION}"
availabilityZones: ["${ZONE}", "${SECOND_ZONE}"]
autoModeConfig:
  enabled: false
iam:
  withOIDC: true
vpc:
  nat:
    gateway: Single
addons:
  - name: vpc-cni
    version: latest
    configurationValues: |-
      enableNetworkPolicy: "true"
    resolveConflicts: overwrite
  - name: coredns
    version: latest
  - name: kube-proxy
    version: latest
  - name: eks-pod-identity-agent
    version: latest
  - name: aws-ebs-csi-driver
    version: latest
    useDefaultPodIdentityAssociations: true
managedNodeGroups:
  - name: workers
    instanceType: m7i.2xlarge
    desiredCapacity: 5
    minSize: 5
    maxSize: 6
    volumeSize: 100
    volumeType: gp3
    volumeEncrypted: true
    privateNetworking: true
    disableIMDSv1: true
    disablePodIMDS: true
    availabilityZones: ["${ZONE}"]
EOF
  step "Cluster ${CLUSTER} (${ZONE}, 5 x m7i.2xlarge)"
  if aws eks describe-cluster --name "$CLUSTER" >/dev/null 2>&1; then
    echo "    exists"
    aws eks update-kubeconfig --name "$CLUSTER" >/dev/null
  else
    eksctl create cluster -f "$config"
  fi
  rm -f "$config"

  step "StorageClass gp3"
  kubectl apply -f "${AWS_DIR}/storageclass-gp3.yaml"

  step "AWS Load Balancer Controller"
  account="$(aws sts get-caller-identity --query Account --output text)"
  policy_arn="arn:aws:iam::${account}:policy/PipesHub-${CLUSTER}-LBController"
  helm repo add eks https://aws.github.io/eks-charts >/dev/null 2>&1 || true
  helm repo update eks >/dev/null
  lbc_version="$(helm show chart eks/aws-load-balancer-controller | sed -n 's/^appVersion: *//p' | tr -d '"')"
  if ! aws iam get-policy --policy-arn "$policy_arn" >/dev/null 2>&1; then
    curl -fsSL -o "${TMPDIR:-/tmp}/lbc-iam-policy.json" \
      "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/${lbc_version}/docs/install/iam_policy.json"
    aws iam create-policy --policy-name "PipesHub-${CLUSTER}-LBController" \
      --policy-document "file://${TMPDIR:-/tmp}/lbc-iam-policy.json" >/dev/null
  fi
  eksctl create iamserviceaccount --cluster "$CLUSTER" --region "$REGION" \
    --namespace kube-system --name aws-load-balancer-controller \
    --role-name "PipesHub-${CLUSTER}-LBController" --attach-policy-arn "$policy_arn" \
    --override-existing-serviceaccounts --approve
  vpc_id="$(aws eks describe-cluster --name "$CLUSTER" --query cluster.resourcesVpcConfig.vpcId --output text)"
  helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller -n kube-system \
    --set clusterName="$CLUSTER" --set region="$REGION" --set vpcId="$vpc_id" \
    --set serviceAccount.create=false --set serviceAccount.name=aws-load-balancer-controller \
    --wait --timeout 10m >/dev/null
  kubectl get nodes -L topology.kubernetes.io/zone

  printf '\nReady. Next: %s deploy\n' "$0"
}

deploy() {
  "${AWS_DIR}/deploy.sh" --domain "$DOMAIN" --region "$REGION" \
    --existing-cluster --cluster "$CLUSTER" --zone "$ZONE" "$@"
  step "Checks"
  kubectl get pods -n pipeshub -o wide
  kubectl get pods -n pipeshub -o jsonpath='{range .items[*]}{.spec.nodeName}{"\n"}{end}' | sort -u \
    | xargs -I{} kubectl get node {} -o jsonpath='{.metadata.name}{"  "}{.metadata.labels.topology\.kubernetes\.io/zone}{"\n"}'
  aws eks list-pod-identity-associations --cluster-name "$CLUSTER" --namespace pipeshub --output table
  echo "The load balancer is internal. From this machine, open the UI with:"
  echo "  kubectl port-forward -n pipeshub svc/pipeshub-ai 3001:3001   # then http://localhost:3001"
}

down() {
  local account
  account="$(aws sts get-caller-identity --query Account --output text)"
  step "Remove the release (keeps the cluster)"
  if aws eks describe-cluster --name "$CLUSTER" >/dev/null 2>&1; then
    "${AWS_DIR}/deploy.sh" --domain "$DOMAIN" --region "$REGION" \
      --existing-cluster --cluster "$CLUSTER" --destroy --yes
    step "Delete cluster ${CLUSTER}"
    eksctl delete cluster --name "$CLUSTER" --region "$REGION" --disable-nodegroup-eviction --wait
  fi
  # With the cluster gone, --purge only deletes this cluster's leftovers: detached
  # disks, snapshots, backup vault, bucket and saved secret-key.
  step "Purge ${CLUSTER} data"
  "${AWS_DIR}/deploy.sh" --domain "$DOMAIN" --region "$REGION" --cluster "$CLUSTER" --purge --yes
  step "IAM policies"
  for name in S3 LBController; do
    aws iam delete-policy --policy-arn "arn:aws:iam::${account}:policy/PipesHub-${CLUSTER}-${name}" 2>/dev/null \
      && echo "    deleted PipesHub-${CLUSTER}-${name}" || echo "    PipesHub-${CLUSTER}-${name} not found or still attached"
  done
}

case "${1:-}" in
  up) up ;;
  deploy) shift; deploy "$@" ;;
  down) down ;;
  *) echo "usage: $0 up|deploy|down" >&2; exit 2 ;;
esac

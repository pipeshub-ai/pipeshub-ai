#!/usr/bin/env bash
# One-command PipesHub deployment on AWS EKS: cluster, storage, load balancer,
# TLS, S3, backups, and the Helm release. Safe to re-run; every step skips
# what already exists. See docs/deployment/aws-eks.md.
#
#   ./deployment/helm/aws/deploy.sh --domain pipeshub.example.com --region us-east-1
#   ./deployment/helm/aws/deploy.sh --domain pipeshub.example.com --region us-east-1 --destroy
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
AWS_DIR="${ROOT}/deployment/helm/aws"

CLUSTER="${CLUSTER:-pipeshub}"
NAMESPACE="${NAMESPACE:-pipeshub}"
RELEASE="${RELEASE:-pipeshub-ai}"
DOMAIN="${DOMAIN:-}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
HOSTED_ZONE_ID="${HOSTED_ZONE_ID:-}"
CERT_ARN="${ALB_CERT_ARN:-}"
KMS_KEY_ARN="${KMS_KEY_ARN:-}"
K8S_VERSION="${K8S_VERSION:-}"
ZONES="${ZONES:-}"
BUCKET="${BUCKET:-}"
IMAGE_REF="${PIPESHUB_IMAGE:-}"
DOCKER_TOKEN="${PIPESHUB_DOCKER_TOKEN:-}"
DOCKER_USERNAME_ARG="${PIPESHUB_DOCKER_USERNAME:-}"
TOKEN_FROM_FLAG=false
USE_CUSTOM_IMAGE=false
USE_PRIVATE_IMAGE=false
BACKUPS=true
ASSUME_YES=false
PURGE=false
ACTION=deploy

usage() {
  cat <<'EOF'
Usage: deploy.sh --domain HOST [options]

Creates the EKS cluster, gp3 storage, load balancer, TLS certificate, S3 bucket,
Helm release, DNS record and daily backups. A new cluster takes about an hour,
mostly waiting for EKS. Re-running the same command skips what already exists
and upgrades the Helm release.

Required for a deploy (--destroy and --render-cluster-config do not need it):
  --domain HOST            Public hostname for PipesHub, e.g. pipeshub.example.com

Options:
  --region REGION          AWS region (default: AWS_REGION or aws configure)
  --cluster NAME           EKS cluster name (default: pipeshub, or CLUSTER)
  --namespace NAME         Kubernetes namespace (default: pipeshub, or NAMESPACE)
  --release NAME           Helm release name (default: pipeshub-ai, or RELEASE)
  --hosted-zone-id ID      Route 53 public zone for DNS and certificate validation
                           (default: found automatically from --domain)
  --cert-arn ARN           Use this ACM certificate instead of requesting one
  --kms-key-arn ARN        Use this KMS key for Kubernetes Secrets
  --k8s-version X.Y        Kubernetes version (default: EKS default version)
  --zones a,b,c            Three availability zones (default: first three that offer m7i)
  --bucket NAME            S3 bucket for files (default: pipeshub-<cluster>-<account>-<region>)
  --image ACCOUNT/IMAGE[:TAG]
                           pipeshub-ai image. With no tag, uses 0.9.0-slim.
                           A private image also needs a Docker token. Omit this
                           to use the public image pipeshubai/pipeshub-ai:0.9.0-slim
  --docker-username USER   Owner of the Docker token (default: the account in --image)
  --docker-token TOKEN     Registry token. Prefer PIPESHUB_DOCKER_TOKEN so the
                           token is not saved in shell history. With no token,
                           the public image is used
  --no-backups             Skip the AWS Backup plan
  --render-cluster-config  Print the eksctl config and exit. Needs --region,
                           --zones, --kms-key-arn and --k8s-version; makes no AWS calls
  --destroy                Remove the release and the cluster. Keeps S3, EBS volumes,
                           backups, KMS key and the saved secret-key
  --purge                  With --destroy, also delete this cluster's database disks,
                           snapshots, S3 bucket, backup vault and saved secret-key.
                           Implies --destroy. The KMS key is kept
  -y, --yes                Do not ask for confirmation
  -h, --help               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    --cluster) CLUSTER="$2"; shift 2 ;;
    --namespace) NAMESPACE="$2"; shift 2 ;;
    --release) RELEASE="$2"; shift 2 ;;
    --hosted-zone-id) HOSTED_ZONE_ID="${2#/hostedzone/}"; shift 2 ;;
    --cert-arn) CERT_ARN="$2"; shift 2 ;;
    --kms-key-arn) KMS_KEY_ARN="$2"; shift 2 ;;
    --k8s-version) K8S_VERSION="$2"; shift 2 ;;
    --zones) ZONES="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --image) IMAGE_REF="$2"; shift 2 ;;
    --docker-username) DOCKER_USERNAME_ARG="$2"; shift 2 ;;
    --docker-token) DOCKER_TOKEN="$2"; TOKEN_FROM_FLAG=true; shift 2 ;;
    --no-backups) BACKUPS=false; shift ;;
    --render-cluster-config) ACTION=render; shift ;;
    --destroy) ACTION=destroy; shift ;;
    --purge) PURGE=true; shift ;;
    -y|--yes) ASSUME_YES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; echo "deploy-eks: unknown option $1" >&2; exit 2 ;;
  esac
done

if $PURGE; then
  [[ "$ACTION" == render ]] && { usage >&2; echo "deploy-eks: --purge cannot be combined with --render-cluster-config" >&2; exit 2; }
  ACTION=destroy
fi

if [ -t 1 ]; then C_CYAN=$'\033[36m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_RESET=$'\033[0m'
else C_CYAN=""; C_RED=""; C_GREEN=""; C_RESET=""; fi
step() { printf '\n%s==>%s %s\n' "$C_CYAN" "$C_RESET" "$*"; }
note() { printf '    %s\n' "$*"; }
die() { printf '%sdeploy-eks:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }
# shellcheck source=registry-auth.sh
source "${AWS_DIR}/registry-auth.sh"
on_error() { # exit code, line, command
  printf '%sdeploy-eks:%s failed (exit %s) at line %s: %s\n' "$C_RED" "$C_RESET" "$1" "$2" "$3" >&2
  printf '    Fix the cause and re-run the same command; finished steps are skipped.\n' >&2
}
trap 'on_error $? $LINENO "$BASH_COMMAND"' ERR

[[ "$CLUSTER" =~ ^[a-zA-Z][a-zA-Z0-9-]{0,99}$ ]] || die "invalid cluster name: $CLUSTER"

render_cluster_config() {
  local zones_json
  zones_json="[\"${ZONES//,/\", \"}\"]"
  sed \
    -e "s|\[\"us-east-1a\", \"us-east-1b\", \"us-east-1c\"\]|${zones_json}|" \
    -e "s|region: us-east-1\$|region: ${REGION}|" \
    -e "s|value: us-east-1\$|value: ${REGION}|" \
    -e "s|^  name: pipeshub\$|  name: ${CLUSTER}|" \
    -e "s|^  version: \".*\"\$|  version: \"${K8S_VERSION}\"|" \
    -e "s|keyARN: .*|keyARN: ${KMS_KEY_ARN}|" \
    "${AWS_DIR}/cluster.yaml"
}

if [[ "$ACTION" == render ]]; then
  [[ -n "$REGION" && -n "$ZONES" && -n "$KMS_KEY_ARN" && -n "$K8S_VERSION" ]] \
    || die "--render-cluster-config needs --region, --zones, --kms-key-arn and --k8s-version"
  render_cluster_config
  exit 0
fi

# Reject a deploy before installing tools or calling AWS.
if [[ "$ACTION" == deploy ]]; then
  [[ -n "$DOMAIN" ]] || { usage >&2; die "--domain is required"; }
  [[ "$DOMAIN" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]] || die "invalid domain: $DOMAIN"
  if [[ -n "$IMAGE_REF" ]]; then
    parse_image_ref "$IMAGE_REF" "$DOCKER_USERNAME_ARG"
    USE_CUSTOM_IMAGE=true
    [[ -n "$DOCKER_TOKEN" ]] && USE_PRIVATE_IMAGE=true
  elif [[ -n "$DOCKER_TOKEN" ]]; then
    die "a Docker token was set but no image. Pass --image account/image[:tag]"
  fi
fi

export PATH="${PIPESHUB_BIN_DIR:-${PIPESHUB_HOME:-${HOME}/.pipeshub}/bin}:${PATH}"
if ! K8S_VERSION="$K8S_VERSION" "${AWS_DIR}/install-deps.sh" --check >/dev/null 2>&1; then
  step "Command-line tools"
  install_args=()
  $ASSUME_YES && install_args+=(--yes)
  K8S_VERSION="$K8S_VERSION" "${AWS_DIR}/install-deps.sh" ${install_args[@]+"${install_args[@]}"}
fi

export AWS_PAGER=""
[[ -n "$REGION" ]] || REGION="$(aws configure get region 2>/dev/null || true)"
[[ -n "$REGION" ]] || die "set --region or AWS_REGION"
export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)" \
  || die "AWS credentials are not configured (aws sts get-caller-identity failed)"

STATE_DIR="${PIPESHUB_STATE_DIR:-${HOME}/.pipeshub/eks/${ACCOUNT_ID}-${REGION}-${CLUSTER}}"
mkdir -p "$STATE_DIR"
CLUSTER_CONFIG="${STATE_DIR}/cluster.yaml"

confirm() {
  $ASSUME_YES && return 0
  local reply=""
  read -r -p "$1 [y/N] " reply || true
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted (pass --yes to skip this prompt)"
}

cluster_exists() {
  aws eks describe-cluster --name "$CLUSTER" --query cluster.status --output text >/dev/null 2>&1
}

find_hosted_zone() {
  local name="$1" id
  while [[ "$name" == *.* ]]; do
    id="$(aws route53 list-hosted-zones-by-name --dns-name "${name}." --max-items 1 \
      --query "HostedZones[?Name=='${name}.' && Config.PrivateZone==\`false\`].Id | [0]" --output text 2>/dev/null || true)"
    if [[ -n "$id" && "$id" != None ]]; then
      echo "${id#/hostedzone/}"
      return 0
    fi
    name="${name#*.}"
  done
  return 1
}

route53_upsert() { # zone, json change batch
  local change_id
  change_id="$(aws route53 change-resource-record-sets --hosted-zone-id "$1" \
    --change-batch "$2" --query ChangeInfo.Id --output text)"
  aws route53 wait resource-record-sets-changed --id "$change_id"
}

# Finishes add-ons after a partial create. CREATE_FAILED ones are deleted first.
# If eksctl already made the EBS CSI Pod Identity role, create that add-on through
# the AWS API so CloudFormation is not asked to create the same stack twice.
ensure_addons() { # cluster config
  local name status stack role
  for name in $(awk '/^addons:/ {m = 1; next} m && /^  - name:/ {print $3}' "$1"); do
    status="$(aws eks describe-addon --cluster-name "$CLUSTER" --addon-name "$name" \
      --query addon.status --output text 2>/dev/null || true)"
    if [[ "$status" == CREATE_FAILED || "$status" == DEGRADED ]]; then
      note "add-on ${name} is ${status}; deleting it so it can be created again"
      aws eks delete-addon --cluster-name "$CLUSTER" --addon-name "$name" >/dev/null
      aws eks wait addon-deleted --cluster-name "$CLUSTER" --addon-name "$name"
      status=""
    fi
    [[ -z "$status" || "$status" == None ]] || continue
    if [[ "$name" == aws-ebs-csi-driver ]]; then
      stack="eksctl-${CLUSTER}-addon-aws-ebs-csi-driver-podidentityrole-ebs-csi-controller-sa"
      role="$(aws cloudformation describe-stacks --stack-name "$stack" \
        --query 'Stacks[0].Outputs[0].OutputValue' --output text 2>/dev/null || true)"
      if [[ -n "$role" && "$role" != None ]]; then
        note "creating aws-ebs-csi-driver with the existing Pod Identity role"
        aws eks create-addon --cluster-name "$CLUSTER" --addon-name aws-ebs-csi-driver \
          --pod-identity-associations "serviceAccount=ebs-csi-controller-sa,roleArn=${role}" \
          --resolve-conflicts OVERWRITE >/dev/null
        aws eks wait addon-active --cluster-name "$CLUSTER" --addon-name aws-ebs-csi-driver
        continue
      fi
    fi
  done
  eksctl create addon --config-file "$1"
}

ensure_iam_policy() { # name, document file -> prints ARN
  local arn="arn:aws:iam::${ACCOUNT_ID}:policy/$1" old
  if aws iam get-policy --policy-arn "$arn" >/dev/null 2>&1; then
    # A policy keeps at most five versions; drop the oldest non-default one first.
    old="$(aws iam list-policy-versions --policy-arn "$arn" \
      --query 'sort_by(Versions[?IsDefaultVersion==`false`], &CreateDate)[0].VersionId' --output text)"
    if [[ "$(aws iam list-policy-versions --policy-arn "$arn" --query 'length(Versions)' --output text)" -ge 5 ]]; then
      aws iam delete-policy-version --policy-arn "$arn" --version-id "$old"
    fi
    aws iam create-policy-version --policy-arn "$arn" --policy-document "file://$2" --set-as-default >/dev/null
  else
    aws iam create-policy --policy-name "$1" --policy-document "file://$2" >/dev/null
  fi
  echo "$arn"
}

# On-demand EC2 limit for A, C, D, H, I, M, R, T and Z instances, counted in vCPUs.
STANDARD_VCPU_QUOTA=L-1216C47A

# Over the limit, EKS creates the node group but its instances never launch and
# eksctl waits until it times out, so check before creating anything.
check_vcpu_quota() { # cluster config
  local need=0 used quota type count vcpus want pending
  while read -r type count; do
    vcpus="$(aws ec2 describe-instance-types --instance-types "$type" \
      --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text)"
    need=$(( need + vcpus * count ))
  done < <(awk '/instanceType:/ {t = $2} /desiredCapacity:/ {print t, $2}' "$1")
  quota="$(aws service-quotas get-service-quota --service-code ec2 --quota-code "$STANDARD_VCPU_QUOTA" \
    --query Quota.Value --output text)"
  quota="${quota%.*}"
  used="$(aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running \
    --query 'Reservations[].Instances[].[InstanceType,CpuOptions.CoreCount,CpuOptions.ThreadsPerCore]' --output text \
    | awk 'tolower(substr($1, 1, 1)) ~ /[acdhimrtz]/ {v += $2 * $3} END {print v + 0}')"
  note "vCPUs: this cluster needs ${need}, the account already runs ${used}, the limit is ${quota}"
  (( used + need <= quota )) && return 0

  note "That is over the EC2 limit, so some nodes would never start."
  pending="$(aws service-quotas list-requested-service-quota-change-history-by-quota \
    --service-code ec2 --quota-code "$STANDARD_VCPU_QUOTA" \
    --query "RequestedQuotas[?Status=='PENDING' || Status=='CASE_OPENED'] | [0].[Id,DesiredValue]" --output text)"
  if [[ -n "$pending" && "$pending" != None* ]]; then
    read -r pending want <<<"$pending"
    die "an increase to ${want%.*} vCPUs is already requested (id ${pending}). Re-run when this shows APPROVED: aws service-quotas get-requested-service-quota-change --request-id ${pending} --query RequestedQuota.Status"
  fi
  want=$(( (used + need + 31) / 32 * 32 ))
  confirm "Request an increase of the limit to ${want} vCPUs now?"
  pending="$(aws service-quotas request-service-quota-increase --service-code ec2 --quota-code "$STANDARD_VCPU_QUOTA" \
    --desired-value "$want" --query RequestedQuota.Id --output text)"
  die "requested ${want} vCPUs (id ${pending}). AWS usually answers within minutes to a few hours. Re-run when this shows APPROVED: aws service-quotas get-requested-service-quota-change --request-id ${pending} --query RequestedQuota.Status"
}

# Only resources this deploy created for this cluster. A volume must be detached
# and carry both the cluster tag and pipeshub-backup=true. The S3 name must
# contain the cluster name, so a shared bucket cannot be removed by accident.
purge_cluster_data() {
  cluster_exists && die "cluster ${CLUSTER} still exists, so its disks may still be attached. Re-run --purge after it is gone."
  [[ -n "$BUCKET" ]] || BUCKET="$(echo "pipeshub-${CLUSTER}-${ACCOUNT_ID}-${REGION}" | tr '[:upper:]' '[:lower:]' | cut -c1-63)"
  [[ "$BUCKET" == *"${CLUSTER}"* ]] \
    || die "refusing to delete s3://${BUCKET}: the name does not contain the cluster name ${CLUSTER}"

  step "Database disks for ${CLUSTER}"
  local rows id state skipped=false
  rows="$(aws ec2 describe-volumes \
    --filters "Name=tag:kubernetes.io/cluster/${CLUSTER},Values=owned" "Name=tag:pipeshub-backup,Values=true" \
    --query 'Volumes[].[VolumeId,State]' --output text)"
  if [[ -z "$rows" || "$rows" == None ]]; then
    note "none"
  else
    while read -r id state; do
      [[ -n "$id" && "$id" != None ]] || continue
      if [[ "$state" != available ]]; then
        note "left ${id} attached (state ${state})"
        skipped=true
        continue
      fi
      aws ec2 delete-volume --volume-id "$id"
      note "deleted volume ${id}"
    done <<<"$rows"
  fi
  # Snapshots and the bucket are the copies of these disks. Do not delete them
  # while a disk is still attached; the next run removes them once the disk is free.
  $skipped && die "some database disks are still attached. Re-run the same command when they are available."

  step "Snapshots for ${CLUSTER}"
  local snaps snap
  snaps="$(aws ec2 describe-snapshots --owner-ids self \
    --filters "Name=tag:kubernetes.io/cluster/${CLUSTER},Values=owned" "Name=tag:pipeshub-backup,Values=true" \
    --query 'Snapshots[].SnapshotId' --output text)"
  if [[ -z "$snaps" || "$snaps" == None ]]; then
    note "none"
  else
    for snap in $snaps; do
      # AWS Backup owns its snapshots. Those are removed with the recovery points below.
      # trap - ERR: set -E would otherwise print a failure for this expected refusal.
      if ! err="$(trap - ERR; aws ec2 delete-snapshot --snapshot-id "$snap" 2>&1)"; then
        if grep -q "managed by the AWS Backup" <<<"$err"; then
          note "left ${snap} for the backup vault"
          continue
        fi
        printf '%s\n' "$err" >&2
        return 1
      fi
      note "deleted snapshot ${snap}"
    done
  fi

  step "Backups for ${CLUSTER}"
  local plan selections selection arns arn attempt
  plan="$(aws backup list-backup-plans --query "BackupPlansList[?BackupPlanName=='${CLUSTER}-daily'].BackupPlanId | [0]" --output text 2>/dev/null || true)"
  if [[ -n "$plan" && "$plan" != None ]]; then
    selections="$(aws backup list-backup-selections --backup-plan-id "$plan" --query 'BackupSelectionsList[].SelectionId' --output text)"
    for selection in $selections; do
      [[ "$selection" == None ]] && continue
      aws backup delete-backup-selection --backup-plan-id "$plan" --selection-id "$selection"
    done
    aws backup delete-backup-plan --backup-plan-id "$plan"
    note "deleted backup plan ${CLUSTER}-daily"
  else
    note "no backup plan"
  fi
  if aws backup describe-backup-vault --backup-vault-name "$CLUSTER" >/dev/null 2>&1; then
    for attempt in $(seq 1 10); do
      arns="$(aws backup list-recovery-points-by-backup-vault --backup-vault-name "$CLUSTER" \
        --query 'RecoveryPoints[].RecoveryPointArn' --output text)"
      [[ -z "$arns" || "$arns" == None ]] && break
      for arn in $arns; do
        aws backup delete-recovery-point --backup-vault-name "$CLUSTER" --recovery-point-arn "$arn" || true
      done
      sleep 5
    done
    aws backup delete-backup-vault --backup-vault-name "$CLUSTER"
    note "deleted backup vault ${CLUSTER}"
  else
    note "no backup vault"
  fi

  step "S3 bucket ${BUCKET}"
  if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
    empty_versioned_bucket "$BUCKET"
    aws s3api delete-bucket --bucket "$BUCKET"
    note "deleted s3://${BUCKET}"
  else
    note "no bucket"
  fi

  step "Saved secret-key"
  if aws secretsmanager describe-secret --secret-id "${CLUSTER}/secret-key" >/dev/null 2>&1; then
    aws secretsmanager delete-secret --secret-id "${CLUSTER}/secret-key" --force-delete-without-recovery >/dev/null
    note "deleted ${CLUSTER}/secret-key"
  else
    note "none"
  fi

  note "KMS key alias/${CLUSTER}-eks was kept. The next deploy reuses it."
}

# Versioned buckets keep old copies after a plain recursive delete.
empty_versioned_bucket() {
  local bucket="$1" token="" batch
  command -v python3 >/dev/null 2>&1 || die "python3 is required to empty the versioned bucket ${bucket}"
  while :; do
    if [[ -n "$token" ]]; then
      batch="$(aws s3api list-object-versions --bucket "$bucket" --max-keys 1000 --starting-token "$token" --output json)"
    else
      batch="$(aws s3api list-object-versions --bucket "$bucket" --max-keys 1000 --output json)"
    fi
    token="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("NextToken") or "")' <<<"$batch")"
    python3 -c 'import json,sys; d=json.load(sys.stdin); objs=[{"Key":o["Key"],"VersionId":o["VersionId"]} for o in (d.get("Versions") or [])+(d.get("DeleteMarkers") or [])]; print(json.dumps({"Objects":objs,"Quiet":True}) if objs else "")' <<<"$batch" \
      | while read -r payload; do
          [[ -n "$payload" ]] || continue
          aws s3api delete-objects --bucket "$bucket" --delete "$payload" >/dev/null
        done
    [[ -n "$token" ]] || break
  done
}

destroy() {
  step "Destroy ${CLUSTER} in ${REGION} (account ${ACCOUNT_ID})"
  note "Deletes the Helm release, the load balancer and the cluster."
  if $PURGE; then
    note "Also deletes this cluster's database disks, snapshots, S3 bucket, backup vault and saved secret-key."
    note "Keeps the KMS key alias/${CLUSTER}-eks."
  else
    note "Keeps EBS volumes, the S3 bucket, backups, the KMS key and the saved secret-key."
    note "Add --purge to delete the database disks and the bucket as well."
  fi
  confirm "Continue?"

  if cluster_exists; then
    aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" >/dev/null
    if helm status "$RELEASE" -n "$NAMESPACE" >/dev/null 2>&1; then
      step "Uninstall ${RELEASE}"
      helm uninstall "$RELEASE" -n "$NAMESPACE" --wait --timeout 15m
    fi
    # The ALB is deleted by its controller; wait so it does not block VPC deletion.
    for _ in $(seq 1 30); do
      kubectl get ingress -n "$NAMESPACE" --no-headers 2>/dev/null | grep -q . || break
      sleep 10
    done
    kubectl delete pvc -n "$NAMESPACE" --all --wait=false >/dev/null 2>&1 || true

    step "Delete cluster ${CLUSTER} (about 15 minutes)"
    if [[ -f "$CLUSTER_CONFIG" ]]; then
      eksctl delete cluster -f "$CLUSTER_CONFIG" --disable-nodegroup-eviction --wait
    else
      eksctl delete cluster --name "$CLUSTER" --region "$REGION" --disable-nodegroup-eviction --wait
    fi
  else
    note "cluster ${CLUSTER} not found"
  fi

  if [[ -n "$DOMAIN" ]]; then
    [[ -n "$HOSTED_ZONE_ID" ]] || HOSTED_ZONE_ID="$(find_hosted_zone "$DOMAIN" || true)"
    if [[ -n "$HOSTED_ZONE_ID" ]]; then
      local record
      record="$(aws route53 list-resource-record-sets --hosted-zone-id "$HOSTED_ZONE_ID" \
        --query "ResourceRecordSets[?Name=='${DOMAIN}.' && Type=='A'] | [0]" --output json)"
      if [[ "$record" != null ]]; then
        step "Delete DNS record ${DOMAIN}"
        route53_upsert "$HOSTED_ZONE_ID" "{\"Changes\":[{\"Action\":\"DELETE\",\"ResourceRecordSet\":${record}}]}"
      fi
    fi
  fi

  if $PURGE; then
    purge_cluster_data
    return 0
  fi

  step "Left in the account"
  aws ec2 describe-volumes --filters Name=tag:pipeshub-backup,Values=true \
    --query 'Volumes[].[VolumeId,State,Size,Tags[?Key==`kubernetes.io/created-for/pvc/name`]|[0].Value]' --output table || true
  note "Delete this cluster's disks and bucket with the same command plus --purge."
  note "Also kept: S3 bucket, backup vault ${CLUSTER}, KMS key alias/${CLUSTER}-eks, secret ${CLUSTER}/secret-key"
}

if [[ "$ACTION" == destroy ]]; then
  destroy
  exit 0
fi

FRONTEND_PUBLIC_URL="https://${DOMAIN}"
[[ -n "$BUCKET" ]] || BUCKET="$(echo "pipeshub-${CLUSTER}-${ACCOUNT_ID}-${REGION}" | tr '[:upper:]' '[:lower:]' | cut -c1-63)"
[[ -n "$HOSTED_ZONE_ID" ]] || HOSTED_ZONE_ID="$(find_hosted_zone "$DOMAIN" || true)"

step "Plan"
note "account      ${ACCOUNT_ID}"
note "region       ${REGION}"
note "cluster      ${CLUSTER}"
note "namespace    ${NAMESPACE}"
note "release      ${RELEASE}"
note "url          ${FRONTEND_PUBLIC_URL}"
note "dns          ${HOSTED_ZONE_ID:-manual (no Route 53 zone found for ${DOMAIN})}"
note "s3 bucket    ${BUCKET}"
if $USE_CUSTOM_IMAGE; then
  if $USE_PRIVATE_IMAGE; then
    note "image        ${IMAGE_REPOSITORY}:${IMAGE_TAG} (private)"
    note "pull secret  ${PULL_SECRET_NAME} for ${DOCKER_USERNAME}"
    $TOKEN_FROM_FLAG && note "Docker token was passed as a flag. Next time set PIPESHUB_DOCKER_TOKEN so it is not saved in shell history."
  else
    note "image        ${IMAGE_REPOSITORY}:${IMAGE_TAG}"
  fi
else
  note "image        ${DEFAULT_APP_REPOSITORY}:${DEFAULT_APP_TAG}"
fi
note "backups      ${BACKUPS}"
if cluster_exists; then
  note "Cluster ${CLUSTER} already exists. Finished steps are skipped, and the Helm release is upgraded."
else
  note "Creates 8 EC2 instances, EBS volumes, a load balancer and a NAT gateway. These cost money until you run --destroy."
  note "A new cluster takes about an hour, mostly waiting for EKS."
fi
confirm "Continue?"

step "KMS key for Kubernetes Secrets"
if [[ -z "$KMS_KEY_ARN" ]]; then
  KMS_KEY_ARN="$(aws kms describe-key --key-id "alias/${CLUSTER}-eks" --query KeyMetadata.Arn --output text 2>/dev/null || true)"
  if [[ -z "$KMS_KEY_ARN" || "$KMS_KEY_ARN" == None ]]; then
    KMS_KEY_ARN="$(aws kms create-key --description "PipesHub EKS ${CLUSTER}" --query KeyMetadata.Arn --output text)"
    aws kms create-alias --alias-name "alias/${CLUSTER}-eks" --target-key-id "$KMS_KEY_ARN"
    aws kms enable-key-rotation --key-id "$KMS_KEY_ARN"
    note "created ${KMS_KEY_ARN}"
  else
    note "reusing ${KMS_KEY_ARN}"
  fi
fi

step "EKS cluster ${CLUSTER}"
if cluster_exists; then
  note "exists; skipping create"
  aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" >/dev/null
  [[ -n "$K8S_VERSION" ]] \
    || K8S_VERSION="$(aws eks describe-cluster --name "$CLUSTER" --query cluster.version --output text)"
  if [[ -z "$ZONES" ]]; then
    ZONES="$(aws ec2 describe-instances \
      --filters Name=tag:eks:cluster-name,Values="$CLUSTER" Name=instance-state-name,Values=running \
      --query 'Reservations[].Instances[].Placement.AvailabilityZone' --output text \
      | tr '\t' '\n' | sort -u | paste -sd, -)"
  fi
  [[ -n "$ZONES" ]] || die "could not detect availability zones for ${CLUSTER}; pass --zones"
  render_cluster_config >"$CLUSTER_CONFIG"
  note "refreshed ${CLUSTER_CONFIG}"
  for ng in $(awk '/^managedNodeGroups:/ {m = 1; next} m && /^  - name:/ {print $3}' "$CLUSTER_CONFIG"); do
    ng_status="$(aws eks describe-nodegroup --cluster-name "$CLUSTER" --nodegroup-name "$ng" \
      --query nodegroup.status --output text 2>/dev/null || true)"
    if [[ "$ng_status" == CREATE_FAILED ]]; then
      note "node group ${ng} failed to create earlier; deleting it so it can be created again"
      eksctl delete nodegroup --cluster "$CLUSTER" --region "$REGION" --name "$ng" --wait
    fi
  done
  eksctl create nodegroup --config-file "$CLUSTER_CONFIG"
  ensure_addons "$CLUSTER_CONFIG"
else
  if [[ -z "$K8S_VERSION" ]]; then
    K8S_VERSION="$(aws eks describe-cluster-versions --default-only --query 'clusterVersions[0].clusterVersion' --output text 2>/dev/null || true)"
    [[ -n "$K8S_VERSION" && "$K8S_VERSION" != None ]] \
      || K8S_VERSION="$(sed -n 's/^  version: "\(.*\)"$/\1/p' "${AWS_DIR}/cluster.yaml")"
  fi
  if [[ -z "$ZONES" ]]; then
    ZONES="$(aws ec2 describe-instance-type-offerings --location-type availability-zone \
      --filters Name=instance-type,Values=m7i.2xlarge --query 'InstanceTypeOfferings[].Location' --output text \
      | tr '\t' '\n' | sort | head -3 | paste -sd, -)"
  fi
  [[ "$(tr ',' '\n' <<<"$ZONES" | grep -c .)" -eq 3 ]] || die "need three availability zones with m7i.2xlarge in ${REGION}, found: ${ZONES}"
  render_cluster_config >"$CLUSTER_CONFIG"
  note "kubernetes ${K8S_VERSION}, zones ${ZONES}"
  note "config saved to ${CLUSTER_CONFIG}"
  check_vcpu_quota "$CLUSTER_CONFIG"
  note "this takes 20 to 30 minutes"
  eksctl create cluster -f "$CLUSTER_CONFIG"
fi
kubectl get nodes -L pipeshub/role --no-headers | awk '{print "    " $1 "  " $2 "  " $NF}'

step "Storage class gp3"
if kubectl get storageclass gp3 >/dev/null 2>&1; then
  note "exists"
else
  kubectl apply -f "${AWS_DIR}/storageclass-gp3.yaml"
fi

step "AWS Load Balancer Controller"
helm repo add eks https://aws.github.io/eks-charts >/dev/null 2>&1 || true
helm repo update eks >/dev/null
LBC_VERSION="$(helm show chart eks/aws-load-balancer-controller | sed -n 's/^appVersion: *//p' | tr -d '"')"
[[ -n "$LBC_VERSION" ]] || die "could not read the controller version from the eks chart repo"
curl -fsSL -o "${STATE_DIR}/lbc-iam-policy.json" \
  "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/${LBC_VERSION}/docs/install/iam_policy.json"
LBC_POLICY_ARN="$(ensure_iam_policy "PipesHub-${CLUSTER}-LBController" "${STATE_DIR}/lbc-iam-policy.json")"
eksctl create iamserviceaccount \
  --cluster "$CLUSTER" --region "$REGION" \
  --namespace kube-system --name aws-load-balancer-controller \
  --role-name "PipesHub-${CLUSTER}-LBController" \
  --attach-policy-arn "$LBC_POLICY_ARN" \
  --override-existing-serviceaccounts --approve
VPC_ID="$(aws eks describe-cluster --name "$CLUSTER" --query cluster.resourcesVpcConfig.vpcId --output text)"
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName="$CLUSTER" \
  --set region="$REGION" \
  --set vpcId="$VPC_ID" \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --wait --timeout 10m >/dev/null
# Helm rewrites the webhook CA in the TLS secret. Running pods keep the old
# certificate, and the API server then rejects every Ingress update.
kubectl rollout restart deployment/aws-load-balancer-controller -n kube-system >/dev/null
kubectl rollout status deployment/aws-load-balancer-controller -n kube-system --timeout 5m >/dev/null
note "controller ${LBC_VERSION} ready"

step "TLS certificate for ${DOMAIN}"
if [[ -z "$CERT_ARN" ]]; then
  CERT_ARN="$(aws acm list-certificates --certificate-statuses ISSUED PENDING_VALIDATION \
    --query "CertificateSummaryList[?DomainName=='${DOMAIN}'].CertificateArn | [0]" --output text)"
  if [[ -z "$CERT_ARN" || "$CERT_ARN" == None ]]; then
    CERT_ARN="$(aws acm request-certificate --domain-name "$DOMAIN" --validation-method DNS \
      --idempotency-token "$(echo "$CLUSTER" | tr -cd '[:alnum:]' | cut -c1-32)" \
      --query CertificateArn --output text)"
    note "requested ${CERT_ARN}"
  else
    note "reusing ${CERT_ARN}"
  fi
fi
if [[ "$(aws acm describe-certificate --certificate-arn "$CERT_ARN" --query Certificate.Status --output text)" != ISSUED ]]; then
  VALIDATION=""
  for _ in $(seq 1 30); do
    VALIDATION="$(aws acm describe-certificate --certificate-arn "$CERT_ARN" \
      --query 'Certificate.DomainValidationOptions[0].ResourceRecord.[Name,Value]' --output text 2>/dev/null || true)"
    [[ -n "$VALIDATION" && "$VALIDATION" != None* ]] && break
    sleep 5
  done
  read -r V_NAME V_VALUE <<<"$VALIDATION"
  [[ -n "${V_NAME:-}" ]] || die "ACM did not return a validation record for ${CERT_ARN}"
  if [[ -n "$HOSTED_ZONE_ID" ]]; then
    route53_upsert "$HOSTED_ZONE_ID" "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"${V_NAME}\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"${V_VALUE}\"}]}}]}"
    note "validation record added to Route 53"
  else
    note "No Route 53 zone for ${DOMAIN}. Add this DNS-only CNAME now (on Cloudflare, turn the proxy off):"
    note "  CNAME  ${V_NAME}  ->  ${V_VALUE}"
  fi
  note "waiting for ACM to issue the certificate (up to 40 minutes)"
  issued=false
  for attempt in $(seq 1 40); do
    if [[ "$(aws acm describe-certificate --certificate-arn "$CERT_ARN" --query Certificate.Status --output text)" == ISSUED ]]; then
      issued=true
      break
    fi
    if [[ -z "$HOSTED_ZONE_ID" && $(( attempt % 2 )) -eq 0 ]]; then
      note "still pending. DNS-only CNAME  ${V_NAME}  ->  ${V_VALUE}"
    fi
    sleep 60
  done
  $issued || die "certificate ${CERT_ARN} is still pending. Add a DNS-only CNAME ${V_NAME} -> ${V_VALUE}, then re-run this command"
fi
note "certificate issued"

step "S3 bucket ${BUCKET} and Pod Identity access"
if aws s3api head-bucket --bucket "$BUCKET" >/dev/null 2>&1; then
  note "bucket exists"
else
  if [[ "$REGION" == us-east-1 ]]; then
    aws s3api create-bucket --bucket "$BUCKET" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
  note "bucket created"
fi
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
cat >"${STATE_DIR}/s3-policy.json" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": "arn:aws:s3:::${BUCKET}"},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": "arn:aws:s3:::${BUCKET}/*"},
    {"Effect": "Allow", "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*", "Condition": {"StringEquals": {"kms:ViaService": "s3.${REGION}.amazonaws.com", "kms:EncryptionContext:aws:s3:arn": "arn:aws:s3:::${BUCKET}"}}}
  ]
}
EOF
S3_POLICY_ARN="$(ensure_iam_policy "PipesHub-${CLUSTER}-S3" "${STATE_DIR}/s3-policy.json")"
# The association can exist before the service account; pods get credentials when they start.
# Chart fullname is the release name when it contains "pipeshub-ai", otherwise <release>-pipeshub-ai.
if [[ "$RELEASE" == *pipeshub-ai* ]]; then
  SA_NAME="$RELEASE"
else
  SA_NAME="${RELEASE}-pipeshub-ai"
fi
EXISTING_ASSOC=$(aws eks list-pod-identity-associations --cluster-name "$CLUSTER" --namespace "$NAMESPACE" --service-account "$SA_NAME" --query 'associations[0].associationId' --output text)
if [[ -z "$EXISTING_ASSOC" || "$EXISTING_ASSOC" == None ]]; then
  eksctl create podidentityassociation \
    --cluster "$CLUSTER" --region "$REGION" \
    --namespace "$NAMESPACE" --service-account-name "$SA_NAME" \
    --role-name "PipesHub-${CLUSTER}-S3" \
    --permission-policy-arns "$S3_POLICY_ARN"
else
  note "Pod Identity association exists"
fi

step "PipesHub (Helm release ${RELEASE})"
export FRONTEND_PUBLIC_URL NAMESPACE RELEASE
if $USE_CUSTOM_IMAGE; then
  export PIPESHUB_IMAGE_REPOSITORY="$IMAGE_REPOSITORY"
  export PIPESHUB_IMAGE_TAG="$IMAGE_TAG"
  if $USE_PRIVATE_IMAGE; then
    export PIPESHUB_DOCKER_TOKEN="$DOCKER_TOKEN"
    export PIPESHUB_DOCKER_SERVER="$DOCKER_SERVER"
    export PIPESHUB_DOCKER_USERNAME="$DOCKER_USERNAME"
  fi
fi
ALLOWED_ORIGINS="$FRONTEND_PUBLIC_URL" PIPESHUB_HOST="$DOMAIN" ALB_CERT_ARN="$CERT_ARN" \
  "${AWS_DIR}/install.sh"
unset PIPESHUB_DOCKER_TOKEN DOCKER_TOKEN

step "Save secret-key to Secrets Manager"
if aws secretsmanager describe-secret --secret-id "${CLUSTER}/secret-key" >/dev/null 2>&1; then
  note "${CLUSTER}/secret-key already saved"
else
  kubectl get secret -n "$NAMESPACE" pipeshub-ai-secrets -o jsonpath='{.data.secret-key}' | base64 -d \
    | aws secretsmanager create-secret --name "${CLUSTER}/secret-key" \
        --description "PipesHub ${CLUSTER} key-value store encryption key" \
        --secret-string file:///dev/stdin >/dev/null
  note "saved as ${CLUSTER}/secret-key"
fi

step "Load balancer and DNS"
note "waiting for the load balancer hostname (up to 10 minutes)"
ALB_HOST=""
for attempt in $(seq 1 60); do
  ALB_HOST="$(kubectl get ingress -n "$NAMESPACE" "$SA_NAME" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)"
  [[ -n "$ALB_HOST" ]] && break
  if [[ $(( attempt % 6 )) -eq 0 ]]; then
    note "still waiting for the load balancer"
  fi
  sleep 10
done
[[ -n "$ALB_HOST" ]] || die "the ingress has no load balancer after 10 minutes. kubectl logs -n kube-system deploy/aws-load-balancer-controller"
note "load balancer ${ALB_HOST}"
if [[ -n "$HOSTED_ZONE_ID" ]]; then
  ALB_ZONE_ID="$(aws elbv2 describe-load-balancers \
    --query "LoadBalancers[?DNSName=='${ALB_HOST}'].CanonicalHostedZoneId | [0]" --output text)"
  [[ -n "$ALB_ZONE_ID" && "$ALB_ZONE_ID" != None ]] || die "could not find load balancer ${ALB_HOST}. Create an alias record for ${DOMAIN} by hand"
  route53_upsert "$HOSTED_ZONE_ID" "{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"${DOMAIN}\",\"Type\":\"A\",\"AliasTarget\":{\"HostedZoneId\":\"${ALB_ZONE_ID}\",\"DNSName\":\"${ALB_HOST}\",\"EvaluateTargetHealth\":false}}}]}"
  note "Route 53: ${DOMAIN} -> load balancer"
else
  note "Create this DNS record at your DNS provider now:"
  note "  CNAME  ${DOMAIN}  ->  ${ALB_HOST}"
fi

if $BACKUPS; then
  step "Daily EBS backups"
  aws backup describe-backup-vault --backup-vault-name "$CLUSTER" >/dev/null 2>&1 \
    || aws backup create-backup-vault --backup-vault-name "$CLUSTER" >/dev/null
  BACKUP_ROLE="PipesHub-${CLUSTER}-Backup"
  if ! aws iam get-role --role-name "$BACKUP_ROLE" >/dev/null 2>&1; then
    aws iam create-role --role-name "$BACKUP_ROLE" --assume-role-policy-document \
      '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"backup.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    aws iam attach-role-policy --role-name "$BACKUP_ROLE" \
      --policy-arn arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup
    aws iam attach-role-policy --role-name "$BACKUP_ROLE" \
      --policy-arn arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores
  fi
  PLAN_ID="$(aws backup list-backup-plans --query "BackupPlansList[?BackupPlanName=='${CLUSTER}-daily'].BackupPlanId | [0]" --output text)"
  if [[ -z "$PLAN_ID" || "$PLAN_ID" == None ]]; then
    PLAN_ID="$(aws backup create-backup-plan --query BackupPlanId --output text --backup-plan \
      "{\"BackupPlanName\":\"${CLUSTER}-daily\",\"Rules\":[{\"RuleName\":\"daily\",\"TargetBackupVaultName\":\"${CLUSTER}\",\"ScheduleExpression\":\"cron(0 3 * * ? *)\",\"StartWindowMinutes\":60,\"CompletionWindowMinutes\":240,\"Lifecycle\":{\"DeleteAfterDays\":30}}]}")"
  fi
  if [[ "$(aws backup list-backup-selections --backup-plan-id "$PLAN_ID" --query 'length(BackupSelectionsList)' --output text)" == 0 ]]; then
    # A new IAM role takes a few seconds to become usable by AWS Backup.
    for attempt in $(seq 1 12); do
      if aws backup create-backup-selection --backup-plan-id "$PLAN_ID" --backup-selection \
        "{\"SelectionName\":\"${CLUSTER}-ebs\",\"IamRoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/${BACKUP_ROLE}\",\"ListOfTags\":[{\"ConditionType\":\"STRINGEQUALS\",\"ConditionKey\":\"pipeshub-backup\",\"ConditionValue\":\"true\"}]}" \
        >/dev/null 2>&1; then
        break
      fi
      [[ "$attempt" -lt 12 ]] || die "could not create the backup selection"
      sleep 10
    done
  fi
  note "daily at 03:00 UTC, kept 30 days, vault ${CLUSTER}"
fi

step "Health check"
note "waiting for ${FRONTEND_PUBLIC_URL}/api/v1/health (up to 5 minutes)"
healthy=false
for attempt in $(seq 1 30); do
  if [[ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "${FRONTEND_PUBLIC_URL}/api/v1/health")" == 200 ]]; then
    healthy=true
    break
  fi
  if [[ $(( attempt % 6 )) -eq 0 ]]; then
    note "still waiting for the health check"
  fi
  sleep 10
done
if $healthy; then
  note "${FRONTEND_PUBLIC_URL}/api/v1/health returned 200"
else
  note "not reachable yet. DNS can take a few minutes. Retry: curl ${FRONTEND_PUBLIC_URL}/api/v1/health"
fi

printf '\n%sPipesHub is deployed.%s\n' "$C_GREEN" "$C_RESET"
cat <<EOF

  S3 bucket  ${BUCKET}
  Region     ${REGION}
  Image      $(if $USE_PRIVATE_IMAGE; then printf '%s:%s (private)' "$IMAGE_REPOSITORY" "$IMAGE_TAG"; elif $USE_CUSTOM_IMAGE; then printf '%s:%s' "$IMAGE_REPOSITORY" "$IMAGE_TAG"; else printf '%s:%s' "$DEFAULT_APP_REPOSITORY" "$DEFAULT_APP_TAG"; fi)

  During onboarding, on the Storage step, choose S3 and enter this bucket and region.
  Leave the access key and secret key empty. The app uses its Pod Identity role.
  Do this before anyone uploads a file. App pods do not share a local disk.

  1. Open ${FRONTEND_PUBLIC_URL} now and create the admin account.
     Until an admin exists, anyone who reaches the URL can create it.
  2. secret-key is saved in Secrets Manager as ${CLUSTER}/secret-key.

  Re-run this command to update. Remove with:
    $0 --domain ${DOMAIN} --region ${REGION} --cluster ${CLUSTER} --destroy
EOF

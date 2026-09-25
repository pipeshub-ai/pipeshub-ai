# Deploy PipesHub on Amazon EKS

This installs PipesHub on a private EKS cluster. Databases use encrypted EBS gp3 volumes, one per pod. Nothing uses NFS or EFS.

What you get:

- MongoDB 8.0.17, three members, replica set `rs0`
- Redis 7.4, one master and one replica. Redis Streams is the event bus. The same Redis holds the key-value store. Values in that store are encrypted with AES-256-GCM using `secret-key`
- Qdrant, three nodes, each shard kept on two nodes
- Neo4j Community, one node. Community cannot cluster. Backups are EBS snapshots
- The app, two to four replicas, with code execution in a Docker sandbox on a separate node group

The app pods do not share a disk. Set blob storage to S3 before anyone uploads a file. Files written to local disk exist only on the pod that wrote them.

Existing installs that already use the Bitnami MongoDB and Redis charts should stay on those charts. The data directories and disk names differ. This guide is for a new cluster.

## Quick install: one command

`deployment/helm/aws/deploy.sh` does steps 1 to 10 below for you. It takes 45 to 60 minutes, mostly waiting for EKS.

You need AWS credentials (`aws sts get-caller-identity` works) and a hostname. If `aws` (v2), `eksctl`, `kubectl`, or `helm` is missing or too old, the script offers to install it; see step 1. If the hostname's domain is a public Route 53 zone in the same account, the script also creates the certificate validation record and the DNS record. Otherwise it prints each record for you to add at your DNS provider and waits.

```bash
./deployment/helm/aws/deploy.sh --domain pipeshub.example.com --region us-east-1
```

It shows a plan and asks once before creating anything; `--yes` skips the prompt. It then:

1. Creates a KMS key (`alias/<cluster>-eks`) for Kubernetes Secrets
2. Picks three zones that offer `m7i.2xlarge`, picks the EKS default Kubernetes version, and creates the cluster from `cluster.yaml`. The filled-in copy is saved under `~/.pipeshub/eks/`
3. Adds the `gp3` storage class and the AWS Load Balancer Controller, with its own IAM role
4. Requests the ACM certificate and waits until it is issued
5. Creates a private, encrypted, versioned S3 bucket and gives the app access through Pod Identity
6. Runs `install.sh`, which generates the passwords and installs the chart
7. Saves `secret-key` to Secrets Manager as `<cluster>/secret-key`
8. Points the hostname at the load balancer
9. Sets up daily EBS backups, kept 30 days (`--no-backups` to skip)
10. Checks `https://<domain>/api/v1/health`

When it finishes, open the URL, create the admin account, and choose S3 in storage settings with the bucket and region it prints. Leave the access keys empty.

If a step fails, the script prints the command that failed. Fix the cause and run the same command again. Every step skips what already exists. Run it again later to update the chart.

`./deployment/helm/aws/deploy.sh --help` prints what the command does and the full option list, including `--namespace`, `--release`, `--no-backups`, and `--destroy`.

To remove the release and the cluster:

```bash
./deployment/helm/aws/deploy.sh --domain pipeshub.example.com --region us-east-1 --destroy
```

This keeps the EBS volumes, the S3 bucket, backups, the KMS key, and the saved `secret-key`, and lists what is left.

The rest of this guide is the same install step by step, for when you want to run or review each part yourself.

## Manual install

Every command below uses these names. Change them once here and paste the block into each new shell:

```bash
export AWS_REGION=us-east-1
export CLUSTER=pipeshub
export NAMESPACE=pipeshub
export ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
```

## 1. Prerequisites

You need:

- An AWS account and a user or role that can create EKS, IAM roles, KMS keys, EBS volumes, S3 buckets, and load balancers
- `aws` CLI v2, `kubectl`, `helm` 3.11 or newer (Helm 4 works), `eksctl` 0.200 or newer, `openssl`, `jq`
- A domain name you can point at an Application Load Balancer
- A clone of this repository. Run every command from its root

Install the tools on macOS or Linux (amd64 or arm64):

```bash
./deployment/helm/aws/install-deps.sh
```

It installs only what is missing or too old, into `~/.pipeshub/bin`, without sudo. `kubectl`, `helm`, and `eksctl` come from their official releases and are checked against the published SHA-256 sums. The AWS CLI comes from AWS; on macOS its package signature is checked. `kubectl` matches the cluster's Kubernetes minor version. `--check` only reports. Add `~/.pipeshub/bin` to your `PATH` to use the tools outside the scripts. `curl`, `tar`, `openssl`, and on Linux `unzip` come from the operating system; the script prints the package-manager command if one is missing.

On Windows, use WSL2: run `wsl --install -d Ubuntu` in PowerShell, then clone the repository inside Ubuntu and follow the Linux steps.

Create a KMS key for Kubernetes Secrets:

```bash
aws kms create-key --region "$AWS_REGION" --description pipeshub-eks --query KeyMetadata.Arn --output text
```

Verify: the command prints a key ARN. Copy it.

Check which Kubernetes versions are still in standard support. Versions in extended support cost more per hour:

```bash
aws eks describe-cluster-versions --region "$AWS_REGION" --status standard-support \
  --query 'clusterVersions[].clusterVersion' --output text
```

## 2. Create the cluster

Edit [deployment/helm/aws/cluster.yaml](../../deployment/helm/aws/cluster.yaml):

- `metadata.region` and `metadata.version` (one of the versions from step 1)
- the top-level `availabilityZones` and the `availabilityZones` in each node group: three zones in that region
- `secretsEncryption.keyARN`: the ARN from step 1

The file creates:

- Private node subnets across three zones, public and private API endpoints
- KMS encryption for Kubernetes Secrets
- The EBS CSI driver (with a Pod Identity role), the VPC CNI with network policy, the Pod Identity agent, and metrics-server
- A `system` node group: 2 × `m7i.large`, no taint. CoreDNS and the controllers run here
- A `data` node group: 4 × `m7i.2xlarge` (8 vCPU, 32 GiB), tainted `pipeshub/role=data`
- An `app` node group: 2 × `m7i.2xlarge`, 150 GiB root disk, tainted `pipeshub/role=app`
- IMDSv2 only, and pods cannot reach the instance metadata endpoint (`disablePodIMDS`). Neither the app nor sandbox containers can read the node's IAM role

```bash
eksctl create cluster -f deployment/helm/aws/cluster.yaml
```

This takes 20 to 30 minutes. eksctl writes the kubeconfig entry for you.

Verify:

```bash
kubectl get nodes -L pipeshub/role,topology.kubernetes.io/zone
kubectl get csidriver ebs.csi.aws.com
kubectl get pods -n kube-system
```

You should see two `system`, four `data`, and two `app` nodes, all Ready, across three zones. Every `kube-system` pod should be Running.

## 3. Storage class and the load balancer controller

```bash
kubectl apply -f deployment/helm/aws/storageclass-gp3.yaml
```

`gp3` is encrypted, waits to bind until a pod is scheduled (so the volume is created in that pod's zone), can be expanded, and is `Retain`. Helm uninstall does not delete database disks. Every volume it creates is tagged `pipeshub-backup=true`, which step 10 uses.

If the cluster already has a `gp3` class with different parameters, delete it first (`kubectl delete storageclass gp3`). Storage class parameters cannot be changed in place. Existing volumes are not affected.

Install the AWS Load Balancer Controller. It gets its AWS permissions from an IAM role for its service account:

```bash
LBC_VERSION=v3.5.0
curl -fsSL -o lbc-iam-policy.json \
  "https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/${LBC_VERSION}/docs/install/iam_policy.json"
aws iam create-policy --policy-name PipesHubLBController --policy-document file://lbc-iam-policy.json

eksctl create iamserviceaccount \
  --cluster "$CLUSTER" --region "$AWS_REGION" \
  --namespace kube-system --name aws-load-balancer-controller \
  --role-name PipesHubLBController \
  --attach-policy-arn "arn:aws:iam::${ACCOUNT_ID}:policy/PipesHubLBController" \
  --approve

VPC_ID="$(aws eks describe-cluster --name "$CLUSTER" --region "$AWS_REGION" \
  --query cluster.resourcesVpcConfig.vpcId --output text)"

helm repo add eks https://aws.github.io/eks-charts
helm repo update eks
helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
  -n kube-system \
  --set clusterName="$CLUSTER" \
  --set region="$AWS_REGION" \
  --set vpcId="$VPC_ID" \
  --set serviceAccount.create=false \
  --set serviceAccount.name=aws-load-balancer-controller \
  --wait
```

`region` and `vpcId` are required here because pods cannot read instance metadata on these nodes.

Verify:

```bash
kubectl get storageclass gp3
kubectl get deployment -n kube-system aws-load-balancer-controller
```

The deployment should show `2/2` ready.

## 4. TLS certificate

Request a public ACM certificate in the cluster region for the name you will use:

```bash
export PIPESHUB_HOST=pipeshub.example.com
aws acm request-certificate --region "$AWS_REGION" --domain-name "$PIPESHUB_HOST" \
  --validation-method DNS --query CertificateArn --output text
```

Copy the ARN. Add the validation CNAME it asks for:

```bash
aws acm describe-certificate --region "$AWS_REGION" --certificate-arn <ARN> \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord'
```

Verify: after a few minutes, `aws acm describe-certificate ... --query Certificate.Status` returns `ISSUED`. The load balancer is not created until the certificate is issued.

## 5. Secrets

`deployment/helm/aws/install.sh` creates the Kubernetes Secret `pipeshub-ai-secrets` on the first run and never overwrites it. The cluster's KMS key encrypts it at rest. It holds `secret-key`, `mongodb-username`, `mongodb-password`, `redis-password`, `neo4j-password`, and `qdrant-api-key`.

To keep these in AWS Secrets Manager instead, install External Secrets Operator, create a Secret with the same name and keys through it, and then run the installer. The installer reuses a Secret that already exists.

`secret-key` encrypts the key-value store. If you lose it or change it, stored credentials cannot be decrypted. Right after step 6, copy it to Secrets Manager:

```bash
aws secretsmanager create-secret --region "$AWS_REGION" --name pipeshub/secret-key \
  --secret-string "$(kubectl get secret -n "$NAMESPACE" pipeshub-ai-secrets -o jsonpath='{.data.secret-key}' | base64 -d)"
```

Do not commit it or paste it into a ticket.

## 6. Install

```bash
export FRONTEND_PUBLIC_URL="https://${PIPESHUB_HOST}"
export ALLOWED_ORIGINS="$FRONTEND_PUBLIC_URL"
export ALB_CERT_ARN=arn:aws:acm:us-east-1:111122223333:certificate/your-cert-id
./deployment/helm/aws/install.sh
```

The script checks the storage class, the EBS driver, the load balancer controller, and the node labels. It labels the namespace so the privileged sandbox can run, creates the Secret, then installs the chart and waits up to 30 minutes. The first install pulls large images; 10 to 15 minutes is normal.

Verify:

```bash
kubectl get pods -n "$NAMESPACE" -o wide
kubectl get ingress -n "$NAMESPACE"
```

Every pod should be `Running` and `Ready`. The `pipeshub-ai-mongodb-initiate-1` job pod should be `Completed`. The ingress `ADDRESS` column shows the ALB hostname after one or two minutes.

Create a CNAME from `PIPESHUB_HOST` to that hostname (or a Route 53 alias record). Then:

```bash
dig +short "$PIPESHUB_HOST"
curl -fsS -o /dev/null -w '%{http_code}\n' "https://${PIPESHUB_HOST}/api/v1/health"
curl -s -o /dev/null -w '%{http_code}\n' "http://${PIPESHUB_HOST}/"
```

Expect `200` from the first call and `301` from the second (HTTP redirects to HTTPS).

Open `FRONTEND_PUBLIC_URL` in a browser and create the first admin account. Do this now. Until an admin exists, anyone who reaches the URL can create it.

## 7. Check the databases

Each database pod already has its own credentials in its environment, so these commands do not need you to copy passwords.

MongoDB, three healthy members:

```bash
kubectl exec -n "$NAMESPACE" pipeshub-ai-mongodb-0 -c mongodb -- bash -c \
  'mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --quiet \
   --eval "rs.status().members.map(m => m.name + \" \" + m.stateStr)"'
```

Expect one `PRIMARY` and two `SECONDARY`.

Qdrant, three peers:

```bash
QDRANT_API_KEY="$(kubectl get secret -n "$NAMESPACE" pipeshub-ai-secrets -o jsonpath='{.data.qdrant-api-key}' | base64 -d)"
kubectl port-forward -n "$NAMESPACE" pipeshub-ai-qdrant-0 6333:6333 >/dev/null &
sleep 2
curl -fsS -H "api-key: ${QDRANT_API_KEY}" http://127.0.0.1:6333/cluster | jq '.result.peers | length'
kill %1
unset QDRANT_API_KEY
```

Expect `3`.

Redis, replica connected, and nothing is evicted when memory is full:

```bash
kubectl exec -n "$NAMESPACE" pipeshub-ai-redis-master-0 -- redis-cli INFO replication
kubectl exec -n "$NAMESPACE" pipeshub-ai-redis-master-0 -- redis-cli CONFIG GET maxmemory-policy
```

Expect `connected_slaves:1` and `noeviction`.

The app talks only to the Redis master. The replica is a warm copy on its own disk. If the master pod dies, Kubernetes starts it again on the same disk. The replica is not promoted.

Redis is both the event bus (Streams) and the key-value store. Do not point a second product at this Redis. `FLUSHALL` and `FLUSHDB` are disabled.

Neo4j:

```bash
kubectl exec -n "$NAMESPACE" pipeshub-ai-neo4j-0 -- bash -c \
  'cypher-shell -u neo4j -p "$PIPESHUB_NEO4J_PASSWORD" "RETURN 1 AS ok"'
```

Expect `ok` and `1`.

Sandbox, the Docker daemon next to each app pod:

```bash
kubectl exec -n "$NAMESPACE" deploy/pipeshub-ai -c dind -- docker info --format '{{.ServerVersion}}'
```

Expect a version number. The first `run_code` call pulls `pipeshubai/pipeshub-sandbox:0.8.0` into that pod's daemon.

## 8. Store files in S3

Create a private, encrypted bucket:

```bash
export BUCKET="pipeshub-files-${ACCOUNT_ID}"
if [ "$AWS_REGION" = us-east-1 ]; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION"
else
  aws s3api create-bucket --bucket "$BUCKET" --region "$AWS_REGION" \
    --create-bucket-configuration LocationConstraint="$AWS_REGION"
fi
aws s3api put-public-access-block --bucket "$BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
```

Give the app's service account access to that bucket only, through EKS Pod Identity:

```bash
cat > pipeshub-s3-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": "arn:aws:s3:::${BUCKET}"},
    {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": "arn:aws:s3:::${BUCKET}/*"},
    {"Effect": "Allow", "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": "*", "Condition": {"StringEquals": {"kms:ViaService": "s3.${AWS_REGION}.amazonaws.com", "kms:EncryptionContext:aws:s3:arn": "arn:aws:s3:::${BUCKET}"}}}
  ]
}
EOF
aws iam create-policy --policy-name PipesHubS3 --policy-document file://pipeshub-s3-policy.json

eksctl create podidentityassociation \
  --cluster "$CLUSTER" --region "$AWS_REGION" \
  --namespace "$NAMESPACE" --service-account-name pipeshub-ai \
  --role-name PipesHubS3 \
  --permission-policy-arns "arn:aws:iam::${ACCOUNT_ID}:policy/PipesHubS3"

kubectl rollout restart deployment/pipeshub-ai -n "$NAMESPACE"
kubectl rollout status deployment/pipeshub-ai -n "$NAMESPACE" --timeout 15m
```

Pods get Pod Identity credentials when they start, so the restart is required.

Verify:

```bash
kubectl exec -n "$NAMESPACE" deploy/pipeshub-ai -c pipeshub-ai -- env | grep AWS_CONTAINER_CREDENTIALS_FULL_URI
```

Expect a line that ends with `169.254.170.23/v1/credentials`.

In the UI, as the admin, open storage settings and choose S3. Enter the bucket name and region. Leave the access key and secret key empty, so the app uses the Pod Identity role. Save before uploading documents or connecting sources.

## 9. Connector OAuth

Only the dashboard is public by default. Add a second host on port 8088 only if a connector needs an OAuth callback or a webhook. Set `config.connectorPublicBackend` to that public URL and list both hosts, because `--set` on a list replaces the whole list:

```bash
helm upgrade pipeshub-ai ./deployment/helm/pipeshub-ai \
  -n "$NAMESPACE" -f ./deployment/helm/pipeshub-ai/values-eks.yaml \
  --reuse-values \
  --set "ingress.hosts[0].host=${PIPESHUB_HOST}" \
  --set 'ingress.hosts[0].paths[0].path=/' \
  --set 'ingress.hosts[0].paths[0].pathType=Prefix' \
  --set 'ingress.hosts[1].host=connect.example.com' \
  --set 'ingress.hosts[1].paths[0].path=/' \
  --set 'ingress.hosts[1].paths[0].pathType=Prefix' \
  --set 'ingress.hosts[1].paths[0].port=8088' \
  --set config.connectorPublicBackend=https://connect.example.com \
  --wait --timeout 30m
```

The ACM certificate must cover `connect.example.com` too. Request one for both names, or add a second certificate ARN, comma-separated, in `alb.ingress.kubernetes.io/certificate-arn`.

## 10. Backups and restore

AWS Backup takes daily snapshots of every volume tagged `pipeshub-backup=true`. The `gp3` class adds that tag.

```bash
aws backup create-backup-vault --region "$AWS_REGION" --backup-vault-name pipeshub

aws iam create-role --role-name PipesHubBackup --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"backup.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name PipesHubBackup \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup
aws iam attach-role-policy --role-name PipesHubBackup \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores

PLAN_ID="$(aws backup create-backup-plan --region "$AWS_REGION" --query BackupPlanId --output text --backup-plan \
  '{"BackupPlanName":"pipeshub-daily","Rules":[{"RuleName":"daily","TargetBackupVaultName":"pipeshub","ScheduleExpression":"cron(0 3 * * ? *)","StartWindowMinutes":60,"CompletionWindowMinutes":240,"Lifecycle":{"DeleteAfterDays":30}}]}')"

aws backup create-backup-selection --region "$AWS_REGION" --backup-plan-id "$PLAN_ID" --backup-selection \
  "{\"SelectionName\":\"pipeshub-ebs\",\"IamRoleArn\":\"arn:aws:iam::${ACCOUNT_ID}:role/PipesHubBackup\",\"ListOfTags\":[{\"ConditionType\":\"STRINGEQUALS\",\"ConditionKey\":\"pipeshub-backup\",\"ConditionValue\":\"true\"}]}"
```

Verify the next morning:

```bash
aws backup list-recovery-points-by-backup-vault --region "$AWS_REGION" --backup-vault-name pipeshub \
  --query 'RecoveryPoints[].[ResourceArn,Status,CreationDate]' --output table
```

Expect one completed recovery point per database volume. Snapshots are crash-consistent. Neo4j has no second node, so its snapshot is the copy you will restore. S3 versioning (step 8) covers uploaded files.

To restore one database volume, for example `data-pipeshub-ai-neo4j-0`:

1. Scale the StatefulSet to 0: `kubectl scale statefulset pipeshub-ai-neo4j -n "$NAMESPACE" --replicas 0`.
2. Find the old volume's zone: `kubectl get pv $(kubectl get pvc -n "$NAMESPACE" data-pipeshub-ai-neo4j-0 -o jsonpath='{.spec.volumeName}') -o jsonpath='{.spec.nodeAffinity}'`.
3. In AWS Backup, restore the recovery point to a new EBS volume in that zone, type gp3, encrypted. Note the new volume id.
4. Delete the claim: `kubectl delete pvc -n "$NAMESPACE" data-pipeshub-ai-neo4j-0`. The old volume stays, because the class is `Retain`.
5. Create a PersistentVolume for the new volume and a claim with the old name bound to it:

   ```yaml
   apiVersion: v1
   kind: PersistentVolume
   metadata:
     name: restored-neo4j-data
   spec:
     capacity: { storage: 100Gi }
     accessModes: [ReadWriteOnce]
     persistentVolumeReclaimPolicy: Retain
     storageClassName: gp3
     csi:
       driver: ebs.csi.aws.com
       volumeHandle: vol-0123456789abcdef0
       fsType: ext4
     nodeAffinity:
       required:
         nodeSelectorTerms:
           - matchExpressions:
               - key: topology.kubernetes.io/zone
                 operator: In
                 values: [us-east-1a]
   ---
   apiVersion: v1
   kind: PersistentVolumeClaim
   metadata:
     name: data-pipeshub-ai-neo4j-0
     namespace: pipeshub
   spec:
     accessModes: [ReadWriteOnce]
     storageClassName: gp3
     volumeName: restored-neo4j-data
     resources:
       requests: { storage: 100Gi }
   ```

6. Scale back to the previous replica count.
7. For MongoDB, confirm `rs.status()` shows three healthy members. For Qdrant, confirm `/cluster` still lists three peers.

Restoring one MongoDB or Qdrant member is rarely needed: delete its claim and pod, and it resyncs from the others.

A wrong or lost `secret-key` cannot be fixed by restoring disks. Restoring an old disk with a new `secret-key` leaves the key-value store unreadable. Keep the copy from step 5.

## 11. Upgrades

Set the new app and sandbox versions, then upgrade:

```bash
helm upgrade pipeshub-ai ./deployment/helm/pipeshub-ai \
  -n "$NAMESPACE" -f ./deployment/helm/pipeshub-ai/values-eks.yaml \
  --reuse-values \
  --set image.tag=0.8.0 \
  --set config.sandboxDockerImage=pipeshubai/pipeshub-sandbox:0.8.0 \
  --wait --timeout 30m
```

`--reuse-values` keeps the public URL, the ingress hosts, and the existing Secret, because `values-eks.yaml` does not set those. Do not pass a new `secret-key`. Take an on-demand backup in AWS Backup first.

MongoDB and Qdrant spread across zones. When you upgrade node groups, drain one node at a time. The disruption budgets allow one database pod down. Neo4j and the Redis master have one pod each and are briefly unavailable while their node drains.

To grow a disk, edit the claim's storage request. The class allows expansion. Do not shrink a disk.

The app scales from two to four pods. More pods do not fit on two app nodes. To go higher, add Karpenter or Cluster Autoscaler, then raise `autoscaling.maxReplicas`.

## 12. Troubleshooting

**`helm` times out and the app is not Ready.** Check MongoDB first: `kubectl logs -n "$NAMESPACE" job/pipeshub-ai-mongodb-initiate-1` (the number is the Helm revision). The app waits for the replica set.

**PVC stays Pending.** The pod is in a zone where the volume cannot attach, or `gp3` is missing. Run `kubectl describe pvc -n "$NAMESPACE"`. Confirm the storage class exists and that `ebs-csi-controller` pods are Running in `kube-system`.

**Pod is Pending with a taint or `Insufficient cpu`.** Data pods land only on `pipeshub/role=data`, app pods only on `pipeshub/role=app`. Qdrant and Neo4j each reserve 4 CPUs and 8 GiB. Check that four `data` nodes exist across three zones.

**Ingress has no address.** Check `kubectl logs -n kube-system deploy/aws-load-balancer-controller`. Common causes: the certificate is not `ISSUED`, the public subnets are missing the `kubernetes.io/role/elb` tag (eksctl adds it), or the controller is missing `region` or `vpcId`.

**Sandbox pod is forbidden.** The namespace must allow privileged pods. DinD is privileged on purpose, and only on the app nodes. Re-run `install.sh`; it re-applies the namespace labels.

**`run_code` cannot pull an image.** The sandbox image is set by `config.sandboxDockerImage`. Check that the tag exists on Docker Hub and that the app nodes have outbound HTTPS through the NAT gateway. The network policy allows TCP 443.

**S3 errors with "could not load credentials".** The pod started before the Pod Identity association existed. Restart the deployment. Confirm the association with `eksctl get podidentityassociation --cluster "$CLUSTER" --region "$AWS_REGION"`.

**Login works, then every integration fails after a reinstall.** `secret-key` changed. Restore the previous Secret from Secrets Manager. There is no recovery without that key.

## Remove everything

```bash
helm uninstall pipeshub-ai -n "$NAMESPACE"
kubectl delete pvc -n "$NAMESPACE" --all
eksctl delete cluster -f deployment/helm/aws/cluster.yaml --disable-nodegroup-eviction
```

The `Retain` class leaves the EBS volumes in your account after this. List and delete them when you no longer need them:

```bash
aws ec2 describe-volumes --region "$AWS_REGION" --filters Name=tag:pipeshub-backup,Values=true \
  --query 'Volumes[].[VolumeId,State,Size]' --output table
aws ec2 delete-volume --region "$AWS_REGION" --volume-id <vol-id>
```

The S3 bucket, backup vault, KMS key, IAM policies, and Secrets Manager secret are also kept.

## Security checklist

- HTTP redirects to HTTPS. The ALB terminates TLS with an ACM certificate. Add a WAFv2 web ACL with the `alb.ingress.kubernetes.io/wafv2-acl-arn` annotation if you want managed rules.
- Create the first admin account right after install.
- Node groups are in private subnets. IMDSv1 is off and pods cannot reach instance metadata. AWS access is only through the Pod Identity and IRSA roles in this guide.
- Node IAM roles have no extra policies. The EBS driver, the load balancer controller, and S3 each use their own role.
- EBS volumes and Kubernetes Secrets are encrypted with KMS. The S3 bucket is private, encrypted, and versioned.
- Network policy is on: DNS, pods in the namespace, HTTPS 443, and the Pod Identity agent. Only ports 3001 and 8088 accept traffic from outside the namespace.
- The sandbox does not install packages unless you set `config.sandboxAllowNetwork=true`. User code itself runs with no network.
- No database port is published on the load balancer.
- `FLUSHALL` and `FLUSHDB` are disabled on Redis. `secret-key` is stored in Secrets Manager as well as in the Secret.
- Restrict the public API endpoint to your office or VPN ranges: `eksctl utils set-public-access-cidrs --cluster "$CLUSTER" --public-access-cidrs <CIDR> --approve`.

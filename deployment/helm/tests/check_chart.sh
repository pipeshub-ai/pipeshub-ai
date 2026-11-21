#!/usr/bin/env bash
# ==============================================================================
# Render the chart in every supported configuration and validate the output.
# ==============================================================================
# First, every .Values path the templates read must exist in values.yaml (a
# missing one renders as an empty string, not an error); the checker's own unit
# tests run too. Then, for each
# variant: helm lint, helm template, then kubeconform against the
# Kubernetes API schemas for the oldest version the README supports and a
# current one. Then check that misconfigurations the chart is meant to refuse
# are still refused, with the message a user would see.
#
# Needs helm, kubeconform and python3 with PyYAML, and network access the
# first time (chart dependencies from Docker Hub, API schemas from GitHub).
#
#   bash deployment/helm/tests/check_chart.sh
# ==============================================================================
set -euo pipefail

# Run from inside the chart so every path below is relative and space-free.
cd "$(dirname "${BASH_SOURCE[0]}")/../pipeshub-ai"
read -r -a K8S_VERSIONS <<<"${K8S_VERSIONS:-1.24.0 1.37.0}"
OUT="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-chart.XXXXXX")"
trap 'rm -rf "$OUT"' EXIT

for tool in helm kubeconform python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "check_chart: $tool is required" >&2; exit 1; }
done

# Fails when Chart.lock no longer matches Chart.yaml, which is the first step
# of every documented install.
helm dependency build . >/dev/null

SECRETS=(
  --set secretKey=ci-secret-key
  --set mongodb.auth.rootPassword=ci-root
  --set redis.auth.password=ci-redis
  --set neo4j.auth.password=ci-neo4j
  --set qdrant.apiKey=ci-qdrant
  --set 'mongodb.auth.usernames[0]=pipeshub'
  --set 'mongodb.auth.passwords[0]=ci-app'
  --set 'mongodb.auth.databases[0]=pipeshub'
)
INGRESS=(
  --set ingress.enabled=true
  --set 'ingress.hosts[0].host=pipeshub.example.com'
  --set 'ingress.hosts[0].paths[0].path=/'
  --set 'ingress.hosts[0].paths[0].pathType=Prefix'
)
# The presets render the hardened profile here (ADR-OPS-07): their network graph is what netpol_check.py proves.
# defaults-dind and compat-kafka-builtin-mongo render the compatibility default.
LOCAL=(-f values-local.yaml --set hardened=true)

# name, then the helm arguments for that variant. They come after SECRETS so a
# variant can override one of them.
VARIANTS=(
  "local-neo4j-kafka|${LOCAL[*]}"
  "local-redis-streams|${LOCAL[*]} --set messageBroker.type=redis"
  "local-arangodb|${LOCAL[*]} --set neo4j.enabled=false --set arango.enabled=true --set arango.auth.rootPassword=ci-arango"
  "local-etcd|${LOCAL[*]} --set etcd.enabled=true --set config.kvStoreType=etcd"
  "local-redis-cluster-external|${LOCAL[*]} --set redis.enabled=false --set redis.external.enabled=true --set redis.mode=cluster --set redis.external.clusterEndpoints=redis-0:6379 --set celery.brokerUrl=redis://celery:6379/0"
  "local-extras|${LOCAL[*]} --set networkPolicy.enabled=true ${INGRESS[*]}"
  "local-external-secrets|${LOCAL[*]} --set secretManagement.externalSecrets.enabled=true --set secretManagement.externalSecrets.secretStoreRef.name=vault --set secretManagement.externalSecrets.remoteRefs.secretKey=pipeshub/secret-key --set secretManagement.externalSecrets.remoteRefs.mongodbPassword=pipeshub/mongo --set secretManagement.externalSecrets.remoteRefs.redisPassword=pipeshub/redis --set secretManagement.externalSecrets.remoteRefs.neo4jPassword=pipeshub/neo4j"
  "local-existing-secrets|${LOCAL[*]} --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-app"
  "defaults-dind|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany}"
  "hardened-dind|--set hardened=true --set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set messageBroker.type=kafka --set mongodb.enabled=false --set mongodb.builtin.enabled=true"
  "compat-kafka-builtin-mongo|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set messageBroker.type=kafka --set mongodb.enabled=false --set mongodb.builtin.enabled=true"
  "node-log-files|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set logging.nodeFiles.enabled=true"
  "broker-netpol|--set config.sandboxMode=broker --set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set networkPolicy.enabled=true"
  "broker-etcd|--set hardened=true --set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set etcd.enabled=true --set config.kvStoreType=etcd"
  "netpol-off|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set networkPolicy.enabled=false"
  "hpa-event-loop|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set monitoring.serviceMonitor.enabled=true --set autoscaling.eventLoopUtilization.enabled=true"
  "harness-metrics|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set metrics.harness.enabled=true --set metrics.harness.remoteWrite.url=http://prometheus.monitoring:9090/api/v1/write --set metrics.harness.remoteWrite.peers[0].namespaceSelector.matchLabels.kubernetes\\.io/metadata\\.name=monitoring --set metrics.harness.remoteWrite.bearerTokenSecret.name=prom-rw --set metrics.harness.remoteWrite.bearerTokenSecret.key=token"
  "legacy-docker-dind|--set hardened=true --set config.sandboxMode=docker --set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany}"
  "cloud|--set hardened=true -f values-cloud.yaml --set config.sandboxMode=e2b ${INGRESS[*]}"
  "eks|-f values-eks.yaml --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets --set config.frontendPublicUrl=https://pipeshub.example.com --set config.allowedOrigins=https://pipeshub.example.com ${INGRESS[*]}"
  "eks-hardened|--set hardened=true -f values-eks.yaml --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets --set config.frontendPublicUrl=https://pipeshub.example.com --set config.allowedOrigins=https://pipeshub.example.com ${INGRESS[*]}"
  "eks-single-az|--set hardened=true -f values-eks.yaml -f values-eks-single-az.yaml --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets --set config.frontendPublicUrl=https://pipeshub.example.com --set config.allowedOrigins=https://pipeshub.example.com ${INGRESS[*]} --set-string nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string mongodb.builtin.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string redis.builtin.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string qdrant.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string neo4j.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set redis.builtin.enabled=false --set redis.external.enabled=true --set redis.external.host=redis.example.internal --set redis.clientTls.enabled=true"
)

KUBECONFORM=(
  kubeconform -strict -summary
  -schema-location default
  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
)

failed=0
if ! python3 -m unittest discover -s ../tests -p 'test_*.py' >"$OUT/unittest.log" 2>&1; then
  cat "$OUT/unittest.log"; failed=1
fi
python3 ../tests/check_values_refs.py . || failed=1

for entry in "${VARIANTS[@]}"; do
  name="${entry%%|*}"
  # Word-splitting is intended: every argument above is free of spaces.
  read -r -a args <<<"${entry#*|}"
  echo "== ${name}"
  if ! helm lint . "${SECRETS[@]}" "${args[@]}" --quiet >"$OUT/$name.lint" 2>&1; then
    cat "$OUT/$name.lint"; failed=1; continue
  fi
  if ! helm template ci . "${SECRETS[@]}" "${args[@]}" >"$OUT/$name.yaml" 2>"$OUT/$name.err"; then
    cat "$OUT/$name.err"; failed=1; continue
  fi
  for v in "${K8S_VERSIONS[@]}"; do
    "${KUBECONFORM[@]}" -kubernetes-version "$v" "$OUT/$name.yaml" || failed=1
  done
done

# Wiring that only matters with several replicas, which the kind install (one
# node) never exercises. The Confluent image reads ZOOKEEPER_SERVERS; with
# ZOO_SERVERS every replica runs standalone and Kafka splits across them.
expect() { # variant, grep -F pattern, "present" | "absent" [, only in the document naming this]
  local found=present doc="$OUT/$1.yaml"
  if [[ -n "${4:-}" ]]; then
    doc="$OUT/$1.doc.yaml"
    awk -v sel="$4" 'BEGIN { RS = "\n---" } index($0, sel)' "$OUT/$1.yaml" >"$doc"
  fi
  grep -qF -- "$2" "$doc" || found=absent
  if [[ "$found" != "$3" ]]; then
    echo "!! $1: expected '$2' to be $3${4:+ in $4}"; failed=1
  else
    echo "ok $1: '$2' $3${4:+ in $4}"
  fi
}
# The security profile (ADR-OPS-07): default values render what the chart before the programme rendered, so a
# `helm upgrade` changes nothing until the operator sets hardened=true (test_security_profile.py has the detail).
if [[ -f "$OUT/compat-kafka-builtin-mongo.yaml" ]]; then
  expect compat-kafka-builtin-mongo 'kind: NetworkPolicy' absent
  expect compat-kafka-builtin-mongo 'readOnlyRootFilesystem: false' present
  expect compat-kafka-builtin-mongo 'value: "docker"' present 'name: SANDBOX_MODE'
  expect compat-kafka-builtin-mongo 'value: "false"' present 'name: PIPESHUB_HARDENED'
  expect compat-kafka-builtin-mongo 'SASL_PLAINTEXT' absent
  expect compat-kafka-builtin-mongo 'chown 999:999 /keyfile/keyfile' present
fi
if [[ -f "$OUT/hardened-dind.yaml" ]]; then
  expect hardened-dind 'name: ci-pipeshub-ai-default-deny' present
  expect hardened-dind 'readOnlyRootFilesystem: true' present
  expect hardened-dind 'value: "broker"' present 'name: SANDBOX_MODE'
  expect hardened-dind 'value: "true"' present 'name: PIPESHUB_HARDENED'
  expect hardened-dind 'INTERNAL:SASL_PLAINTEXT,EXTERNAL:SASL_PLAINTEXT' present
  expect hardened-dind 'chown 999:999 /keyfile/keyfile' absent
fi
if [[ -f "$OUT/cloud.yaml" ]]; then
  # shellcheck disable=SC2016 # the literal template text, not an expansion
  expect cloud 'export ZOOKEEPER_SERVERS="$servers"' present
  expect cloud 'for i in {0..2}; do' present
  expect cloud 'publishNotReadyAddresses: true' present 'name: ci-pipeshub-ai-zookeeper-headless'
  expect cloud 'ZOO_SERVERS' absent
  expect local-neo4j-kafka 'ZOOKEEPER_SERVERS' absent
fi
if [[ -f "$OUT/eks.yaml" ]]; then
  expect eks 'ReadWriteMany' absent
  expect eks 'efs' absent
  expect eks 'bitnami/' absent
  expect eks 'app.kubernetes.io/component: kafka' absent
  expect eks 'app.kubernetes.io/component: zookeeper' absent
  expect eks '--bootstrap' present
  expect eks 'QDRANT__STORAGE__COLLECTION__REPLICATION_FACTOR' present
  expect eks 'value: "2"' present 'name: QDRANT__STORAGE__COLLECTION__REPLICATION_FACTOR'
  expect eks 'memory: 8Gi' present
  expect eks 'cpu: "4"' present
  expect eks 'replicaSet=rs0' present
  expect eks 'value: "redis"' present 'name: MESSAGE_BROKER'
  expect eks 'value: "redis"' present 'name: KV_STORE_TYPE'
  expect eks 'pipeshubai/pipeshub-sandbox:0.8.0' present
  expect eks 'force-sigv4.js' absent
  # The image drops root itself with setpriv, which fails once capabilities are dropped.
  # eks runs the sandbox broker (no docker-proxy in the app pod), so check the app Deployment itself.
  expect eks 'runAsUser: 1000' present 'name: NODE_LOG_FILES'
  expect local-neo4j-kafka 'force-sigv4.js' absent
  expect eks 'name: ci-mongodb-initiate-1' present
  expect eks 'helm.sh/hook' absent
  # The preset's policies follow `hardened` (ADR-OPS-07): an upgraded EKS release gets none, a hardened one the rules.
  expect eks 'kind: NetworkPolicy' absent
  expect eks-hardened 'cidr: 169.254.170.23/32' present
  expect eks 'name: DOCKER_HOST' present
  expect eks 'storageClassName: "gp3"' present
  # Neo4j is one pod. Qdrant is three. Count the StatefulSet replica lines by name.
  expect eks 'replicas: 3' present 'name: ci-pipeshub-ai-qdrant'
  expect eks 'whenUnsatisfiable: ScheduleAnyway' present 'name: ci-pipeshub-ai-qdrant'
  expect eks 'requiredDuringSchedulingIgnoredDuringExecution' present 'name: ci-pipeshub-ai-qdrant'
  expect eks 'replicas: 1' present 'name: ci-pipeshub-ai-neo4j'
fi
if [[ -f "$OUT/eks-single-az.yaml" ]]; then
  expect eks-single-az 'alb.ingress.kubernetes.io/scheme: internal' present
  expect eks-single-az 'topology.kubernetes.io/zone: us-east-1a' present
  expect eks-single-az 'rediss://' present
  expect eks-single-az 'pipeshub/role' absent
  expect eks-single-az 'topologyKey: topology.kubernetes.io/zone' absent
  expect eks-single-az 'app.kubernetes.io/component: redis' absent
  expect eks-single-az 'internet-facing' absent
  # The release here is "ci"; a selector naming another release matches no pods.
  expect eks-single-az 'topologyKey: kubernetes.io/hostname' present 'kind: Deployment'
  expect eks-single-az 'app.kubernetes.io/instance: pipeshub-ai' absent
fi

# MongoDB topology per preset: data-bearing members, arbiters, and the
# connection string the app gets. Local is a single-node replica set; the
# harness preset (cloud) and EKS are three data members with no arbiter, and
# only arbiter-free sets get w=majority. The chart default keeps its PSA set.
mongo_topology() { # variant, members, arbiters, "majority" | "no-majority"
  local doc="$OUT/$1.yaml" got
  [[ -f "$doc" ]] || return 0
  got="$(python3 - "$doc" <<'PY'
import sys, yaml
members = arbiters = 0
uri = rs_available = ""
for doc in yaml.safe_load_all(open(sys.argv[1], encoding="utf-8")):
    if not doc:
        continue
    name = doc.get("metadata", {}).get("name", "")
    if doc.get("kind") == "StatefulSet" and name == "ci-mongodb":
        members = doc["spec"]["replicas"]
    if doc.get("kind") == "StatefulSet" and name == "ci-mongodb-arbiter":
        arbiters = doc["spec"]["replicas"]
    if doc.get("kind") == "Deployment" and name == "ci-pipeshub-ai":
        for env in doc["spec"]["template"]["spec"]["containers"][0].get("env", []):
            if env["name"] == "MONGO_URI":
                uri = env.get("value", "")
            if env["name"] == "REPLICA_SET_AVAILABLE":
                rs_available = env.get("value", "")
majority = "majority" if "w=majority" in uri else "no-majority"
rs = "rs" if "replicaSet=rs0" in uri and rs_available == "true" else "no-rs"
print(members, arbiters, majority, rs)
PY
)"
  if [[ "$got" != "$2 $3 $4 rs" ]]; then
    echo "!! $1: expected mongodb '$2 $3 $4 rs' (members arbiters write-concern replicaSet), got '$got'"; failed=1
  else
    echo "ok $1: mongodb $got"
  fi
}
mongo_topology local-neo4j-kafka 1 0 majority
mongo_topology local-arangodb 1 0 majority
mongo_topology cloud 3 0 majority
mongo_topology eks 3 0 majority
mongo_topology defaults-dind 2 1 no-majority

# D1: the Docker daemon (privileged DinD) lives in the sandbox-broker pod, never
# in the app pods, unless the legacy sandboxMode=docker is chosen explicitly.
sandbox_topology() { # variant, broker | docker [, --netpol]
  local doc="$OUT/$1.yaml"
  [[ -f "$doc" ]] || return 0
  if python3 ../tests/sandbox_topology.py "$doc" "${@:2}"; then
    echo "ok $1: sandbox daemon topology ${*:2}"
  else
    echo "!! $1: sandbox daemon topology ${*:2}"; failed=1
  fi
}
# Unset config.sandboxMode follows `hardened` (ADR-OPS-07): docker by default, as before the broker; broker hardened.
sandbox_topology defaults-dind docker
sandbox_topology hardened-dind broker --netpol
sandbox_topology broker-netpol broker --netpol
sandbox_topology eks docker
sandbox_topology eks-hardened broker --netpol
sandbox_topology eks-single-az broker --netpol
sandbox_topology legacy-docker-dind docker
sandbox_topology broker-etcd broker --netpol

# ADR-OPS-04 (SEC-D-09): every pod default-denied, and the allowed paths exactly
# the expected ones; the broker reaches DNS, its KV port and public 443 only.
netpol() { # variant [netpol_check.py flags]
  local doc="$OUT/$1.yaml"
  [[ -f "$doc" ]] || return 0
  if python3 ../tests/netpol_check.py "$doc" "${@:2}"; then
    echo "ok $1: network policy paths"
  else
    echo "!! $1: network policy paths"; failed=1
  fi
}
for variant in local-neo4j-kafka local-redis-streams local-arangodb local-etcd local-redis-cluster-external \
               local-extras hardened-dind broker-netpol broker-etcd legacy-docker-dind cloud; do
  netpol "$variant"
done
netpol eks-hardened --pod-identity --allow-world-ingress 3001 8088
netpol eks-single-az --pod-identity --allow-world-ingress 3001 8088
[[ -f "$OUT/netpol-off.yaml" ]] && expect netpol-off 'kind: NetworkPolicy' absent
# The component, not the word: the app pod names the broker's key (ADR-KEY-02) in every mode.
[[ -f "$OUT/local-neo4j-kafka.yaml" ]] && expect local-neo4j-kafka 'app.kubernetes.io/component: sandbox-broker' absent
[[ -f "$OUT/local-neo4j-kafka.yaml" ]] && expect local-neo4j-kafka '--oplogSize=1024' present
# Legacy sandboxMode=docker: the app pod's DinD daemon must be reachable only
# through the policy proxy: no TCP listener (pod containers share one network
# namespace), and its socket volume mounted into dind and docker-proxy alone,
# never the app container. (Broker mode: sandbox_topology above checks the
# broker pod the same way.)
check_docker_proxy() { # variant
  local doc="$OUT/$1.yaml"
  [[ -f "$doc" ]] || return 0
  expect "$1" 'app.docker_proxy_main' present
  expect "$1" '--host=tcp://' absent
  if python3 - "$doc" <<'PY'; then echo "ok $1: dind socket reachable only through docker-proxy"; else failed=1; fi
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") == "Deployment"]
pod = next(d for d in docs if d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["spec"]
containers = {c["name"]: c for c in pod["containers"]}
errors = []
mounting = sorted(n for n, c in containers.items() if any(m["name"] == "dind-sock" for m in c.get("volumeMounts") or []))
if mounting != ["dind", "docker-proxy"]:
    errors.append(f"dind-sock is mounted into {mounting}, expected only dind and docker-proxy")
if any("tcp://" in a for a in containers["dind"].get("args") or []):
    errors.append("dind has a TCP listener")
env = {e["name"]: e.get("value") for e in containers["docker-proxy"].get("env") or []}
if env.get("DOCKER_PROXY_HOST") != "127.0.0.1":
    errors.append(f"docker-proxy DOCKER_PROXY_HOST is {env.get('DOCKER_PROXY_HOST')!r}, expected 127.0.0.1")
app_env = {e["name"]: e.get("value") for e in containers["pipeshub-ai"].get("env") or []}
if app_env.get("DOCKER_HOST") != f"tcp://127.0.0.1:{env.get('DOCKER_PROXY_PORT')}":
    errors.append(f"app DOCKER_HOST {app_env.get('DOCKER_HOST')!r} is not the docker-proxy listener")
# SEC-A-04: the proxy and the app share the app's credential; the proxy runs as no root.
for name, holder in (("docker-proxy", env), ("pipeshub-ai", app_env)):
    if "DOCKER_PROXY_TOKEN" not in holder:
        errors.append(f"{name} lacks DOCKER_PROXY_TOKEN")
if (containers["docker-proxy"].get("securityContext") or {}).get("runAsUser") in (None, 0):
    errors.append("docker-proxy runs as root")
for e in errors:
    print(f"!! {sys.argv[1]}: {e}")
sys.exit(1 if errors else 0)
PY
}
check_docker_proxy legacy-docker-dind

# A rotated key must restart both sides together. Secrets the chart does not
# create carry no checksum: the chart cannot see their value.
if [[ -f "$OUT/local-neo4j-kafka.yaml" && -f "$OUT/local-existing-secrets.yaml" && -f "$OUT/local-external-secrets.yaml" ]]; then
  expect local-neo4j-kafka 'checksum/qdrant-api-key' present 'name: ci-pipeshub-ai-qdrant'
  expect local-neo4j-kafka 'checksum/qdrant-api-key' present 'app.kubernetes.io/component: main'
  expect local-existing-secrets 'checksum/qdrant-api-key' absent
  expect local-external-secrets 'checksum/qdrant-api-key' absent
fi

# Secrets sourced outside the chart must not need qdrant.apiKey in values.
for entry in \
  "existing secrets without qdrant.apiKey|${LOCAL[*]} --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-app" \
  "external secrets without qdrant.apiKey|${LOCAL[*]} --set secretManagement.externalSecrets.enabled=true --set secretManagement.externalSecrets.secretStoreRef.name=vault --set secretManagement.externalSecrets.remoteRefs.secretKey=pipeshub/secret-key"; do
  name="${entry%%|*}"
  read -r -a args <<<"${entry#*|}"
  if helm template ci . "${args[@]}" >/dev/null 2>"$OUT/accepted.err"; then
    echo "ok accepted: ${name}"
  else
    echo "!! ${name}: refused:"; cat "$OUT/accepted.err"; failed=1
  fi
done

# name | expected message fragment | helm arguments (after SECRETS)
REFUSED=(
  "no graph database|No graph database is enabled|${LOCAL[*]} --set neo4j.enabled=false"
  "etcd store without etcd|config.kvStoreType=etcd requires etcd.enabled=true|${LOCAL[*]} --set config.kvStoreType=etcd"
  "external secrets without secret-key|remoteRefs.secretKey is required|${LOCAL[*]} --set secretManagement.externalSecrets.enabled=true --set secretManagement.externalSecrets.secretStoreRef.name=vault"
  "placeholder neo4j password|must not use default placeholder|${LOCAL[*]} --set neo4j.auth.password=your_password"
  "no qdrant api key|qdrant.apiKey is required|${LOCAL[*]} --set qdrant.apiKey="
  "placeholder qdrant api key|qdrant.apiKey must not use default placeholder 'api_key'|${LOCAL[*]} --set qdrant.apiKey=api_key"
  "default arango password|arango.auth.rootPassword must not use default placeholder 'root'|${LOCAL[*]} --set neo4j.enabled=false --set arango.enabled=true --set arango.auth.rootPassword=root"
  "broker sandbox without a daemon|config.sandboxMode is \"broker\" but no Docker daemon is configured|--set hardened=true --set persistence.accessModes={ReadWriteMany}"
  "default sandbox without a daemon, as before the broker|config.sandboxMode is \"docker\" but no Docker daemon is configured|--set persistence.accessModes={ReadWriteMany}"
  "legacy docker sandbox without a daemon|config.sandboxMode is \"docker\" but no Docker daemon is configured|--set config.sandboxMode=docker --set persistence.accessModes={ReadWriteMany}"
  "shared RWO volume across replicas|persistence requires ReadWriteMany|--set sandbox.dind.enabled=true"
  "docker socket mounted into the app|mounts a Docker socket into the application container|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/var/run/docker.sock --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/var/run/docker.sock"
  "docker socket mounted under another path|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/var/run/docker.sock --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/tmp/d"
  "dind socket volume mounted into the app|mounts a Docker socket into the application container|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set extraVolumeMounts[0].name=dind-sock --set extraVolumeMounts[0].mountPath=/var/run/dind"
  "host /var/run directory mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/var/run/ --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/host-run"
  "host /run directory mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=//run --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/host-run"
  "host root mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/ --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/host"
  "containerd socket directory mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/run/containerd --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/c"
  "k3s containerd socket mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/run/k3s/containerd/containerd.sock --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/c"
  "k3s directory under /run mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/run/k3s --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/runtime"
  "k3s directory under /var/run mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/var/run/k3s --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/runtime"
  "podman directory under /var/run mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/var/run/podman --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/runtime"
  "dockershim socket under /run mounted into the app|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/run/dockershim.sock --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/runtime"
  "any hostPath of type Socket|mounts a Docker socket into the application container|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/opt/engine/api.sock --set extraVolumes[0].hostPath.type=Socket --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/e"
  "docker socket picked out by subPath|mounts a Docker socket into the application container|${LOCAL[*]} --set-json extraVolumes=[{\"name\":\"s\",\"emptyDir\":{}}] --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/tmp/d --set extraVolumeMounts[0].subPath=docker.sock"
  "cluster mode on the bundled redis|requires redis.external.enabled=true|${LOCAL[*]} --set redis.mode=cluster"
  "external host and cluster endpoints|cannot both be set|${LOCAL[*]} --set redis.enabled=false --set redis.external.enabled=true --set redis.external.host=redis.example --set redis.external.clusterEndpoints=redis.example:6379 --set redis.mode=cluster"
  "external host with cluster mode|requires redis.mode=standalone|${LOCAL[*]} --set redis.enabled=false --set redis.external.enabled=true --set redis.external.host=redis.example --set redis.mode=cluster"
  "neo4j community replicas|requires an Enterprise image|${LOCAL[*]} --set neo4j.replicaCount=2"
  "both mongodb charts|cannot both be true|--set mongodb.enabled=true --set mongodb.builtin.enabled=true --set persistence.enabled=false --set config.sandboxMode=local --set config.sandboxAllowLocal=true"
  "local sandbox without the dev flag|refuses it unless config.sandboxAllowLocal=true|${LOCAL[*]} --set config.sandboxAllowLocal=false"
  "local sandbox with a string false flag|refuses it unless config.sandboxAllowLocal=true|${LOCAL[*]} --set-string config.sandboxAllowLocal=false"
  "harness metrics with nowhere to write|needs metrics.harness.remoteWrite.url|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set metrics.harness.enabled=true"
  "event-loop autoscaling without metrics|needs metrics.enabled|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set autoscaling.eventLoopUtilization.enabled=true --set metrics.enabled=false"
)
for entry in "${REFUSED[@]}"; do
  IFS='|' read -r name message rest <<<"$entry"
  read -r -a args <<<"$rest"
  if helm template ci . "${SECRETS[@]}" "${args[@]}" >/dev/null 2>"$OUT/refused.err"; then
    echo "!! ${name}: rendered, but the chart should refuse it"; failed=1
  elif ! grep -qF -- "$message" "$OUT/refused.err"; then
    echo "!! ${name}: refused with an unexpected message:"; cat "$OUT/refused.err"; failed=1
  else
    echo "ok refused: ${name}"
  fi
done

# The socket checks must not refuse ordinary host paths that only look similar.
ACCEPTED=(
  "host log directory|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/var/log/pipeshub --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/logs"
  "host path sharing the /var/run prefix|${LOCAL[*]} --set extraVolumes[0].name=s --set extraVolumes[0].hostPath.path=/var/runner-cache --set extraVolumeMounts[0].name=s --set extraVolumeMounts[0].mountPath=/cache"
)
for entry in "${ACCEPTED[@]}"; do
  IFS='|' read -r name rest <<<"$entry"
  read -r -a args <<<"$rest"
  if helm template ci . "${SECRETS[@]}" "${args[@]}" >/dev/null 2>"$OUT/accepted.err"; then
    echo "ok rendered: ${name}"
  else
    echo "!! ${name}: refused, but it is not a runtime socket:"; cat "$OUT/accepted.err"; failed=1
  fi
done

# The no-daemon error used to suggest mounting the node's socket.
if helm template ci . "${SECRETS[@]}" --set 'persistence.accessModes={ReadWriteMany}' >/dev/null 2>"$OUT/refused.err" \
   || grep -qF 'docker.sock' "$OUT/refused.err"; then
  echo "!! docker sandbox without a daemon: error must not suggest mounting docker.sock"; failed=1
else
  echo "ok refused: docker sandbox without a daemon suggests no socket mount"
fi

if [[ "$failed" -ne 0 ]]; then
  echo "check_chart: FAILED" >&2
  exit 1
fi
echo "check_chart: all variants valid"

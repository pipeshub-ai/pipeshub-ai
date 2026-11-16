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
AWS_DIR="$(cd ../aws && pwd)"
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
LOCAL=(-f values-local.yaml)

# name, then the helm arguments for that variant. They come after SECRETS so a
# variant can override one of them.
VARIANTS=(
  "local-neo4j-kafka|${LOCAL[*]}"
  "local-parsing|${LOCAL[*]} --set parsing.enabled=true --set parsing.replicas=3 --set parsing.maxConcurrent=2 --set parsing.clientConcurrency=8 --set-string parsing.nodeSelector.pool=parse --set parsing.tolerations[0].key=parse --set parsing.tolerations[0].operator=Exists --set parsing.extraEnv[0].name=PARSING_UVICORN_WORKERS --set-string parsing.extraEnv[0].value=4 --set parsing.extraEnv[1].name=extra-marker --set-string parsing.extraEnv[1].value=x"
  "local-parsing-netpol|${LOCAL[*]} --set parsing.enabled=true --set networkPolicy.enabled=true --set parsing.networkPolicy.extraIngress[0].from[0].namespaceSelector.matchLabels.team=monitoring --set parsing.networkPolicy.extraIngress[0].ports[0].port=8092"
  "local-redis-streams|${LOCAL[*]} --set messageBroker.type=redis"
  "local-arangodb|${LOCAL[*]} --set neo4j.enabled=false --set arango.enabled=true --set arango.auth.rootPassword=ci-arango"
  "local-etcd|${LOCAL[*]} --set etcd.enabled=true --set config.kvStoreType=etcd"
  "local-redis-cluster-external|${LOCAL[*]} --set redis.enabled=false --set redis.external.enabled=true --set redis.mode=cluster --set redis.external.clusterEndpoints=redis-0:6379 --set celery.brokerUrl=redis://celery:6379/0"
  "local-metrics|${LOCAL[*]} --set metrics.podMonitor.enabled=true --set metrics.podMonitor.labels.release=prom"
  "local-metrics-bind|${LOCAL[*]} --set metrics.bind=0.0.0.0"
  "local-extras|${LOCAL[*]} --set networkPolicy.enabled=true ${INGRESS[*]}"
  "local-external-secrets|${LOCAL[*]} --set secretManagement.externalSecrets.enabled=true --set secretManagement.externalSecrets.secretStoreRef.name=vault --set secretManagement.externalSecrets.remoteRefs.secretKey=pipeshub/secret-key --set secretManagement.externalSecrets.remoteRefs.mongodbPassword=pipeshub/mongo --set secretManagement.externalSecrets.remoteRefs.redisPassword=pipeshub/redis --set secretManagement.externalSecrets.remoteRefs.neo4jPassword=pipeshub/neo4j"
  "local-existing-secrets|${LOCAL[*]} --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-app"
  "defaults-dind|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany}"
  "cloud|-f values-cloud.yaml --set config.sandboxMode=e2b ${INGRESS[*]}"
  "eks|-f values-eks.yaml --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets --set config.frontendPublicUrl=https://pipeshub.example.com --set config.allowedOrigins=https://pipeshub.example.com ${INGRESS[*]}"
  "eks-parsing|-f values-eks.yaml --set parsing.enabled=true --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets --set config.frontendPublicUrl=https://pipeshub.example.com --set config.allowedOrigins=https://pipeshub.example.com ${INGRESS[*]}"
  "eks-single-az-parsing|-f values-eks.yaml -f values-eks-single-az.yaml --set parsing.enabled=true --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets --set config.frontendPublicUrl=https://pipeshub.example.com --set config.allowedOrigins=https://pipeshub.example.com ${INGRESS[*]} --set redis.builtin.enabled=false --set redis.external.enabled=true --set redis.external.host=redis.example.internal --set redis.clientTls.enabled=true"
  "eks-single-az|-f values-eks.yaml -f values-eks-single-az.yaml --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-ai-secrets --set config.frontendPublicUrl=https://pipeshub.example.com --set config.allowedOrigins=https://pipeshub.example.com ${INGRESS[*]} --set-string nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string mongodb.builtin.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string redis.builtin.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string qdrant.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set-string neo4j.nodeSelector.topology\\.kubernetes\\.io/zone=us-east-1a --set redis.builtin.enabled=false --set redis.external.enabled=true --set redis.external.host=redis.example.internal --set redis.clientTls.enabled=true"
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
if [[ -f "$OUT/local-metrics.yaml" ]]; then
  expect local-metrics 'kind: PodMonitor' present
  expect local-metrics 'port: metrics' present 'kind: PodMonitor'
  expect local-metrics 'release: prom' present 'kind: PodMonitor'
  expect local-metrics 'name: PIPESHUB_METRICS_ENABLED' present
  expect local-metrics 'name: PIPESHUB_METRICS_BIND' present
  expect local-metrics 'fieldPath: status.podIP' present
  expect local-metrics-bind 'name: PIPESHUB_METRICS_BIND' present
  expect local-metrics-bind 'fieldPath: status.podIP' absent
  expect local-neo4j-kafka 'kind: PodMonitor' absent
  expect local-neo4j-kafka 'name: PIPESHUB_METRICS_PORT' present
fi
if [[ -f "$OUT/cloud.yaml" ]]; then
  # shellcheck disable=SC2016 # the literal template text, not an expansion
  expect cloud 'export ZOOKEEPER_SERVERS="$servers"' present
  expect cloud 'for i in {0..2}; do' present
  expect cloud 'publishNotReadyAddresses: true' present 'name: ci-pipeshub-ai-zookeeper-headless'
  expect cloud 'ZOO_SERVERS' absent
  expect local-neo4j-kafka 'ZOOKEEPER_SERVERS' absent
fi
# Parsing in its own pods: nothing renders by default; enabled, the Deployment,
# Service and PDB use the documented names and labels, and the
# app pods are pointed at the Service.
if [[ -f "$OUT/local-parsing.yaml" ]]; then
  expect local-neo4j-kafka 'ci-pipeshub-ai-parsing' absent
  expect local-neo4j-kafka 'PIPESHUB_ROLE' absent
  expect local-neo4j-kafka 'USE_PARSING_SERVICE' absent
  expect local-parsing 'name: ci-pipeshub-ai-parsing' present
  expect local-parsing 'pipeshub.ai/workload: parsing' present
  expect local-parsing 'replicas: 3' present 'kind: Deployment'
  expect local-parsing 'value: parsing' present 'name: PIPESHUB_ROLE'
  expect local-parsing 'path: /health' present 'name: PIPESHUB_ROLE'
  expect local-parsing 'cpu: "1"' present 'name: PIPESHUB_ROLE'
  expect local-parsing 'pool: parse' present 'name: PIPESHUB_ROLE'
  expect local-parsing 'kind: PodDisruptionBudget' present 'name: ci-pipeshub-ai-parsing'
  expect local-parsing 'HorizontalPodAutoscaler' absent 'name: ci-pipeshub-ai-parsing'
  expect local-parsing 'http://ci-pipeshub-ai-parsing:8092' present 'name: PARSING_SERVICE_URL'
  expect local-parsing 'value: "2"' present 'name: PARSING_MAX_CONCURRENT'
  expect local-parsing 'value: "8"' present 'name: PARSING_SERVICE_CONCURRENCY'
  expect local-parsing 'name: PARSING_UVICORN_WORKERS' present 'name: PIPESHUB_ROLE'
  expect local-parsing 'path: /health/ready' present 'name: PIPESHUB_ROLE'
  # A scale-down or rollout gives an in-flight parse this long to finish before the kill.
  expect local-parsing 'terminationGracePeriodSeconds: 120' present 'name: PIPESHUB_ROLE'
  # The parsing pods must not match the main Deployment's selector.
  expect local-parsing 'app.kubernetes.io/name: pipeshub-ai-parsing' present 'name: ci-pipeshub-ai-parsing'
  expect local-parsing 'kind: NetworkPolicy' absent 'name: ci-pipeshub-ai-parsing'
  expect local-parsing 'value: "4"' absent 'name: PARSING_UVICORN_WORKERS'
  expect local-parsing 'extra-marker' present 'name: PIPESHUB_ROLE'
  expect local-neo4j-kafka 'PARSING_MAX_CONCURRENT' absent
  expect local-neo4j-kafka 'PARSING_SERVICE_CONCURRENCY' absent
fi
# The parsing pods' own NetworkPolicy: only with networkPolicy.enabled and parsing.enabled, selecting only the
# parsing pods, admitting the application's selector on the parsing port (and what extraIngress lists).
netpol_check() { # rendered file, "present" | "absent"
  python3 - "$1" "$2" <<'PY' || failed=1
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") == "NetworkPolicy"]
parsing = [d for d in docs if d["metadata"]["name"].endswith("-parsing")]
if sys.argv[2] == "absent":
    assert not parsing, "parsing NetworkPolicy rendered without both flags"
    print("ok", sys.argv[1], "no parsing NetworkPolicy")
    raise SystemExit
assert len(parsing) == 1, "want exactly one parsing NetworkPolicy"
spec = parsing[0]["spec"]
sel = spec["podSelector"]["matchLabels"]
assert sel["app.kubernetes.io/name"].endswith("-parsing") and sel["pipeshub.ai/workload"] == "parsing", sel
main = [d for d in docs if d is not parsing[0]]
assert all(d["spec"]["podSelector"]["matchLabels"]["app.kubernetes.io/name"] != sel["app.kubernetes.io/name"] for d in main), "main policy selects parsing pods"
first = spec["ingress"][0]
assert first["ports"] == [{"protocol": "TCP", "port": 8092}], first
(source,) = first["from"]
assert list(source) == ["podSelector"], source
app = source["podSelector"]["matchLabels"]
assert app["app.kubernetes.io/name"] and not app["app.kubernetes.io/name"].endswith("-parsing"), app
# The rule must select the application pods: every selector label is a label of the main Deployment's pods.
all_docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
main_pod = next(d for d in all_docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["metadata"]["labels"]
assert all(main_pod.get(k) == v for k, v in app.items()), (app, main_pod)
assert app.get("app.kubernetes.io/instance"), app
extra = spec["ingress"][1:]
assert extra == [{"from": [{"namespaceSelector": {"matchLabels": {"team": "monitoring"}}}], "ports": [{"port": 8092}]}], extra
assert spec["egress"], "no egress rules"
print("ok", sys.argv[1], "parsing NetworkPolicy: app selector on 8092 only, plus extraIngress")
PY
}
netpol_check "$OUT/local-parsing-netpol.yaml" present
# Parsing pod labels, rendered alone (the main workload's labels do not take these inputs): the name stays within
# 63 characters, the selector keys are not overridable from global.commonLabels, other common labels are kept.
helm template ci . "${SECRETS[@]}" "${LOCAL[@]}" --set parsing.enabled=true --show-only templates/parsing.yaml \
  --set nameOverride=a-very-long-release-name-that-is-nearly-at-the-sixty-three-limit \
  --set-string global.commonLabels.team=platform --set-string 'global.commonLabels.app\.kubernetes\.io/instance=hijack' >"$OUT/parsing-labels.yaml"
helm template ci . "${SECRETS[@]}" "${LOCAL[@]}" --set parsing.enabled=true --show-only templates/deployment.yaml \
  --set nameOverride=a-very-long-release-name-that-is-nearly-at-the-sixty-three-limit >"$OUT/parsing-url.yaml"
python3 - "$OUT/parsing-labels.yaml" "$OUT/parsing-url.yaml" <<'PY' || failed=1
import sys, yaml
dep = next(d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d["kind"] == "Deployment")
pod = dep["spec"]["template"]["metadata"]["labels"]
sel = dep["spec"]["selector"]["matchLabels"]
assert len(pod["app.kubernetes.io/name"]) <= 63, pod["app.kubernetes.io/name"]
assert pod["app.kubernetes.io/instance"] != "hijack", pod
assert all(pod[k] == v for k, v in sel.items()), (pod, sel)
assert pod["team"] == "platform", pod
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
names = {d["kind"]: d["metadata"]["name"] for d in docs if d["kind"] in ("Deployment", "Service")}
assert all(len(n) <= 63 for n in names.values()), names
app = next(d for d in yaml.safe_load_all(open(sys.argv[2])) if d and d["kind"] == "Deployment")
env = {e["name"]: e.get("value") for c in app["spec"]["template"]["spec"]["containers"] for e in c.get("env", [])}
assert env["PARSING_SERVICE_URL"] == f"http://{names['Service']}:8092", (env["PARSING_SERVICE_URL"], names)
print("ok parsing pod labels: name within 63, selector keys not overridable, common labels kept")
PY
netpol_check "$OUT/local-parsing.yaml" absent
netpol_check "$OUT/local-extras.yaml" absent
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
  expect eks 'runAsUser: 1000' present 'name: DOCKER_PROXY_UPSTREAM'
  expect local-neo4j-kafka 'force-sigv4.js' absent
  expect eks 'name: ci-mongodb-initiate-1' present
  expect eks 'helm.sh/hook' absent
  expect eks 'cidr: 169.254.170.23/32' present
  expect eks 'name: DOCKER_HOST' present
  expect eks 'storageClassName: "gp3"' present
  # Neo4j is one pod. Qdrant is three. Count the StatefulSet replica lines by name.
  expect eks 'replicas: 3' present 'name: ci-pipeshub-ai-qdrant'
  expect eks 'whenUnsatisfiable: ScheduleAnyway' present 'name: ci-pipeshub-ai-qdrant'
  expect eks 'requiredDuringSchedulingIgnoredDuringExecution' present 'name: ci-pipeshub-ai-qdrant'
  expect eks 'replicas: 1' present 'name: ci-pipeshub-ai-neo4j'
fi
# The EKS preset puts parsing on its own tainted node group, sized for the
# c7i.2xlarge in aws/cluster.yaml. Off, nothing renders; on, three 2 CPU pods
# fit a node, and the application is told how many parses it may keep in flight.
if [[ -f "$OUT/eks-parsing.yaml" ]]; then
  expect eks 'ci-pipeshub-ai-parsing' absent
  expect eks 'USE_PARSING_SERVICE' absent
  expect eks-parsing 'name: ci-pipeshub-ai-parsing' present
  expect eks-parsing 'replicas: 2' present 'name: PIPESHUB_ROLE'
  expect eks-parsing 'pipeshub/role: parsing' present 'name: PIPESHUB_ROLE'
  expect eks-parsing 'effect: NoSchedule' present 'name: PIPESHUB_ROLE'
  expect eks-parsing 'cpu: "2"' present 'name: PIPESHUB_ROLE'
  expect eks-parsing 'memory: 8Gi' present 'name: PIPESHUB_ROLE'
  expect eks-parsing 'value: "2"' present 'name: PARSING_MAX_CONCURRENT'
  expect eks-parsing 'value: "4"' present 'name: PARSING_SERVICE_CONCURRENCY'
  expect eks-parsing 'HorizontalPodAutoscaler' absent 'name: ci-pipeshub-ai-parsing'
fi
if [[ -f "$OUT/eks-single-az.yaml" ]]; then
  expect eks-single-az 'alb.ingress.kubernetes.io/scheme: internal' present
  expect eks-single-az 'topology.kubernetes.io/zone: us-east-1a' present
  expect eks-single-az 'rediss://' present
  expect eks-single-az 'pipeshub/role' absent
  expect eks-single-az 'topologyKey: topology.kubernetes.io/zone' absent
  expect eks-single-az 'app.kubernetes.io/component: redis' absent
  expect eks-single-az 'internet-facing' absent
  # A preset that clears the parsing node pin must not render the cleared key (a null label value is refused by the API).
  expect eks-single-az-parsing 'name: ci-pipeshub-ai-parsing' present
  expect eks-single-az-parsing 'pipeshub/role: null' absent
  expect eks-single-az-parsing 'pipeshub/role: parsing' absent
  expect eks-single-az-parsing 'key: pipeshub/role' present 'name: ci-pipeshub-ai-parsing'
  # The release here is "ci"; a selector naming another release matches no pods.
  expect eks-single-az 'topologyKey: kubernetes.io/hostname' present 'kind: Deployment'
  expect eks-single-az 'app.kubernetes.io/instance: pipeshub-ai' absent
fi

# The DinD daemon must be reachable only through the policy proxy: no TCP
# listener (pod containers share one network namespace), and its socket volume
# mounted into dind and docker-proxy alone, never the app container.
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
for e in errors:
    print(f"!! {sys.argv[1]}: {e}")
sys.exit(1 if errors else 0)
PY
}
check_docker_proxy defaults-dind
check_docker_proxy eks
check_docker_proxy eks-single-az

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
  "docker sandbox without a daemon|no Docker daemon is configured|--set persistence.accessModes={ReadWriteMany}"
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
  "parsing name colliding with the app name|equals the main application's name|${LOCAL[*]} --set parsing.enabled=true --set nameOverride=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-parsing"
  "parsing object names colliding with the app names|equals the main application's name|${LOCAL[*]} --set parsing.enabled=true --set nameOverride=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-parsing"
  "parsing object names colliding, shorter override|equals the main application's name|${LOCAL[*]} --set parsing.enabled=true --set nameOverride=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-parsing"
  "local sandbox with a string false flag|refuses it unless config.sandboxAllowLocal=true|${LOCAL[*]} --set-string config.sandboxAllowLocal=false"
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

# The eksctl config: it parses, and the parsing node group carries the taint and label and
# a max that fits the pods the chart sizes for it.
if python3 - "$AWS_DIR" "$OUT" <<'PY'; then echo "ok cluster.yaml: parsing node group"; else failed=1; fi
import subprocess, sys, yaml
aws_dir, out = sys.argv[1:3]
render = subprocess.run(
    ["bash", f"{aws_dir}/deploy.sh", "--render-cluster-config", "--region", "eu-west-1",
     "--zones", "eu-west-1a,eu-west-1b,eu-west-1c", "--kms-key-arn", "arn:aws:kms:eu-west-1:111122223333:key/x",
     "--k8s-version", "1.36", "--cluster", "acme"],
    capture_output=True, text=True)
errors = []
if render.returncode:
    errors.append(f"render-cluster-config failed: {render.stderr}")
else:
    cfg = yaml.safe_load(render.stdout)
    groups = {g["name"]: g for g in cfg["managedNodeGroups"]}
    g = groups.get("parsing")
    if not g:
        errors.append("no parsing node group")
    else:
        if g["instanceType"] != "c7i.2xlarge": errors.append(f"instance type {g['instanceType']}")
        if (g["minSize"], g["desiredCapacity"]) != (1, 1) or g["maxSize"] < 2: errors.append("min/desired must be 1, max at least 2")
        if g["labels"] != {"pipeshub/role": "parsing"}: errors.append(f"labels {g['labels']}")
        if g["taints"] != [{"key": "pipeshub/role", "value": "parsing", "effect": "NoSchedule"}]: errors.append(f"taints {g['taints']}")
        if g["availabilityZones"] != ["eu-west-1a", "eu-west-1b", "eu-west-1c"]: errors.append("zones not rewritten")
    for n, g in groups.items():
        if "tags" in g: errors.append(f"{n} carries tags {g['tags']}")
for e in errors:
    print("!! cluster.yaml:", e)
sys.exit(1 if errors else 0)
PY

if [[ "$failed" -ne 0 ]]; then
  echo "check_chart: FAILED" >&2
  exit 1
fi
echo "check_chart: all variants valid"

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
EXTERNAL=(--set config.dockerHost=tcp://d:2375 --set persistence.accessModes={ReadWriteMany})

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
  "local-metrics|${LOCAL[*]}"
  "local-postgres-exporter|${LOCAL[*]} --set postgresExporter.enabled=true --set postgresExporter.dataSource=pg.example:5432/pipeshub?sslmode=require --set postgresExporter.passwordSecret.name=pg-monitor"
  "local-metrics-bind|${LOCAL[*]} --set metrics.bind=0.0.0.0"
  "local-extras|${LOCAL[*]} --set networkPolicy.enabled=true ${INGRESS[*]}"
  "local-external-secrets|${LOCAL[*]} --set secretManagement.externalSecrets.enabled=true --set secretManagement.externalSecrets.secretStoreRef.name=vault --set secretManagement.externalSecrets.remoteRefs.secretKey=pipeshub/secret-key --set secretManagement.externalSecrets.remoteRefs.mongodbPassword=pipeshub/mongo --set secretManagement.externalSecrets.remoteRefs.redisPassword=pipeshub/redis --set secretManagement.externalSecrets.remoteRefs.neo4jPassword=pipeshub/neo4j"
  "local-existing-secrets|${LOCAL[*]} --set secretManagement.existingSecrets.enabled=true --set secretManagement.existingSecrets.appSecretName=pipeshub-app"
  "defaults-dind|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany}"
  "dind-run-cap|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set config.sandboxMaxRunTimeoutS=900"
  "dind-live-off|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.enabled=false"
  "dind-live-tuned|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHigh=0.85 --set sandbox.dind.liveLimit.cpuThrottle=0.3 --set sandbox.capacity.memoryOvercommit=2"
  "cloud|-f values-cloud.yaml --set config.sandboxMode=e2b ${INGRESS[*]}"
  "external-daemon-2-pods|${EXTERNAL[*]} --set replicaCount=2 --set autoscaling.enabled=false"
  "external-daemon-max-total|${EXTERNAL[*]} --set replicaCount=2 --set autoscaling.enabled=false --set sandbox.capacity.maxTotal=10"
  "external-daemon-max-total-0|${EXTERNAL[*]} --set replicaCount=2 --set autoscaling.enabled=false --set sandbox.capacity.maxTotal=0"
  "dind-max-total-0|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set-string sandbox.capacity.maxTotal=0 --set sandbox.proxy.reapUnlabeled=true"
  "dind-capacity-zeros|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.capacity.slotWaitSeconds=0 --set sandbox.capacity.maxPerOrg=0"
  "external-daemon-max-total-float|${EXTERNAL[*]} --set replicaCount=2 --set autoscaling.enabled=false --set-string sandbox.capacity.maxTotal=4.0"
  "external-daemon-1-pod|${EXTERNAL[*]} --set replicaCount=1 --set autoscaling.enabled=false"
  "external-daemon-autoscaled|${EXTERNAL[*]} --set replicaCount=1 --set autoscaling.enabled=true --set autoscaling.maxReplicas=3"
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
  expect local-metrics 'kind: PodMonitor' absent
  expect local-metrics 'name: metrics' present
  expect local-metrics 'name: PIPESHUB_METRICS_ENABLED' present
  expect local-metrics 'name: PIPESHUB_METRICS_BIND' present
  expect local-metrics 'fieldPath: status.podIP' present
  expect local-metrics-bind 'name: PIPESHUB_METRICS_BIND' present
  expect local-metrics-bind 'fieldPath: status.podIP' absent
  expect local-neo4j-kafka 'name: PIPESHUB_METRICS_PORT' present
fi
# The PostgreSQL exporter: nothing by default; enabled, its own pods (not selected by the main Deployment) log in as the monitoring role.
if [[ -f "$OUT/local-postgres-exporter.yaml" ]]; then
  expect local-neo4j-kafka 'postgres-exporter' absent
  expect local-postgres-exporter 'app.kubernetes.io/name: pipeshub-ai-postgres-exporter' present 'name: ci-pipeshub-ai-postgres-exporter'
  expect local-postgres-exporter 'value: "pipeshub_monitor"' present 'name: DATA_SOURCE_USER'
  expect local-postgres-exporter 'name: "pg-monitor"' present 'name: DATA_SOURCE_PASS'
  expect local-postgres-exporter 'kind: Service' present 'port: 9187'
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
check_docker_proxy() { # variant [expected SANDBOX_MAX_RUN_TIMEOUT_S, default 300]
  local doc="$OUT/$1.yaml"
  [[ -f "$doc" ]] || return 0
  expect "$1" 'app.docker_proxy_main' present
  expect "$1" '--host=tcp://' absent
  if python3 - "$doc" "${2:-300}" <<'PY'; then echo "ok $1: dind socket reachable only through docker-proxy"; else failed=1; fi
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
# The proxy's reaper age follows the run-time cap, so both must get the same value.
cap = sys.argv[2]
if app_env.get("SANDBOX_MAX_RUN_TIMEOUT_S") != cap or env.get("SANDBOX_MAX_RUN_TIMEOUT_S") != cap:
    errors.append(
        f"SANDBOX_MAX_RUN_TIMEOUT_S is {app_env.get('SANDBOX_MAX_RUN_TIMEOUT_S')!r} on the app and "
        f"{env.get('SANDBOX_MAX_RUN_TIMEOUT_S')!r} on docker-proxy, expected {cap!r} on both"
    )
if env.get("DOCKER_PROXY_INSTANCE") != "default/ci-pipeshub-ai":
    errors.append(f"docker-proxy DOCKER_PROXY_INSTANCE is {env.get('DOCKER_PROXY_INSTANCE')!r}")
for e in errors:
    print(f"!! {sys.argv[1]}: {e}")
sys.exit(1 if errors else 0)
PY
}
check_docker_proxy defaults-dind
check_docker_proxy dind-run-cap 900
check_docker_proxy dind-live-off
check_docker_proxy eks
check_docker_proxy eks-single-az

# The code sandbox cap is sized from the dind sidecar's limits, passed in as
# env; pods sharing an external daemon each take their share of it.
app_env() { # variant name -> "missing" or the env entry as JSON
  python3 - "$OUT/$1.yaml" "$2" <<'PY'
import json, sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") == "Deployment"]
pod = next(d for d in docs if d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["spec"]
app = next(c for c in pod["containers"] if c["name"] == "pipeshub-ai")
entry = next((e for e in app.get("env") or [] if e["name"] == sys.argv[2]), None)
print("missing" if entry is None else json.dumps(entry.get("value", entry.get("valueFrom")), sort_keys=True))
PY
}
expect_env() { # variant, name, expected
  local doc="$OUT/$1.yaml" got
  [[ -f "$doc" ]] || return 0
  got="$(app_env "$1" "$2")"
  if [[ "$got" == "$3" ]]; then echo "ok $1: $2 = $3"; else echo "!! $1: $2 is $got, expected $3"; failed=1; fi
}
for v in defaults-dind eks; do
  expect_env "$v" SANDBOX_HOST_MEMORY_BYTES '{"resourceFieldRef": {"containerName": "dind", "divisor": "1", "resource": "limits.memory"}}'
  expect_env "$v" SANDBOX_HOST_CPU_MILLIS '{"resourceFieldRef": {"containerName": "dind", "divisor": "1m", "resource": "limits.cpu"}}'
  expect_env "$v" SANDBOX_MEMORY_SHARE '"0.9"'
  expect_env "$v" SANDBOX_CAPACITY_DIVISOR missing
  if [[ -f "$OUT/$v.yaml" ]] && python3 - "$OUT/$v.yaml" <<'PY'; then echo "ok $v: dind requests equal limits"; else failed=1; fi
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") == "Deployment"]
pod = next(d for d in docs if d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["spec"]
res = next(c for c in pod["containers"] if c["name"] == "dind")["resources"]
if res.get("requests") != res.get("limits"):
    print(f"!! {sys.argv[1]}: dind requests {res.get('requests')} differ from limits {res.get('limits')}")
    sys.exit(1)
PY
done
for name in SANDBOX_HOST_MEMORY_BYTES SANDBOX_MEMORY_SHARE SANDBOX_CAPACITY_DIVISOR SANDBOX_MAX_TOTAL; do
  expect_env cloud "$name" missing
done
expect_env external-daemon-2-pods SANDBOX_CAPACITY_DIVISOR '"2"'
expect_env external-daemon-2-pods SANDBOX_HOST_MEMORY_BYTES missing
expect_env external-daemon-max-total SANDBOX_CAPACITY_DIVISOR missing
expect_env external-daemon-max-total SANDBOX_MAX_TOTAL '"10"'
# maxTotal 0 means "sized automatically", like empty: never the app's 1024,
# and pods sharing an external daemon still split it.
expect_env external-daemon-max-total-0 SANDBOX_MAX_TOTAL missing
expect_env external-daemon-max-total-0 SANDBOX_CAPACITY_DIVISOR '"2"'
expect_env dind-max-total-0 SANDBOX_MAX_TOTAL missing

# docker-proxy reaps unlabelled sandbox containers only when asked.
proxy_env() { # variant, name -> value or "missing"
  python3 - "$OUT/$1.yaml" "$2" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") == "Deployment"]
pod = next(d for d in docs if d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["spec"]
proxy = next(c for c in pod["containers"] if c["name"] == "docker-proxy")
print(next((e.get("value") for e in proxy.get("env") or [] if e["name"] == sys.argv[2]), "missing"))
PY
}
for pair in defaults-dind:false dind-max-total-0:true; do
  v="${pair%%:*}" want="${pair#*:}"
  [[ -f "$OUT/$v.yaml" ]] || continue
  got="$(proxy_env "$v" DOCKER_PROXY_REAP_UNLABELED)"
  if [[ "$got" == "$want" ]]; then echo "ok $v: DOCKER_PROXY_REAP_UNLABELED = $want"; else echo "!! $v: DOCKER_PROXY_REAP_UNLABELED is $got, expected $want"; failed=1; fi
done
expect_env external-daemon-1-pod SANDBOX_CAPACITY_DIVISOR missing
# A whole number written as 4.0 reaches the app as an integer it can read.
expect_env external-daemon-max-total-float SANDBOX_MAX_TOTAL '"4"'
expect_env external-daemon-max-total-float SANDBOX_CAPACITY_DIVISOR missing
# A numeric 0 is passed on, like a quoted one; an empty value is left out.
expect_env dind-capacity-zeros SANDBOX_SLOT_WAIT_S '"0"'
expect_env dind-capacity-zeros SANDBOX_MAX_PER_ORG '"0"'
expect_env defaults-dind SANDBOX_SLOT_WAIT_S missing
expect_env external-daemon-autoscaled SANDBOX_CAPACITY_DIVISOR '"3"'

# Live limit: on the dind sidecar the app reads the sidecar's own cgroup,
# exported by a loop in the dind container and served by docker-proxy; never
# the node. Turned off, the dind container is exactly as without it.
for v in defaults-dind eks; do
  expect_env "$v" SANDBOX_DOCKER_TOPOLOGY '"dind-sidecar"'
  expect_env "$v" SANDBOX_LIVE_LIMIT missing
  expect_env "$v" SANDBOX_SIDECAR_MEM_HIGH missing
done
for v in cloud external-daemon-1-pod; do
  expect_env "$v" SANDBOX_DOCKER_TOPOLOGY missing
done
expect_env dind-live-off SANDBOX_DOCKER_TOPOLOGY '"dind-sidecar"'
expect_env dind-live-off SANDBOX_LIVE_LIMIT '"off"'
expect_env dind-live-tuned SANDBOX_SIDECAR_MEM_HIGH '"0.85"'
expect_env dind-live-tuned SANDBOX_SIDECAR_CPU_THROTTLE '"0.3"'
expect_env dind-live-tuned SANDBOX_SIDECAR_MEM_HARD missing
expect_env dind-live-tuned SANDBOX_MEMORY_OVERCOMMIT '"2"'

# Load alerts: each pod has its own cooldown, named after the pod.
for v in local-neo4j-kafka defaults-dind; do
  expect_env "$v" SANDBOX_ALERT_SCOPE '{"fieldRef": {"fieldPath": "metadata.name"}}'
done
check_live_limit() { # variant, "on" | "off"
  local doc="$OUT/$1.yaml"
  [[ -f "$doc" ]] || return 0
  if python3 - "$doc" "$2" <<'PY'; then echo "ok $1: live limit $2 in dind and docker-proxy"; else failed=1; fi
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") == "Deployment"]
pod = next(d for d in docs if d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["spec"]
containers = {c["name"]: c for c in pod["containers"]}
dind, proxy = containers["dind"], containers["docker-proxy"]
dind_env = [e["name"] for e in dind.get("env") or []]
proxy_env = {e["name"]: e.get("value") for e in proxy.get("env") or []}
errors = []
if (dind.get("args") or [None])[0] != "dockerd":
    errors.append(f"dind args must start with dockerd: {dind.get('args')}")
if sys.argv[2] == "on":
    command = dind.get("command") or []
    if command[:2] != ["/bin/sh", "-c"] or command[3:] != ["pipeshub-dind"] or "dockerd-entrypoint.sh" not in command[2]:
        errors.append(f"dind command is not the exporter wrapper: {command[:2]} ... {command[3:]}")
    if "PIPESHUB_EXPORT_INTERVAL" not in dind_env:
        errors.append("dind has no PIPESHUB_EXPORT_INTERVAL")
    if proxy_env.get("DOCKER_PROXY_SIDECAR_STATS_FILE") != "/var/run/dind/pipeshub-sidecar-stats":
        errors.append("docker-proxy has no DOCKER_PROXY_SIDECAR_STATS_FILE")
else:
    if "command" in dind:
        errors.append("dind has a command with the live limit off")
    if dind_env != ["DOCKER_TLS_CERTDIR", "DOCKER_HOST"]:
        errors.append(f"dind env is {dind_env} with the live limit off")
    if "DOCKER_PROXY_SIDECAR_STATS_FILE" in proxy_env:
        errors.append("docker-proxy reads the sidecar stats with the live limit off")
for e in errors:
    print(f"!! {sys.argv[1]}: {e}")
sys.exit(1 if errors else 0)
PY
}
check_live_limit defaults-dind on
check_live_limit eks on
check_live_limit dind-live-off off

# The exporter itself, run under sh against fixture cgroup v2 files.
if [[ -f "$OUT/defaults-dind.yaml" ]] && python3 - "$OUT/defaults-dind.yaml" "$OUT" <<'PY'; then echo "ok defaults-dind: exporter script writes the sidecar stats"; else failed=1; fi
import os, subprocess, sys, time, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d and d.get("kind") == "Deployment"]
pod = next(d for d in docs if d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["spec"]
command = next(c for c in pod["containers"] if c["name"] == "dind")["command"]
work = os.path.join(sys.argv[2], "exporter")
cg, bin_dir = os.path.join(work, "cg"), os.path.join(work, "bin")
os.makedirs(cg, exist_ok=True)
os.makedirs(bin_dir, exist_ok=True)
files = {
    "memory.max": "4294967296\n", "memory.current": "812000000\n", "cpu.max": "200000 100000\n",
    "memory.stat": "anon 500000000\nfile 300000000\nshmem 20000000\nkernel 1\n",
    "memory.events.local": "low 0\nhigh 0\nmax 2\noom 1\noom_kill 1\n",
    "cpu.stat": "usage_usec 123456\nuser_usec 1\nsystem_usec 2\nnr_periods 150\nnr_throttled 50\nthrottled_usec 9\n",
    "memory.pressure": "some avg10=1.00 avg60=0 avg300=0 total=1\nfull avg10=0.50 avg60=0 avg300=0 total=1\n",
    "cpu.pressure": "some avg10=12.00 avg60=0 avg300=0 total=1\nfull avg10=0.00 avg60=0 avg300=0 total=0\n",
}
for name, text in files.items():
    with open(os.path.join(cg, name), "w") as f:
        f.write(text)
stub = os.path.join(bin_dir, "dockerd-entrypoint.sh")
with open(stub, "w") as f:
    f.write("#!/bin/sh\nexit 0\n")
os.chmod(stub, 0o755)
# No dockerd or containerd process to read RSS from (as on a CI host): the counters are still published.
pidof = os.path.join(bin_dir, "pidof")
with open(pidof, "w") as f:
    f.write("#!/bin/sh\nexit 1\n")
os.chmod(pidof, 0o755)
out = os.path.join(work, "stats")
env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "PIPESHUB_CGROUP_DIR": cg,
       "PIPESHUB_SIDECAR_STATS": out, "PIPESHUB_EXPORT_ONCE": "1"}
proc = subprocess.run([*command, "dockerd", "--host=unix:///x"], env=env, timeout=20)
deadline = time.time() + 10
while not os.path.exists(out) and time.time() < deadline:
    time.sleep(0.05)
errors = [] if proc.returncode == 0 else [f"the wrapper exited {proc.returncode}"]
lines = set(open(out).read().splitlines()) if os.path.exists(out) else set()
for want in ("v 1", "seq 1", "memory.max 4294967296", "memory.current 812000000", "cpu.max 200000 100000",
             "memory.stat.file 300000000", "memory.stat.shmem 20000000", "memory.events.local.oom 1",
             "cpu.stat.usage_usec 123456", "cpu.stat.nr_throttled 50", "memory.pressure.full.avg10 0.50",
             "cpu.pressure.some.avg10 12.00"):
    if want not in lines:
        errors.append(f"missing {want!r} in {sorted(lines)}")
if os.path.exists(out + ".tmp"):
    errors.append("the temp file was left behind")
for e in errors:
    print(f"!! exporter: {e}")
sys.exit(1 if errors else 0)
PY

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
  "dind without a memory limit|the code sandbox cap is sized from the dind limits|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.resources.limits.memory=null"
  "dind without limits|the code sandbox cap is sized from the dind limits|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.resources.limits=null"
  "live limit memHigh above memHard|memHigh must be below memHard|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHigh=0.95 --set sandbox.dind.liveLimit.memHard=0.9"
  "live limit memHigh above the default memHard|memHigh must be below memHard|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHigh=0.96"
  "live limit memHard below the default memHigh|memHigh must be below memHard|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHard=0.9"
  "live limit memHard just under 0.01 above memHigh|memHigh must be below memHard|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHigh=0.70 --set sandbox.dind.liveLimit.memHard=0.70995"
  "fractional sandbox maxTotal|must be a whole number of sandboxes|${EXTERNAL[*]} --set replicaCount=2 --set autoscaling.enabled=false --set-string sandbox.capacity.maxTotal=4.5"
  "live limit memHard closer than 0.01 to memHigh|memHigh must be below memHard|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHigh=0.9 --set sandbox.dind.liveLimit.memHard=0.905"
  "live limit export interval out of range|exportIntervalSeconds must be 2..15|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.exportIntervalSeconds=1"
  "live limit export interval past the proxy's staleness|exportIntervalSeconds must be 2..15|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.exportIntervalSeconds=16"
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
  "postgres exporter without a server|postgresExporter.dataSource is required|${LOCAL[*]} --set postgresExporter.enabled=true --set postgresExporter.passwordSecret.name=pg-monitor"
  "postgres exporter name colliding with the app name|the postgres exporter name|${LOCAL[*]} --set postgresExporter.enabled=true --set postgresExporter.dataSource=pg.example:5432/pipeshub --set postgresExporter.passwordSecret.name=pg-monitor --set fullnameOverride=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-postgres-exporter"
  "postgres exporter selector colliding with the app selector|the postgres exporter pods' application name|${LOCAL[*]} --set postgresExporter.enabled=true --set postgresExporter.dataSource=pg.example:5432/pipeshub --set postgresExporter.passwordSecret.name=pg-monitor --set nameOverride=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-postgres-exporter"
  "postgres exporter without its password|postgresExporter.passwordSecret.name is required|${LOCAL[*]} --set postgresExporter.enabled=true --set postgresExporter.dataSource=pg.example:5432/pipeshub"
  "parsing name colliding with the app name|equals the main application's name|${LOCAL[*]} --set parsing.enabled=true --set nameOverride=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-parsing"
  "parsing object names colliding with the app names|equals the main application's name|${LOCAL[*]} --set parsing.enabled=true --set nameOverride=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-parsing"
  "parsing object names colliding, shorter override|equals the main application's name|${LOCAL[*]} --set parsing.enabled=true --set nameOverride=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-parsing"
  "external daemon shared by more pods than the capacity split allows|splits its capacity at most 64 ways|${EXTERNAL[*]} --set replicaCount=1 --set autoscaling.enabled=true --set autoscaling.maxReplicas=65"
  "run-time cap set through extraEnv|Set config.sandboxMaxRunTimeoutS instead|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set extraEnv[0].name=SANDBOX_MAX_RUN_TIMEOUT_S --set-string extraEnv[0].value=900"
  "local sandbox with a string false flag|refuses it unless config.sandboxAllowLocal=true|${LOCAL[*]} --set-string config.sandboxAllowLocal=false"
  "negative neo4j memory limit|neo4j.resources.limits.memory \"-8Gi\" is not a memory quantity|${LOCAL[*]} --set-string neo4j.resources.limits.memory=-8Gi"
  "neo4j memory limit with two suffixes|neo4j.resources.limits.memory \"8e3Gi\" is not a memory quantity|${LOCAL[*]} --set-string neo4j.resources.limits.memory=8e3Gi"
  "neo4j memory limit that is not a quantity|neo4j.resources.limits.memory \"lots\" is not a memory quantity|${LOCAL[*]} --set-string neo4j.resources.limits.memory=lots"
  "neo4j starting heap in a unit neo4j refuses|NEO4J_server_memory_heap_initial__size \"5kib\" is not a size|${LOCAL[*]} --set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=5kib"
  "neo4j starting heap with a lowercase exponent|NEO4J_server_memory_heap_initial__size \"3e9\" is not a size|${LOCAL[*]} --set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=3e9"
  "neo4j starting heap with a plus-signed exponent|NEO4J_server_memory_heap_initial__size \"3E+9\" is not a size|${LOCAL[*]} --set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=3E+9"
  "neo4j starting heap above Long.MAX_VALUE bytes|NEO4J_server_memory_heap_initial__size \"1E19\" is not a size|${LOCAL[*]} --set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=1E19"
  "neo4j memory limit exponent past the bound|neo4j.resources.limits.memory \"8e31\" is not a memory quantity|${LOCAL[*]} --set-string neo4j.resources.limits.memory=8e31"
  "neo4j memory limit past the bound through its significand|neo4j.resources.limits.memory \"80000000000e21\" is not a memory quantity|${LOCAL[*]} --set-string neo4j.resources.limits.memory=80000000000e21"
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
  "live limit export interval at its maximum|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.exportIntervalSeconds=15"
  "live limit export interval at its minimum|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.exportIntervalSeconds=2"
  "live limit memHigh exactly 0.01 below memHard|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHigh=0.93 --set sandbox.dind.liveLimit.memHard=0.94"
  "live limit memHigh alone, 0.01 below the default memHard|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHigh=0.94"
  "live limit memHard alone, above the default memHigh|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.memHard=0.98"
  "live limit off ignores its tuning|--set sandbox.dind.enabled=true --set persistence.accessModes={ReadWriteMany} --set sandbox.dind.liveLimit.enabled=false --set sandbox.dind.liveLimit.exportIntervalSeconds=1 --set sandbox.dind.liveLimit.memHigh=0.99"
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

# Neo4j sizes derived from its memory limit: name | expected initial,max,pagecache | helm arguments.
# An unquoted integer in a values file reaches the template as a float ("8.589934592e+09").
printf 'neo4j:\n  resources:\n    limits:\n      memory: 8589934592\n' >"$OUT/neo4j-int-limit.yaml"
NEO4J_MEMORY=(
  "8Gi|1024m,4096m,2048m|--set-string neo4j.resources.limits.memory=8Gi"
  "8Gi in bytes, from --set|1024m,4096m,2048m|--set neo4j.resources.limits.memory=8589934592"
  "8Gi in bytes, from a values file|1024m,4096m,2048m|-f $OUT/neo4j-int-limit.yaml"
  "unsigned exponent|953m,3814m,1907m|--set-string neo4j.resources.limits.memory=8e9"
  "signed exponent|953m,3814m,1907m|--set-string neo4j.resources.limits.memory=8e+9"
  "zero-padded signed exponent|953m,3814m,1907m|--set-string neo4j.resources.limits.memory=8e+009"
  "8Gi as a long significand and a negative exponent|1024m,4096m,2048m|--set-string neo4j.resources.limits.memory=85899345920000000000000000000000000000000e-31"
  "8Gi written with leading zeros and a fraction|1024m,4096m,2048m|--set-string neo4j.resources.limits.memory=0000.0000000000085899345920e+21"
  "starting heap with neo4j's uppercase exponent|3E9,4096m,2048m|--set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=3E9"
  "starting heap with an exponent and a unit|5E3m,5000m,2048m|--set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=5E3m"
  "starting heap with a negative exponent|5242880000E-3k,5120m,2048m|--set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=5242880000E-3k"
  "negative exponent|1024m,4096m,2048m|--set-string neo4j.resources.limits.memory=8589934592000e-3"
  "leading dot|64m,256m,128m|--set-string neo4j.resources.limits.memory=.5Gi"
  "plus sign|1024m,4096m,2048m|--set-string neo4j.resources.limits.memory=+8Gi"
  "starting heap in bytes with neo4j's B suffix|2147483648B,4096m,2048m|--set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=2147483648B"
  "starting heap above half the limit|5242880k,5120m,2048m|--set-string neo4j.resources.limits.memory=8Gi --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=5242880k"
  "unreadable limit with every size given|1g,2g,1g|--set-string neo4j.resources.limits.memory=lots --set-string neo4j.env.NEO4J_server_memory_heap_initial__size=1g --set-string neo4j.env.NEO4J_server_memory_heap_max__size=2g --set-string neo4j.env.NEO4J_server_memory_pagecache_size=1g"
)
for entry in "${NEO4J_MEMORY[@]}"; do
  IFS='|' read -r name want rest <<<"$entry"
  read -r -a args <<<"$rest"
  if ! helm template ci . "${SECRETS[@]}" "${LOCAL[@]}" "${args[@]}" --show-only templates/neo4j-statefulset.yaml >"$OUT/neo4j-mem.yaml" 2>"$OUT/neo4j-mem.err"; then
    echo "!! neo4j memory, ${name}: refused:"; cat "$OUT/neo4j-mem.err"; failed=1; continue
  fi
  got=$(for s in heap_initial__size heap_max__size pagecache_size; do
    grep -A1 -E "name: NEO4J_(server|dbms)_memory_${s}\$" "$OUT/neo4j-mem.yaml" | sed -n 's/.*value: "\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' | head -1
  done | paste -sd, -)
  if [[ "$got" == "$want" ]]; then
    echo "ok neo4j memory, ${name}: ${got}"
  else
    echo "!! neo4j memory, ${name}: got '${got}', want '${want}'"; failed=1
  fi
done

# The no-daemon error used to suggest mounting the node's socket.
if helm template ci . "${SECRETS[@]}" --set 'persistence.accessModes={ReadWriteMany}' >/dev/null 2>"$OUT/refused.err" \
   || grep -qF 'docker.sock' "$OUT/refused.err"; then
  echo "!! docker sandbox without a daemon: error must not suggest mounting docker.sock"; failed=1
else
  echo "ok refused: docker sandbox without a daemon suggests no socket mount"
fi

# NOTES.txt, rendered without a cluster: a copy of the chart serves it
# through tpl from a ConfigMap.
NOTES_CHART="$OUT/notes-chart"
cp -r . "$NOTES_CHART"
mv "$NOTES_CHART/templates/NOTES.txt" "$NOTES_CHART/notes.tpl"
cat > "$NOTES_CHART/templates/zz-notes.yaml" <<'YAML'
apiVersion: v1
kind: ConfigMap
metadata:
  name: notes
data:
  notes: {{ tpl (.Files.Get "notes.tpl") . | quote }}
YAML
notes() { # args... -> the rendered NOTES on stdout
  helm template ci "$NOTES_CHART" "${SECRETS[@]}" --set 'persistence.accessModes={ReadWriteMany}' "$@" \
    --show-only templates/zz-notes.yaml | python3 -c 'import sys, yaml; print(yaml.safe_load(sys.stdin)["data"]["notes"])'
}
check_notes() { # name, "has"|"lacks", text, args...
  local name="$1" mode="$2" text="$3" out; shift 3
  if ! out="$(notes "$@" 2>&1)"; then echo "!! notes $name: render failed: $out"; failed=1; return; fi
  if grep -qF -- "$text" <<<"$out"; then found=has; else found=lacks; fi
  if [[ "$found" == "$mode" ]]; then echo "ok notes $name: $mode '$text'"; else echo "!! notes $name: expected it $mode '$text'"; failed=1; fi
}
DIND=(--set sandbox.dind.enabled=true)
EVICT="requests differ from its limits"
check_notes "dind defaults" lacks "$EVICT" "${DIND[@]}"
check_notes "dind requests 2000m / limits 2" lacks "$EVICT" "${DIND[@]}" --set-string sandbox.dind.resources.requests.cpu=2000m
check_notes "dind requests 4096Mi / limits 4Gi" lacks "$EVICT" "${DIND[@]}" --set-string sandbox.dind.resources.requests.memory=4096Mi
check_notes "dind requests 1.5 / limits 1500m" lacks "$EVICT" "${DIND[@]}" --set-string sandbox.dind.resources.requests.cpu=1.5 --set-string sandbox.dind.resources.limits.cpu=1500m
check_notes "dind requests 4G / limits 4Gi" has "$EVICT" "${DIND[@]}" --set-string sandbox.dind.resources.requests.memory=4G
check_notes "dind requests 250m" has "$EVICT" "${DIND[@]}" --set-string sandbox.dind.resources.requests.cpu=250m
check_notes "dind no requests" has "$EVICT" "${DIND[@]}" --set sandbox.dind.resources.requests=null
check_notes "dind sized" has "the smallest of" "${DIND[@]}"
check_notes "dind maxTotal 5" has "Each pod runs at most 5 code sandboxes" "${DIND[@]}" --set sandbox.capacity.maxTotal=5
check_notes "dind maxTotal 0" has "the smallest of" "${DIND[@]}" --set sandbox.capacity.maxTotal=0
check_notes "external 2 pods" has "each pod uses 1/2 of its capacity" "${EXTERNAL[@]}" --set replicaCount=2 --set autoscaling.enabled=false
check_notes "external 2 pods maxTotal 0" has "each pod uses 1/2 of its capacity" "${EXTERNAL[@]}" --set replicaCount=2 --set autoscaling.enabled=false --set-string sandbox.capacity.maxTotal=0
check_notes "external 2 pods maxTotal 4" lacks "each pod uses 1/2" "${EXTERNAL[@]}" --set replicaCount=2 --set autoscaling.enabled=false --set sandbox.capacity.maxTotal=4
check_notes "external run cap" has "SANDBOX_MAX_RUN_TIMEOUT_S=900" "${EXTERNAL[@]}" --set config.sandboxMaxRunTimeoutS=900

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

# The EKS preset's most app pods fit the app node group as created: each pod's
# summed CPU requests against an m7i.2xlarge's CPU left after kubelet reserve and
# daemonsets (about 7.5 of 8).
if [[ -f "$OUT/eks.yaml" ]] && python3 - "$OUT/eks.yaml" "$AWS_DIR/cluster.yaml" <<'PY'; then echo "ok eks: max app pods fit the app node group"; else failed=1; fi
import sys, yaml
def cpu(v):
    v = str(v)
    return float(v[:-1]) / 1000 if v.endswith("m") else float(v)
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
pod = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "ci-pipeshub-ai")["spec"]["template"]["spec"]
per_pod = sum(cpu((c.get("resources") or {}).get("requests", {}).get("cpu", 0)) for c in pod["containers"])
hpa = next(d for d in docs if d["kind"] == "HorizontalPodAutoscaler" and d["metadata"]["name"] == "ci-pipeshub-ai")
app = next(g for g in yaml.safe_load(open(sys.argv[2]))["managedNodeGroups"] if g["name"] == "app")
assert app["instanceType"] == "m7i.2xlarge", app["instanceType"]
fit = int(7.5 // per_pod) * app["desiredCapacity"]
if hpa["spec"]["maxReplicas"] > fit:
    print(f"!! eks: maxReplicas {hpa['spec']['maxReplicas']} but {app['desiredCapacity']} app nodes hold {fit} pods of {per_pod:g} CPU")
    sys.exit(1)
PY

if [[ "$failed" -ne 0 ]]; then
  echo "check_chart: FAILED" >&2
  exit 1
fi
echo "check_chart: all variants valid"

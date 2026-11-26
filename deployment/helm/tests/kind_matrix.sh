#!/usr/bin/env bash
# ==============================================================================
# The options-matrix lane's Helm stack profiles (docs/test-plan/04 §4.1: ST-2, ST-6, ST-7) on a throwaway kind
# cluster, left running for integration-tests/harness/e2e (QA-6), which attaches to it as it does to a Compose
# stack of deployment/e2e/run.sh matrix-up.
#
#   E2E_STACK=ST-6 E2E_RUN=<id> E2E_APP_IMAGE=<image built from this tree> bash deployment/helm/tests/kind_matrix.sh up
#   bash deployment/helm/tests/kind_matrix.sh forward <id>   # re-point the run's local ports after the app pod rolled
#   bash deployment/helm/tests/kind_matrix.sh down <id>      # delete the cluster and the run's files
#
# The chart is installed as kind_smoke.sh installs it (values-local.yaml, generated secrets), with the profile's
# stores, the scripted model and SaaS of deployment/e2e as two Deployments of the app image (it carries
# harness_core), every provider wire pointed at the scripted model, and deployment/e2e mounted at /e2e (the
# runner's journal reader). It writes $E2E_STATE_DIR/<id>/stack.env and seed.json in the shape the runner reads.
#
# Needs: kind, kubectl, helm, docker, python3, openssl; E2E_APP_IMAGE (the app image of this tree: the lane tests
# this tree, never a published release), E2E_SANDBOX_IMAGE, and the engine images the rows use, all local.
# ==============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
CHART="$ROOT/deployment/helm/pipeshub-ai"
E2E="$ROOT/deployment/e2e"
STATE_DIR="${E2E_STATE_DIR:-$HOME/.cache/pxh-e2e}"
NAMESPACE="pxh-e2e"
RELEASE="pipeshub-ai"

say() { printf 'kind_matrix: %s\n' "$*"; }
die() { printf 'kind_matrix: %s\n' "$*" >&2; exit 1; }
rand() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

run_vars() {
  RUN="$1"
  WORK="$STATE_DIR/$RUN"
  CLUSTER="pxh-e2e-$RUN"
  CONTEXT="kind-$CLUSTER"
  KUBECTL=(kubectl --context "$CONTEXT" -n "$NAMESPACE")
}

envv() { grep -E "^$1=" "$WORK/stack.env" | head -1 | cut -d= -f2-; }

# The profile's stores as chart values (04 §4.1; scripts/e2e_covering_array.py STACKS).
profile_values() {
  case "${E2E_STACK:?set E2E_STACK}" in
    ST-2) PROFILE=(--set messageBroker.type=redis --set config.kvStoreType=redis); MONGO_MODE=replset ;;
    ST-6) PROFILE=(--set neo4j.enabled=false --set arango.enabled=true --set "arango.auth.rootPassword=$(rand 12)"
                   --set etcd.enabled=true --set config.kvStoreType=etcd
                   --set messageBroker.type=kafka --set kafka.enabled=true); MONGO_MODE=replset ;;
    ST-7) PROFILE=(--set neo4j.enabled=false --set arango.enabled=true --set "arango.auth.rootPassword=$(rand 12)"
                   --set mongodb.architecture=standalone
                   --set messageBroker.type=kafka --set kafka.enabled=true); MONGO_MODE=standalone ;;
    *) die "E2E_STACK=${E2E_STACK} is not a Helm stack profile (ST-2, ST-6, ST-7)" ;;
  esac
}

# The scripted model and SaaS as Deployments of the app image, reached in the cluster by the names the seed uses.
fakes_manifest() {
  local name port script
  for spec in "pxh-fake-llm 8080 fake_llm/server.py" "pxh-fake-saas 8090 fake_saas/server.py"; do
    read -r name port script <<<"$spec"
    cat <<EOF
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: $name, labels: {app: $name, pipeshub.harness.owner: x-lead}}
spec:
  replicas: 1
  selector: {matchLabels: {app: $name}}
  template:
    metadata: {labels: {app: $name}}
    spec:
      containers:
        - name: $name
          image: $E2E_APP_IMAGE
          imagePullPolicy: Never
          command: ["python", "/e2e/$script", "$port"]
          env:
            - {name: PYTHONPATH, value: /app/python}
            - {name: FAKE_LLM_API_KEY, value: "$LLM_KEY"}
            - {name: FAKE_LLM_CONTROL_TOKEN, value: "$CONTROL"}
            - {name: FAKE_SAAS_CONTROL_TOKEN, value: "$CONTROL"}
            - {name: FAKE_SAAS_JIRA_TOKEN, value: "$JIRA_TOKEN"}
            - {name: FAKE_LLM_MODELS, value: "e2e-llm,e2e-embedding,gpt-5-mini,claude-sonnet-4-5,gemini-2.5-flash,anthropic.claude-3-5-sonnet-20240620-v1:0"}
          ports: [{containerPort: $port}]
          readinessProbe: {httpGet: {path: /healthz, port: $port}, periodSeconds: 3}
          volumeMounts: [{name: e2e, mountPath: /e2e, readOnly: true}]
      volumes: [{name: e2e, configMap: {name: pxh-e2e, items: $(configmap_items)}}]
---
apiVersion: v1
kind: Service
metadata: {name: $name}
spec: {selector: {app: $name}, ports: [{port: $port, targetPort: $port}]}
EOF
  done
}

E2E_FILES=(journal_read.js fake_llm/server.py fake_llm/wires.py fake_saas/server.py)

configmap_items() {
  local file out="["
  for file in "${E2E_FILES[@]}"; do out+="{key: ${file//\//__}, path: $file},"; done
  echo "${out%,}]"
}

forward() {
  run_vars "$1"
  [[ -f "$WORK/stack.env" ]] || die "no run $RUN"
  if [[ -f "$WORK/forward.pids" ]]; then xargs -r kill <"$WORK/forward.pids" 2>/dev/null || true; fi
  : >"$WORK/forward.pids"
  local target port
  for target in "svc/$RELEASE:$(envv E2E_APP_PORT):3001" "svc/pxh-fake-llm:$(envv E2E_LLM_PORT):8080" \
                "svc/pxh-fake-saas:$(envv E2E_SAAS_PORT):8090"; do
    port="${target#*:}"
    "${KUBECTL[@]}" port-forward "${target%%:*}" "$port" >>"$WORK/forward.log" 2>&1 &
    echo $! >>"$WORK/forward.pids"
  done
  for port in "$(envv E2E_APP_PORT)" "$(envv E2E_LLM_PORT)" "$(envv E2E_SAAS_PORT)"; do
    for _ in $(seq 1 60); do
      python3 -c 'import socket,sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), 1)' "$port" 2>/dev/null && break
      sleep 1
    done
  done
}

up() {
  for tool in kind kubectl helm docker python3 openssl; do command -v "$tool" >/dev/null || die "$tool is required"; done
  : "${E2E_APP_IMAGE:?set E2E_APP_IMAGE to the app image built from this tree}"
  local sandbox_image="${E2E_SANDBOX_IMAGE:-pxh-sandbox:x1}" image
  for image in "$E2E_APP_IMAGE" "$sandbox_image"; do
    docker image inspect "$image" >/dev/null 2>&1 || die "image $image is not on this host"
  done
  run_vars "${E2E_RUN:?set E2E_RUN}"
  mkdir -p "$WORK"
  profile_values
  LLM_KEY="sk-e2e-$(rand 16)"; CONTROL="$(rand 16)"; JIRA_TOKEN="pat-canary-$(rand 12)"
  local mongo_password admin_email admin_password
  mongo_password="$(rand 12)"; admin_email="e2e-admin-$RUN@e2etest.pipeshub.local"; admin_password="E2e-$(rand 10)!1a"

  say "cluster $CLUSTER for $E2E_STACK"
  kind create cluster --name "$CLUSTER" --wait 180s >"$WORK/kind.log" 2>&1 || { tail -20 "$WORK/kind.log"; die "kind create failed"; }
  for image in "$E2E_APP_IMAGE" "$sandbox_image" pxh-opencode-tools:1.18.34-ph7 pxh-codex-tools:0.160.0-ph7; do
    docker image inspect "$image" >/dev/null 2>&1 && kind load docker-image --name "$CLUSTER" "$image" >>"$WORK/kind.log" 2>&1
  done
  kubectl --context "$CONTEXT" create namespace "$NAMESPACE" >/dev/null
  local args=() file
  for file in "${E2E_FILES[@]}"; do args+=(--from-file="${file//\//__}=$E2E/$file"); done
  "${KUBECTL[@]}" create configmap pxh-e2e "${args[@]}" >/dev/null
  fakes_manifest | "${KUBECTL[@]}" apply -f - >/dev/null

  helm dependency build "$CHART" >/dev/null
  say "installing the chart (waits for every pod to be Ready)"
  helm install "$RELEASE" "$CHART" --kube-context "$CONTEXT" --namespace "$NAMESPACE" \
    -f "$CHART/values-local.yaml" \
    --set hardened=true \
    --set image.repository="${E2E_APP_IMAGE%:*}" --set image.tag="${E2E_APP_IMAGE##*:}" --set image.pullPolicy=Never \
    --set config.sandboxDockerImage="$sandbox_image" \
    --set secretKey="$(rand 32)" \
    --set mongodb.auth.rootPassword="$mongo_password" \
    --set 'mongodb.auth.usernames[0]=pipeshub' --set "mongodb.auth.passwords[0]=$(rand 12)" \
    --set 'mongodb.auth.databases[0]=pipeshub' \
    --set redis.auth.password="$(rand 12)" --set neo4j.auth.password="$(rand 12)" \
    --set neo4j.resources.requests.cpu=250m --set qdrant.resources.requests.cpu=250m \
    --set arango.resources.requests.cpu=250m \
    --set 'extraEnv[0].name=ANTHROPIC_BASE_URL' --set 'extraEnv[0].value=http://pxh-fake-llm:8080' \
    --set 'extraEnv[1].name=GOOGLE_GEMINI_BASE_URL' --set 'extraEnv[1].value=http://pxh-fake-llm:8080' \
    --set 'extraEnv[2].name=OPENAI_BASE_URL' --set 'extraEnv[2].value=http://pxh-fake-llm:8080/v1' \
    --set 'extraEnv[3].name=AWS_ENDPOINT_URL_BEDROCK_RUNTIME' --set 'extraEnv[3].value=http://pxh-fake-llm:8080' \
    --set 'extraVolumes[0].name=pxh-e2e' --set 'extraVolumes[0].configMap.name=pxh-e2e' \
    --set "extraVolumes[0].configMap.items[0].key=journal_read.js" --set "extraVolumes[0].configMap.items[0].path=journal_read.js" \
    --set 'extraVolumeMounts[0].name=pxh-e2e' --set 'extraVolumeMounts[0].mountPath=/e2e' \
    --set 'extraVolumeMounts[0].readOnly=true' \
    "${PROFILE[@]}" --wait --timeout 40m >"$WORK/helm.log" 2>&1 || { tail -30 "$WORK/helm.log"; die "helm install did not become Ready"; }
  "${KUBECTL[@]}" rollout status deploy/pxh-fake-llm deploy/pxh-fake-saas --timeout 5m >/dev/null

  cat >"$WORK/stack.env" <<EOF
E2E_DEPLOY=helm
E2E_STACK=$E2E_STACK
E2E_KUBE_CONTEXT=$CONTEXT
E2E_NAMESPACE=$NAMESPACE
E2E_APP_DEPLOYMENT=$RELEASE
E2E_APP_PORT=$(free_port)
E2E_LLM_PORT=$(free_port)
E2E_SAAS_PORT=$(free_port)
E2E_LLM_KEY=$LLM_KEY
E2E_LLM_CONTROL=$CONTROL
E2E_JIRA_TOKEN=$JIRA_TOKEN
E2E_ADMIN_EMAIL=$admin_email
E2E_ADMIN_PASSWORD=$admin_password
E2E_MONGO=$MONGO_MODE
E2E_MONGO_POD=$RELEASE-mongodb-0
E2E_MONGO_USER=root
E2E_MONGO_PASSWORD=$mongo_password
MONGO_DB_NAME=es
EOF
  chmod 600 "$WORK/stack.env"
  forward "$RUN"
  say "seeding"
  for step in seed durable harness; do
    E2E_API="http://127.0.0.1:$(envv E2E_APP_PORT)" E2E_ADMIN_EMAIL="$admin_email" E2E_ADMIN_PASSWORD="$admin_password" \
    E2E_LLM_KEY="$LLM_KEY" E2E_STATE="$WORK/seed.json" python3 "$E2E/seed.py" "$step" >>"$WORK/seed.log" 2>&1 \
      || { tail -20 "$WORK/seed.log"; die "seed.py $step failed"; }
  done
  say "left running: run=$RUN stack=$E2E_STACK (bash $0 down $RUN)"
}

down() {
  run_vars "$1"
  if [[ -f "$WORK/forward.pids" ]]; then xargs -r kill <"$WORK/forward.pids" 2>/dev/null || true; fi
  kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  rm -rf "$WORK"
  say "deleted $CLUSTER"
}

case "${1:-}" in
  up) up ;;
  forward) forward "${2:?run id}" ;;
  down) down "${2:?run id}" ;;
  *) die "usage: kind_matrix.sh up | forward RUN | down RUN" ;;
esac

#!/usr/bin/env bash
# ==============================================================================
# The harness e2e lane (X5, E2E2; in CI since QA-5: .github/workflows/harness-e2e.yml): the Compose stack end
# to end in a browser.
#
#   run.sh harness (default; Es-PR, stack ST-1): the durable worker for chat-loop runs, the scripted model and
#   SaaS, and the `harness` Playwright project (the must-runs EM-01/02/03/13 on the real stack, the mocked run
#   UI, cards, AI accounts, admin approvals, schedules, engine agents). run.sh durable (E2E2, nightly): a durable
#   engine run on the real path, browser → Next.js → Node → worker → sandbox broker → opencode (runc) → egress
#   proxy → model gateway / tool gateway → the card in the chat, with its egress checked (`harness-engine`).
#
# Brings the Compose stack up from this tree (deployment/docker-compose/
# docker-compose.yml + docker-compose.e2e.yml; no image is built), seeds an
# org, an admin, the scripted model and the agents, runs the Playwright projects in
# Playwright's own image with no retries, and tears everything down. Every container it starts carries
# pipeshub.harness.owner=x-lead and io.pipeshub.e2e.run=<run>; it finds and
# removes only those, and the sandboxes of the org it created. A skipped spec fails the lane.
#
#   bash deployment/e2e/run.sh            the harness lane, tear down
#   bash deployment/e2e/run.sh durable    the engine lane, then erase_world.py (QAFINAL-FU-ERASE: erased content never
#                                         comes back in search or an answer), tear down; `erase-world RUN` runs the
#                                         last against a stack left up
#   bash deployment/e2e/run.sh durable-up the engine lane's stack + seed, left running (durable-specs RUN, pw RUN …)
#   E2E_ENGINE=<kind> bash deployment/e2e/run.sh durable|durable-up
#                                         DP7-MVP-1: the engine agent and a new default chat run on that release engine
#                                         (an allowlist.json kind: opencode, the default; claude_code, codex, acp-…);
#                                         its run image must be on the host (engines.py image KIND names it)
#   bash deployment/e2e/run.sh browser    WP-71: the harness and authenticated projects, tear down (E2E_PW_WORKERS, default 2)
#   bash deployment/e2e/run.sh browser-specs RUN  the same two projects against a stack left up
#   bash deployment/e2e/run.sh up         bring the harness stack up + seed, leave it running
#   bash deployment/e2e/run.sh off|off-up R50-OFF: the harness switched off (E2E_HARNESS=off), harness_off_check.py, then
#                                         (off) the authenticated project and tear down; off-up leaves it running
#   bash deployment/e2e/run.sh specs RUN [ARGS]  the harness project against a stack left up
#   bash deployment/e2e/run.sh pw RUN ARGS  any Playwright command line against it; E2E_UI_CASE_SHOTS=/work/pw/<dir>
#                                         saves a UI case spec's pictures under the run's pw/<dir>; PXH_QA_RESULTS and
#                                         PXH_UX_BASELINES (QA-AUTO, scripts/qa_full.py) name folders under /work/pw for
#                                         the latency samples and UX gallery, and the UX baselines
#   bash deployment/e2e/run.sh down RUN   tear a run down
#   bash deployment/e2e/run.sh clean      remove dead runs' leftovers, kept logs and the UI cache
#   E2E_STACK=ST-3 bash deployment/e2e/run.sh matrix-up
#                                         QA-6: one stack profile of the options matrix (04 §4.1) up + seeded, left
#                                         running for integration-tests/harness/e2e (Compose profiles ST-1, ST-3,
#                                         ST-4, ST-5; the Helm ones are deployment/helm/tests/kind_matrix.sh)
#   E2E_MCP_HUB=1 E2E_MCP_RUNNER_IMAGE=<image> ... matrix-up|human-up (human-up has it on by default, R36-COVER)
#                                         MCP-E2E: the same stack with MCP_HUB, MCP_STDIO_RUNNERS and
#                                         MCP_ALLOW_CUSTOM_STDIO on and the scripted MCP servers
#                                         (docker-compose.mcp.yml); the image holds the STDIO servers'
#                                         programs and is not built here
#   E2E_DOC_STORE=postgres E2E_PG_MIGRATE_SITE=<site-packages> bash deployment/e2e/run.sh <any command that brings a
#                                         stack up>
#                                         WP114-FU-E2E: Node's document store, every harness and control-plane store
#                                         included, on PostgreSQL 17 behind PgBouncer in transaction mode with the jobs
#                                         login (docker-compose.pg.yml), migrated by the migrate job before the app
#                                         starts; E2E_PG_MIGRATE_SITE holds backend/db/requirements.lock's packages
#   bash deployment/e2e/run.sh pg          WP114-FU-E2E: the durable and harness projects on a stack with
#                                         E2E_DOC_STORE=postgres and engine runs on, then pg_world.py (the rows in
#                                         PostgreSQL, erasure, the org's deletion), torn down; `pg-world RUN` runs
#                                         the last against a stack left up
#   E2E_SANDBOX_RUNTIME=auto|runsc|runc bash deployment/e2e/run.sh <any command that brings a stack up>
#                                         R31-GVISO: engine sandboxes on the strongest runtime the daemon offers under
#                                         the dev override (auto, the default), on gVisor at the production floor with
#                                         no override (runsc: withoutGvisor absent), or on hardened runc with runsc
#                                         withheld from the broker (runc). R32-TRUST: runsc also signs the ACP
#                                         agent profiles with the stack's own test root at build (and again at
#                                         rebuild) and the services verify them, as a release install does; auto and
#                                         runc read them unsigned (the dev setting) unless E2E_ACP_PROFILES=signed.
#                                         `rebuild` takes a new E2E_SANDBOX_RUNTIME or E2E_ACP_PROFILES from the caller
#   bash deployment/e2e/run.sh airgap      C15.04: the same projects on a stack with E2E_AIRGAP=1, torn down
#   E2E_AIRGAP=1 bash deployment/e2e/run.sh <any command that brings a stack up>
#                                         C15.04: the stack on Docker networks with no route out
#                                         (docker-compose.airgap.yml): published ports through pxh-ingress only,
#                                         checked by airgap_check.py once the stack is up
#   bash deployment/e2e/run.sh dev-up      G-50: a laptop's stack (the `dev-harness` stack profile,
#                                         docker-compose.dev-harness.yml): its own MongoDB and Redis, the scripted
#                                         model, seeded, one scripted turn checked (dev_smoke.py), left running;
#                                         pulls the public images it lacks
#   bash deployment/e2e/run.sh human-up    a stack a person tests by hand in the browser (scripts/human-test-stack.sh
#                                         drives it): engine runs on, the local embedding service and indexing,
#                                         the model providers' real endpoints (docker-compose.human.yml), left running
#   bash deployment/e2e/run.sh seckey RUN  R31-SECKEY (EM-56): on a human-up stack left up, canaries planted for every kind
#                                         of credential, a job per release engine read from inside its sandbox (seckey.py;
#                                         E2E_SECKEY_ENGINES=kind,... narrows it), the canaries taken out after
#   bash deployment/e2e/run.sh rebuild RUN rebuild Node and the UI of a stack left up from its tree as it is now, and
#                                         restart its services on that build, keeping its data
#
# Needs: Docker with Compose v2, python:3.12-slim (and apt access once, to build pxh-e2e-app from
# Dockerfile.app), neo4j:5.26.0,
# qdrant/qdrant:v1.15.5, the sandbox image (E2E_SANDBOX_IMAGE, default pxh-sandbox:x1),
# the run image of E2E_ENGINE (lanes with engine runs only; pxh-opencode-tools:… by default), the Playwright image (E2E_PLAYWRIGHT_IMAGE, default
# mcr.microsoft.com/playwright:v<frontend's @playwright/test>-jammy; all local, nothing is pulled), a MongoDB
# replica set and a Redis container (E2E_MONGO_CONTAINER, E2E_REDIS_CONTAINER; default the shared
# pxh-mongo-rs and pxh-redis), Node 22 (E2E_NODE_HOME), a Python
# 3.12 venv with backend/python's dependencies (E2E_SITE_PACKAGES), and
# node_modules in backend/nodejs/apps and frontend.
#
# E2E_FAULT=<service> breaks exactly that service (the "did not come up"
# message is a test case, ST-2): node, connectors, query, model-gateway,
# neo4j, qdrant, sandbox-broker, egress-proxy, pxh-fake-llm, ui.
# ==============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
BASE_COMPOSE="$ROOT/deployment/docker-compose/docker-compose.yml"
OVERLAY="$HERE/docker-compose.e2e.yml"
SANDBOX_LEGACY="$ROOT/deployment/docker-compose/docker-compose.sandbox-legacy.yml"
MATRIX_OVERLAY="$HERE/docker-compose.matrix.yml"
MCP_OVERLAY="$HERE/docker-compose.mcp.yml"
HUMAN_OVERLAY="$HERE/docker-compose.human.yml"
# G-50 (run.sh dev-up): the stack's own MongoDB replica set and Redis, for a laptop with no shared stores.
DEV_OVERLAY="$HERE/docker-compose.dev-harness.yml"
# The sign-in add-on (people's own Claude accounts in Claude Code): the egress proxy's CONNECT tunnels, read by the
# proxy and the harness worker. Off unless the stack's env file says E2E_ENGINE_SIGNIN=1 (human-test-stack.sh
# --engine-signin).
TUNNELS_OVERLAY="$ROOT/deployment/docker-compose/docker-compose.egress-tunnels.yml"
# WP114-FU-E2E: the document store on PostgreSQL (E2E_DOC_STORE=postgres): the store, PgBouncer and the migrate job.
PG_OVERLAY="$HERE/docker-compose.pg.yml"
# C15.04: no route out (E2E_AIRGAP=1).
AIRGAP_OVERLAY="$HERE/docker-compose.airgap.yml"
# R32-REPO: a public git host answering as github.com on the stack's network (E2E_FAKE_GIT=1; human-up's default).
FAKEGIT_OVERLAY="$HERE/docker-compose.fakegit.yml"
PG_IMAGE="pgvector/pgvector:pg17"
PGBOUNCER_DOCKERFILE="$HERE/Dockerfile.pgbouncer"
export E2E_PGBOUNCER_IMAGE="${E2E_PGBOUNCER_IMAGE:-pxh-e2e-pgbouncer:$(sha256sum "$PGBOUNCER_DOCKERFILE" | cut -c1-12)}"
HUMAN_ENGINE_KINDS=(opencode codex claude_code)
STATE_DIR="${E2E_STATE_DIR:-$HOME/.cache/pxh-e2e}"
OWNER_LABEL="pipeshub.harness.owner=x-lead"
# Where the services read the stack's test trust root (PIPESHUB_TEST_TRUST_ROOT, docker-compose.e2e.yml).
TEST_TRUST_MOUNT=/run/pipeshub/test-trust
RUN_LABEL_KEY="io.pipeshub.e2e.run"
BROKER_MANAGED="io.pipeshub.sandbox-broker.managed=1"
BROKER_DATA_VOLUME="pipeshub_sandbox_data"
# The image's browsers are built for one Playwright release: it must be the one frontend/ installs.
PW_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["packages"]["node_modules/@playwright/test"]["version"])' "$ROOT/frontend/package-lock.json")"
PW_IMAGE="${E2E_PLAYWRIGHT_IMAGE:-mcr.microsoft.com/playwright:v${PW_VERSION}-jammy}"
SANDBOX_IMAGE="${E2E_SANDBOX_IMAGE:-pxh-sandbox:x1}"
# The app runs on python:3.12-slim plus the shipped image's document converter (Dockerfile.app); the tag follows
# the file, so a changed package list builds a new image and an unchanged one is reused.
APP_DOCKERFILE="$HERE/Dockerfile.app"
export E2E_APP_IMAGE="${E2E_APP_IMAGE:-pxh-e2e-app:$(sha256sum "$APP_DOCKERFILE" | cut -c1-12)}"
MONGO_CONTAINER="${E2E_MONGO_CONTAINER:-pxh-mongo-rs}"
REDIS_CONTAINER="${E2E_REDIS_CONTAINER:-pxh-redis}"
export E2E_NODE_HOME="${E2E_NODE_HOME:-$(readlink -f "$HOME/.local/node/current")}"
export E2E_SITE_PACKAGES="${E2E_SITE_PACKAGES:-$(readlink -f "$ROOT/backend/python/venv/lib/python3.12/site-packages" 2>/dev/null || true)}"
NODE_BIN="$E2E_NODE_HOME/bin"

say()  { printf 'E2E: %s\n' "$*"; }
default_engine() { python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import engines; print(engines.default_kind())' "$HERE"; }
die()  { printf 'E2E: %s\n' "$*" >&2; exit 1; }

# The one image this runner builds: no build context, and only when Dockerfile.app's tag is missing.
ensure_app_image() {
  docker image inspect "$E2E_APP_IMAGE" >/dev/null 2>&1 && return 0
  say "building the app image $E2E_APP_IMAGE (python:3.12-slim + document conversion)"
  docker build -q --label "$OWNER_LABEL" -t "$E2E_APP_IMAGE" - <"$APP_DOCKERFILE" >/dev/null \
    || die "could not build $E2E_APP_IMAGE from $APP_DOCKERFILE"
}

# The PostgreSQL lane's PgBouncer, built like the app image: from its file alone, only when its tag is missing.
ensure_pgbouncer_image() {
  docker image inspect "$E2E_PGBOUNCER_IMAGE" >/dev/null 2>&1 && return 0
  say "building the PgBouncer image $E2E_PGBOUNCER_IMAGE"
  docker build -q --label "$OWNER_LABEL" -t "$E2E_PGBOUNCER_IMAGE" - <"$PGBOUNCER_DOCKERFILE" >/dev/null \
    || die "could not build $E2E_PGBOUNCER_IMAGE from $PGBOUNCER_DOCKERFILE"
}
rand() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

# --- one run's identity and files -----------------------------------------------

run_vars() {
  RUN="$1"
  WORK="$STATE_DIR/$RUN"
  # The Compose project (and the Mongo database and Redis prefix named after it): pxh-e2e-<run>, or under another
  # prefix a lane asks for (E2E_PROJECT_PREFIX), remembered with the run so `down` finds it.
  if [[ -f "$WORK/project" ]]; then PROJECT="$(cat "$WORK/project")"; else PROJECT="${E2E_PROJECT_PREFIX:-pxh-e2e-}$RUN"; fi
  ENV_FILE="$WORK/stack.env"
  SEED_STATE="$WORK/seed.json"
  RUN_FILTER="label=$RUN_LABEL_KEY=$RUN"
}

# Docker gives a network made without IPAM a /16 or /20 of its default pools, and a shared daemon runs out of them
# ("all predefined address pools have been fully subnetted", WP-71). Each network of a new stack gets its own /26
# of the lanes' range instead, one no other network on the daemon holds, written once into the run's networks.yml
# (the broker's sandbox pools are 10.213/16 and 10.214/16, the Python rigs' 10.215/16).
assign_subnets() { # [--new]: on a stack made before, only networks it has not created yet (a rebuild onto a newer tree)
  local ids names name added=()
  names="$(compose config --networks)" || return 1
  if [[ -f "$WORK/networks.yml" ]]; then
    [[ "${1:-}" == --new ]] || return 0
    for name in $names; do
      grep -qx "  $name:" "$WORK/networks.yml" || docker network inspect "${PROJECT}_$name" >/dev/null 2>&1 \
        || added+=("$name")
    done
    [[ ${#added[@]} -gt 0 ]] || return 0
    names="${added[*]}"
  fi
  ids="$(docker network ls -q)"
  { if [[ -n "$ids" ]]; then docker network inspect $ids; else echo '[]'; fi; } | python3 -c '
import ipaddress, json, os, random, re, sys
out, names = sys.argv[1], sys.argv[2:]
taken = []
for net in json.load(sys.stdin):
    for config in (net.get("IPAM") or {}).get("Config") or []:
        try:
            taken.append(ipaddress.ip_network(config.get("Subnet") or "", strict=False))
        except ValueError:
            pass
kept = open(out).read() if os.path.exists(out) else "networks:\n"
taken += [ipaddress.ip_network(s) for s in re.findall(r"- subnet: (\S+)", kept)]
pool = list(ipaddress.ip_network("10.216.0.0/16").subnets(new_prefix=26))
random.shuffle(pool)
free = (s for s in pool if not any(s.version == t.version and s.overlaps(t) for t in taken))
lines = [kept.rstrip("\n")]
for name in names:
    lines += [f"  {name}:", "    ipam:", "      config:", f"        - subnet: {next(free)}"]
with open(out, "w") as f:
    f.write("\n".join(lines) + "\n")
' "$WORK/networks.yml" $names
}

compose() {
  local files=(-f "$BASE_COMPOSE" -f "$OVERLAY") profiles=() profile
  if [[ "$(envv E2E_MATRIX)" == "1" ]]; then
    files=(-f "$BASE_COMPOSE" -f "$SANDBOX_LEGACY" -f "$OVERLAY" -f "$MATRIX_OVERLAY")
  fi
  [[ "$(envv E2E_MCP_HUB)" == "1" ]] && files+=(-f "$MCP_OVERLAY")
  [[ "$(envv E2E_HUMAN)" == "1" ]] && files+=(-f "$HUMAN_OVERLAY")
  [[ "$(envv E2E_MONGO)" == "own" ]] && files+=(-f "$DEV_OVERLAY")
  [[ "$(envv E2E_ENGINE_SIGNIN)" == "1" ]] && files+=(-f "$TUNNELS_OVERLAY")
  [[ "$(envv E2E_DOC_STORE)" == "postgres" ]] && files+=(-f "$PG_OVERLAY")
  [[ "$(envv E2E_AIRGAP)" == "1" ]] && files+=(-f "$AIRGAP_OVERLAY")
  [[ "$(envv E2E_FAKE_GIT)" == "1" ]] && files+=(-f "$FAKEGIT_OVERLAY")
  # A compose call before a run has its folder (a test, `clean`) has no networks.yml yet.
  [[ -n "${WORK:-}" && -f "$WORK/networks.yml" ]] && files+=(-f "$WORK/networks.yml")
  for profile in $(envv COMPOSE_PROFILES | tr ',' ' '); do profiles+=(--profile "$profile"); done
  # Compose prefers the caller's environment to --env-file, and this script exports some of the file's names
  # (E2E_SITE_PACKAGES, E2E_NODE_HOME, ...), empty when a later command on a stack left up is run without them; that
  # broke every compose call of `durable-specs RUN` (EG-D1..D4 read no logs). The stack runs on the file it was
  # brought up with: its names are taken out of the caller's environment for the call.
  local name unset=()
  if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r name _; do
      [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && unset+=(-u "$name")
    done <"$ENV_FILE"
  fi
  env "${unset[@]}" docker compose "${files[@]}" -p "$PROJECT" --env-file "$ENV_FILE" "${profiles[@]}" "$@"
}

# R31-GVISO: the runtime engine sandboxes use, as the env file's lines. auto (default): the strongest the daemon
# offers, under the dev/test override (E2E_ENGINES_WITHOUT_GVISOR, default true); runsc: a production install's floor,
# no override, so the stack refuses engines without gVisor and admins see no warning; runc: hardened runc on a host
# with gVisor too, the broker withholding runsc (SANDBOX_WITHHOLD_RUNTIMES), under the override.
sandbox_runtime_env() {
  local profiles="${E2E_ACP_PROFILES:-unsigned}"
  case "${E2E_SANDBOX_RUNTIME:-auto}" in
    auto) printf 'E2E_SANDBOX_RUNTIME=auto\nE2E_ENGINES_WITHOUT_GVISOR=%s\nE2E_WITHHOLD_RUNTIMES=\n' \
      "${E2E_ENGINES_WITHOUT_GVISOR:-true}" ;;
    runsc)
      docker info --format '{{json .Runtimes}}' | grep -q '"runsc"' \
        || die "E2E_SANDBOX_RUNTIME=runsc: this Docker daemon has no runsc (deployment/docker-compose/GVISOR.md)"
      # The production floor reads the ACP profiles as a production install does: signed, never unsigned.
      profiles=signed
      printf 'E2E_SANDBOX_RUNTIME=runsc\nE2E_ENGINES_WITHOUT_GVISOR=false\nE2E_WITHHOLD_RUNTIMES=\n' ;;
    runc) printf 'E2E_SANDBOX_RUNTIME=runc\nE2E_ENGINES_WITHOUT_GVISOR=true\nE2E_WITHHOLD_RUNTIMES=runsc\n' ;;
    *) die "E2E_SANDBOX_RUNTIME=${E2E_SANDBOX_RUNTIME} is not auto, runc or runsc" ;;
  esac
  acp_profiles_env "$profiles"
}

# R32-TRUST (ADR-SEC-01 D7): how the stack's services trust the ACP agent profiles, as the env file's lines. signed:
# the stack's own test root signs them at build (stack_trust_root) and the services verify that signature, the path a
# release install takes; unsigned: the dev setting reads them unsigned. Its own choice, never the gVisor override's.
acp_profiles_env() {
  case "$1" in
    signed) printf 'E2E_ACP_PROFILES=signed\nE2E_ACP_PROFILES_UNSIGNED=false\nE2E_TEST_TRUST_ROOT=%s\n' "$TEST_TRUST_MOUNT" ;;
    unsigned) printf 'E2E_ACP_PROFILES=unsigned\nE2E_ACP_PROFILES_UNSIGNED=true\nE2E_TEST_TRUST_ROOT=\n' ;;
    *) die "E2E_ACP_PROFILES=$1 is not signed or unsigned" ;;
  esac
}

# R32-TRUST: a stack's runtime lines again at `rebuild`, from the caller's E2E_SANDBOX_RUNTIME, E2E_ACP_PROFILES and
# E2E_ENGINES_WITHOUT_GVISOR where it sets them (human-test-stack.sh --runtime), else the stack's own; a stack from
# before R32-TRUST has no profiles line and keeps reading them unsigned.
restack_runtime() {
  local lines name value
  lines="$(E2E_SANDBOX_RUNTIME="${E2E_SANDBOX_RUNTIME-$(envv E2E_SANDBOX_RUNTIME || true)}" \
    E2E_ACP_PROFILES="${E2E_ACP_PROFILES-$(envv E2E_ACP_PROFILES || true)}" \
    E2E_ENGINES_WITHOUT_GVISOR="${E2E_ENGINES_WITHOUT_GVISOR-$(envv E2E_ENGINES_WITHOUT_GVISOR || true)}" \
    sandbox_runtime_env)" || exit 1
  while IFS='=' read -r name value; do set_envv "$name" "$value"; done <<<"$lines"
}

# A fresh test key for a signed stack, its public half and its signature of this tree's ACP profiles in the run's
# folder (mounted read-only into the services); the private half is never written. The folder exists for every stack:
# Compose mounts it either way. Again at `rebuild`, so profiles this tree changed are signed as they are now.
stack_trust_root() {
  mkdir -p "$WORK/test-trust" && rm -f "$WORK/test-trust/"*
  [[ "$(envv E2E_ACP_PROFILES)" == "signed" ]] || return 0
  PYTHONPATH="$E2E_SITE_PACKAGES" python3 "$ROOT/scripts/engines/sign_vendor_rules.py" --test-root "$WORK/test-trust" \
    >"$WORK/test-trust.key-id" || die "could not sign the ACP profiles with the stack's test root"
  say "ACP profiles signed with the stack's test root $(cat "$WORK/test-trust.key-id")"
}

# The store and deployment factors of a stack profile (04 §4.1; scripts/e2e_covering_array.py STACKS), as the
# env file's lines. ST-2, ST-6 and ST-7 are Helm profiles: kind_matrix.sh, not this script.
stack_profile_env() {
  case "${E2E_STACK:-ST-1}" in
    ST-1) printf 'COMPOSE_PROFILES=sandbox,graph-neo4j,sandbox-broker\nDATA_STORE=neo4j\nKV_STORE_TYPE=redis\nMESSAGE_BROKER=redis\nE2E_MONGO=replset\n' ;;
    ST-3) printf 'COMPOSE_PROFILES=sandbox,graph-neo4j,broker-kafka,sandbox-broker\nDATA_STORE=neo4j\nKV_STORE_TYPE=redis\nMESSAGE_BROKER=kafka\nE2E_MONGO=replset\n' ;;
    ST-4) printf 'COMPOSE_PROFILES=sandbox,graph-neo4j,kv-etcd,mongo-standalone,sandbox-broker\nDATA_STORE=neo4j\nKV_STORE_TYPE=etcd\nMESSAGE_BROKER=redis\nE2E_MONGO=standalone\n' ;;
    ST-5) printf 'COMPOSE_PROFILES=sandbox,graph-arango,sandbox-broker\nDATA_STORE=arangodb\nKV_STORE_TYPE=redis\nMESSAGE_BROKER=redis\nE2E_MONGO=replset\n' ;;
    # G-50: ST-1's stores, with the stack's own MongoDB replica set and Redis (docker-compose.dev-harness.yml).
    dev-harness) printf 'COMPOSE_PROFILES=sandbox,graph-neo4j,dev-harness,sandbox-broker\nDATA_STORE=neo4j\nKV_STORE_TYPE=redis\nMESSAGE_BROKER=redis\nE2E_MONGO=own\n' ;;
    *) die "E2E_STACK=${E2E_STACK} is not a Compose stack profile (ST-1, ST-3, ST-4, ST-5, dev-harness)" ;;
  esac
}

envv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }

# Sets one line of the env file, replacing it or adding it (a stack from before the line existed).
set_envv() {
  local name="$1" value="$2" rest
  rest="$(grep -vE "^$name=" "$ENV_FILE" || true)"
  printf '%s\n%s=%s\n' "$rest" "$name" "$value" >"$ENV_FILE"
  chmod 600 "$ENV_FILE"
}

# A stack made before a generated credential existed lacks it, and the new code refuses to start without it (R38-IDENT
# added the component keys and DOCKER_PROXY_TOKEN, R43-SOCK the broker's DOCKER_PROXY_BROKER_TOKEN; an older human-test stack predates them). `rebuild` adds each missing one, as
# install.sh --upgrade does, and never changes one that is set. A store's password is not here: its volume was
# initialised without it, so a new value would lock the stack out of its own data. The cohesion test
# test_e2e_rebuild_backfills_generated_keys.py holds every generated value of the env file to one of the two lists.
BACKFILLED_KEYS=(E2E_LLM_CONTROL:16 EGRESS_PROXY_COMPONENT_KEY:32 SANDBOX_BROKER_COMPONENT_KEY:32 DOCKER_PROXY_TOKEN:32
  DOCKER_PROXY_BROKER_TOKEN:32 JWT_SECRET:32 SCOPED_JWT_SECRET:32 COOKIE_SECRET:32 REDIS_PASSWORD:32 KAFKA_PASSWORD:24)
NOT_BACKFILLED_KEYS=(E2E_LLM_KEY E2E_ADMIN_PASSWORD NEO4J_PASSWORD ARANGO_PASSWORD QDRANT_API_KEY E2E_JIRA_TOKEN
  PG_ADMIN_PASSWORD PG_MIGRATE_PASSWORD PG_NODE_PASSWORD PG_JOBS_PASSWORD MONGO_PASSWORD)
backfill_env() {
  local spec name
  for spec in "${BACKFILLED_KEYS[@]}"; do
    name="${spec%%:*}"
    grep -qE "^$name=" "$ENV_FILE" || set_envv "$name" "$(rand "${spec#*:}")"
  done
  grep -qE '^DOCKER_GID=' "$ENV_FILE" || set_envv DOCKER_GID "$(stat -c %g /var/run/docker.sock)"
  # R51-COMPAT: a stack from before the security profile ran the hardened defaults, and the broker and egress proxy
  # moved to their own profile (sandbox-broker).
  grep -qE '^PIPESHUB_HARDENED=' "$ENV_FILE" || set_envv PIPESHUB_HARDENED true
  case ",$(envv COMPOSE_PROFILES)," in
    *,sandbox-broker,*) ;;
    *,sandbox,*) set_envv COMPOSE_PROFILES "$(envv COMPOSE_PROFILES),sandbox-broker" ;;
  esac
}

# --- teardown (by this run's label only) ----------------------------------------

down_run() {
  run_vars "$1"
  [[ -d "$WORK" ]] || { say "no run $RUN"; return 0; }
  say "tearing down $PROJECT"
  docker ps -aq --filter "$RUN_FILTER" --filter "label=$OWNER_LABEL" --filter "label=io.pipeshub.e2e.role=playwright" \
    | xargs -r docker rm -f >/dev/null 2>&1 || true
  # The empty .auth is every run's mount point on this tree: removed while another run's Playwright still mounts it,
  # that run's sign-in state is gone mid-spec (two durable lanes at once, R25-OPS), so it goes only with the last one.
  if [[ -f "$WORK/made-auth-dir" ]] \
    && [[ -z "$(docker ps -q --filter "label=$OWNER_LABEL" --filter "label=io.pipeshub.e2e.role=playwright")" ]]; then
    rmdir "$ROOT/frontend/.auth" 2>/dev/null || true
  fi
  rm -f "$WORK/made-auth-dir"
  local org=""
  [[ -f "$SEED_STATE" ]] && org="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("orgId",""))' "$SEED_STATE" 2>/dev/null || true)"
  if [[ -n "$org" ]]; then
    docker ps -aq --filter "label=$BROKER_MANAGED" --filter "label=orgId=$org" | xargs -r docker rm -f >/dev/null 2>&1 || true
    docker network ls -q --filter "label=$BROKER_MANAGED" --filter "label=orgId=$org" | xargs -r docker network rm >/dev/null 2>&1 || true
  fi
  if [[ -f "$ENV_FILE" ]]; then
    compose logs --no-color --timestamps >"$WORK/logs/compose.log" 2>&1 || true
  fi
  for net in pipeshub sandbox_kv sandbox_plane; do
    for c in "$MONGO_CONTAINER" "$REDIS_CONTAINER"; do
      docker network disconnect -f "${PROJECT}_$net" "$c" >/dev/null 2>&1 || true
    done
  done
  if [[ -f "$ENV_FILE" ]]; then
    compose down -v --remove-orphans --timeout 20 >/dev/null 2>&1 || true
  fi
  # Durable runs keep session folders on the broker's global volume under this install's id (the project).
  if docker volume inspect "$BROKER_DATA_VOLUME" >/dev/null 2>&1; then
    DATA_VOLUME_RW=1 data_volume rm -rf "/d/$PROJECT" >/dev/null 2>&1 || true
    if [[ -f "$WORK/made-data-volume" && -z "$(data_volume ls -A /d 2>/dev/null)" ]]; then
      # The helper that just read it may still hold the volume for a moment.
      for _ in 1 2 3 4 5; do
        docker volume rm "$BROKER_DATA_VOLUME" >/dev/null 2>&1 && break
        sleep 1
      done
    fi
  fi
  # A broker that died mid-task leaves sandbox networks attached to the proxy; down removed the proxy.
  docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" | xargs -r docker rm -f >/dev/null 2>&1 || true
  docker network ls -q --filter "label=com.docker.compose.project=$PROJECT" | xargs -r docker network rm >/dev/null 2>&1 || true
  docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT" | xargs -r docker volume rm >/dev/null 2>&1 || true
  if [[ -f "$WORK/mongo-db" ]]; then
    docker exec "$MONGO_CONTAINER" mongosh --quiet --port "$(cat "$WORK/mongo-port")" \
      --eval "db.getSiblingDB('$(cat "$WORK/mongo-db")').dropDatabase()" >/dev/null 2>&1 || true
  fi
  if [[ -f "$WORK/redis-db" ]]; then
    if redis_db_is_mine "$(cat "$WORK/redis-db")"; then
      docker exec "$REDIS_CONTAINER" redis-cli -n "$(cat "$WORK/redis-db")" FLUSHDB >/dev/null 2>&1 || true
    else
      say "Redis DB $(cat "$WORK/redis-db") is not claimed by $PROJECT: left as it is"
    fi
  fi
  verify_gone "$org" || touch "$WORK/failed"
  if [[ "${E2E_KEEP_WORK:-}" == "1" || -f "$WORK/failed" ]]; then
    # Kept for its logs only: it claims no Redis DB and is no run to reap.
    rm -f "$WORK/owner.pid" "$WORK/redis-db" "$WORK/mongo-db" "$WORK/stack.env"
    rm -rf "$WORK/node-dist"
    say "logs kept in $STATE_DIR/kept-$RUN/logs"
    rm -rf "$STATE_DIR/kept-$RUN"
    mv "$WORK" "$STATE_DIR/kept-$RUN" 2>/dev/null || true
  else
    rm -rf "$WORK"
  fi
}

verify_gone() {
  local org="$1" left=""
  left+="$(docker ps -aq --filter "$RUN_FILTER")"
  left+="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")"
  left+="$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")"
  left+="$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")"
  if [[ -n "$org" ]]; then
    left+="$(docker ps -aq --filter "label=$BROKER_MANAGED" --filter "label=orgId=$org")"
    left+="$(docker network ls -q --filter "label=$BROKER_MANAGED" --filter "label=orgId=$org")"
  fi
  if [[ -n "$left" ]]; then
    say "teardown left resources behind: $left"
    return 1
  fi
  say "teardown: nothing of $PROJECT is left"
}

# A run whose script died (killed, host restart) left its stack: remove it by its own label.
reap_dead_runs() {
  mkdir -p "$STATE_DIR"
  local dir pid
  for dir in "$STATE_DIR"/*/; do
    [[ -f "$dir/owner.pid" && "$(basename "$dir")" != kept-* ]] || continue
    pid="$(cat "$dir/owner.pid")"
    if ! kill -0 "$pid" 2>/dev/null; then
      say "removing the leftovers of an earlier run ($(basename "$dir"))"
      (down_run "$(basename "$dir")") || true
    fi
  done
}

# --- bring-up ---------------------------------------------------------------------

# A DB of the shared Redis is this run's only while it holds this run's claim key: other state directories (other
# lanes, other checkouts) pick from the same DBs, and `down` clears a DB only when the claim is still its own
# (redisiso: a stack lost its config to another job's flush of the shared Redis).
REDIS_CLAIM_KEY="pxh-e2e:claim"

pick_redis_db() {
  local db
  for db in 15 14 13 12 11 10 9 8; do
    [[ "$(docker exec "$REDIS_CONTAINER" redis-cli -n "$db" DBSIZE)" == "0" ]] || continue
    [[ "$(docker exec "$REDIS_CONTAINER" redis-cli -n "$db" SET "$REDIS_CLAIM_KEY" "$PROJECT" NX)" == "OK" ]] || continue
    # Another writer got in between the size and the claim: not ours after all.
    if [[ "$(docker exec "$REDIS_CONTAINER" redis-cli -n "$db" DBSIZE)" != "1" ]]; then
      release_redis_claim "$db"
      continue
    fi
    echo "$db"; return 0
  done
  return 1
}

redis_db_is_mine() {
  [[ "$(docker exec "$REDIS_CONTAINER" redis-cli -n "$1" GET "$REDIS_CLAIM_KEY")" == "$PROJECT" ]]
}

release_redis_claim() {
  redis_db_is_mine "$1" && docker exec "$REDIS_CONTAINER" redis-cli -n "$1" DEL "$REDIS_CLAIM_KEY" >/dev/null
  return 0
}

container_port() {
  # The port the shared container listens on inside (pxh-mongo-rs publishes the same number). Empty when it has only
  # the default port (CI's plain mongo container): under pipefail a grep that matches nothing would end the run silently.
  docker inspect --format '{{range $p, $_ := .NetworkSettings.Ports}}{{$p}} {{end}}' "$1" \
    | tr ' ' '\n' | { grep -v -e '^$' -e "^$2/" || true; } | head -1 | cut -d/ -f1
}

prepare() {
  run_vars "$1"
  mkdir -p "$WORK/logs" "$WORK/node-dist" "$WORK/pw" "$WORK/control"
  echo $$ >"$WORK/owner.pid"
  echo "$PROJECT" >"$WORK/project"
  docker volume inspect "$BROKER_DATA_VOLUME" >/dev/null 2>&1 || touch "$WORK/made-data-volume"
  ensure_app_image
  local images=(python:3.12-slim neo4j:5.26.0 qdrant/qdrant:v1.15.5 "$SANDBOX_IMAGE" "$PW_IMAGE")
  # A laptop's stack runs no Playwright and pulls the public images it lacks; the lanes pull nothing.
  [[ "${E2E_DEV:-}" == "1" ]] && images=(python:3.12-slim neo4j:5.26.0 qdrant/qdrant:v1.15.5 mongo:8.0.17 redis:7.4.12-bookworm "$SANDBOX_IMAGE")
  # Only a lane that runs engines starts their run image; the PR lane's worker has engine runs off and never builds it.
  if [[ "${E2E_DURABLE:-}" == "1" && "${E2E_ENGINE_RUNS:-false}" == "true" ]]; then
    local engine_image
    engine_image="$(python3 "$HERE/engines.py" image "${E2E_ENGINE:-$(default_engine)}")" || die "no run image for E2E_ENGINE"
    images+=("$engine_image")
  fi
  if [[ "${E2E_DOC_STORE:-mongo}" == "postgres" ]]; then
    images+=("$PG_IMAGE")
    [[ -d "${E2E_PG_MIGRATE_SITE:-}/alembic" && -d "${E2E_PG_MIGRATE_SITE:-}/psycopg" ]] \
      || die "E2E_DOC_STORE=postgres needs E2E_PG_MIGRATE_SITE: a site-packages with backend/db/requirements.lock installed"
  elif [[ "${E2E_DOC_STORE:-mongo}" != "mongo" ]]; then
    die "E2E_DOC_STORE=${E2E_DOC_STORE} is not a document store of this lane (mongo, postgres)"
  fi
  if [[ "${E2E_HUMAN:-}" == "1" ]]; then
    local kind
    for kind in "${HUMAN_ENGINE_KINDS[@]}"; do images+=("$(python3 "$HERE/engines.py" image "$kind")"); done
  fi
  if [[ "${E2E_MCP_HUB:-}" == "1" ]]; then
    [[ -n "${E2E_MCP_RUNNER_IMAGE:-}" ]] || die "E2E_MCP_HUB=1 needs E2E_MCP_RUNNER_IMAGE (the STDIO servers' image)"
    images+=("$E2E_MCP_RUNNER_IMAGE")
  fi
  for image in "${images[@]}"; do
    docker image inspect "$image" >/dev/null 2>&1 && continue
    if [[ "${E2E_DEV:-}" == "1" ]]; then
      say "pulling $image"
      docker pull -q "$image" >/dev/null || die "could not pull $image"
    else
      die "image $image is not on this host (nothing is pulled or built)"
    fi
  done
  [[ "${E2E_DOC_STORE:-mongo}" == "postgres" ]] && ensure_pgbouncer_image
  if [[ "${E2E_DEV:-}" != "1" ]]; then
    for c in "$MONGO_CONTAINER" "$REDIS_CONTAINER"; do
      [[ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" == "true" ]] || die "$c is not running"
    done
  fi
  [[ -x "$NODE_BIN/node" ]] || die "Node 22 not found at E2E_NODE_HOME=$E2E_NODE_HOME"
  [[ "$PW_IMAGE" == *":v$PW_VERSION-"* ]] \
    || die "E2E_PLAYWRIGHT_IMAGE $PW_IMAGE is not Playwright $PW_VERSION, which frontend/ installs (its browsers would be missing)"
  [[ -d "$E2E_SITE_PACKAGES" ]] || die "E2E_SITE_PACKAGES is not a site-packages directory"
  # The stack imports this tree against these packages; a venv from an older lock starts a service that dies at
  # import with an error naming the wrong thing (2026-11-08: html-to-markdown 2.x read as a missing router).
  python3 "$ROOT/scripts/supply_chain.py" installed "$E2E_SITE_PACKAGES" >&2 \
    || die "E2E_SITE_PACKAGES ($E2E_SITE_PACKAGES) does not match backend/python/requirements/runtime.lock"
  [[ -d "$ROOT/backend/nodejs/apps/node_modules" && -d "$ROOT/frontend/node_modules" ]] \
    || die "node_modules missing in backend/nodejs/apps or frontend"

  local redis_db=0 mongo_port=27017 mongo_password=""
  # A person's stack and a laptop's keep their config in their own Redis (docker-compose.human.yml,
  # docker-compose.dev-harness.yml); the lanes share pxh-redis by DB.
  if [[ "${E2E_HUMAN:-}" != "1" && "${E2E_DEV:-}" != "1" ]]; then
    redis_db="$(pick_redis_db)" || die "no free Redis DB index (8-15) in $REDIS_CONTAINER"
    echo "$redis_db" >"$WORK/redis-db"
  fi
  if [[ "${E2E_DEV:-}" != "1" ]]; then
    mongo_port="$(container_port "$MONGO_CONTAINER" 27017)"
    [[ -n "$mongo_port" ]] || mongo_port=27017
  fi
  echo "$mongo_port" >"$WORK/mongo-port"
  local mongo_db="${PROJECT//-/_}" profile_env mongo_uri replica_set=true
  profile_env="$(stack_profile_env)"
  mongo_uri="mongodb://mongodb:$mongo_port/?directConnection=true"
  if grep -qx 'E2E_MONGO=own' <<<"$profile_env"; then
    # The stack's own authenticated single-node replica set (the base file's mongodb), removed with the project.
    mongo_password="$(rand 12)"
    mongo_uri="mongodb://admin:$mongo_password@mongodb:27017/?authSource=admin&replicaSet=rs0&w=majority"
  elif grep -qx 'E2E_MONGO=standalone' <<<"$profile_env"; then
    # The stack's own standalone MongoDB (docker-compose.matrix.yml), removed with the project.
    mongo_uri="mongodb://mongodb:27017/"
    replica_set=false
  else
    if docker exec "$MONGO_CONTAINER" mongosh --quiet --port "$mongo_port" --eval 'db.getMongo().getDBNames().join("\n")' | grep -qx "$mongo_db"; then
      die "Mongo database $mongo_db already exists"
    fi
    echo "$mongo_db" >"$WORK/mongo-db"
  fi

  build_node_ui "$WORK/node-dist"

  local secret runtime_env redis_password; secret="$(rand 32)"; redis_password="$(rand 32)"
  runtime_env="$(sandbox_runtime_env)" || exit 1
  cat >"$ENV_FILE" <<EOF
$profile_env
$runtime_env
E2E_RUN=$RUN
E2E_PROJECT=$PROJECT
E2E_UID=$(id -u)
E2E_GID=$(id -g)
E2E_DOCKER_GID=$(stat -c %g /var/run/docker.sock)
DOCKER_GID=$(stat -c %g /var/run/docker.sock)
E2E_SRC=$ROOT
E2E_SITE_PACKAGES=$E2E_SITE_PACKAGES
E2E_NODE_HOME=$E2E_NODE_HOME
E2E_WORK=$WORK
E2E_APP_PORT=${E2E_APP_PORT_WANT:-$(free_port)}
E2E_LLM_PORT=$(free_port)
E2E_SAAS_PORT=$(free_port)
E2E_LLM_KEY=sk-e2e-$(rand 16)
E2E_LLM_CONTROL=$(rand 16)
E2E_EGRESS_SUBNETS=${E2E_EGRESS_SUBNETS:-10.231.0.0/16}
E2E_FAULT=${E2E_FAULT:-}
E2E_ADMIN_EMAIL=${E2E_ADMIN_EMAIL_WANT:-e2e-admin-$RUN@e2etest.pipeshub.local}
E2E_ADMIN_PASSWORD=E2e-$(rand 10)!1a
SECRET_KEY=$secret
# ADR-KEY-02: the sandbox-plane components' own keys (install.sh generates them the same way).
EGRESS_PROXY_COMPONENT_KEY=$(rand 32)
SANDBOX_BROKER_COMPONENT_KEY=$(rand 32)
# The legacy Docker API proxy's caller credential, shared by the app and the proxy (SEC-A-04, ADR-KEY-02).
DOCKER_PROXY_TOKEN=$(rand 32)
DOCKER_PROXY_BROKER_TOKEN=$(rand 32)
JWT_SECRET=$(rand 32)
SCOPED_JWT_SECRET=$(rand 32)
COOKIE_SECRET=$(rand 32)
NEO4J_PASSWORD=$(rand 12)
ARANGO_PASSWORD=$(rand 12)
QDRANT_API_KEY=$(rand 16)
# SEC-D-06: every store starts with its credential (install.sh generates the same). The stack's own Redis takes
# REDIS_PASSWORD; on the shared pxh-redis, which has none, the app signs in without one and the store-auth check
# only warns (E2E_APP_REDIS_PASSWORD, E2E_STORE_AUTH_CHECK below).
REDIS_PASSWORD=${redis_password:-$(rand 32)}
KAFKA_PASSWORD=$(rand 24)
E2E_JIRA_TOKEN=pat-canary-$(rand 12)
E2E_MATRIX=${E2E_MATRIX:-}
E2E_INDEXING=${E2E_INDEXING:-}
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=$redis_db
REDIS_KV_PREFIX=$PROJECT:kv:
MONGO_URI=$mongo_uri
MONGO_DB_NAME=$mongo_db
MONGO_USERNAME=admin
# On the shared pxh-mongo-rs the app signs in through MONGO_URI and the stack's own mongodb service never starts; Compose
# still requires a value to render (SEC-D-06), so it gets one nothing uses.
MONGO_PASSWORD=${mongo_password:-$(rand 16)}
REPLICA_SET_AVAILABLE=$replica_set
# The harness switch (ADR-OPS-06, on by default since 2026-11-20): on for every lane but run.sh off/off-up
# (E2E_HARNESS=off), a stack as an install that opted out; Labs ENABLE_AGENT_HARNESS (off by default) then decides per
# org, and seed.py durable turns it on explicitly.
AGENT_HARNESS_RELAY_ENABLED=true
AGENT_HARNESS_WORKER_ENABLED=$([[ "${E2E_HARNESS:-on}" == off ]] && echo false || echo true)
E2E_HARNESS=${E2E_HARNESS:-on}
SANDBOX_MODE=broker
# A lane stack is a fresh install, so it runs the hardened security profile (ADR-OPS-07), as install.sh writes it.
PIPESHUB_HARDENED=true
SANDBOX_DOCKER_IMAGE=$SANDBOX_IMAGE
LOG_LEVEL=info
# Node's global limiter runs before authentication, so it keys every lane request by the one client address
# (the Docker gateway): nine Playwright workers, seeding and the specs' own API calls share one budget and met
# 429 (H5). The lane's allowance; the product default (Compose 2000, Node 1000) is unchanged.
MAX_REQUESTS_PER_MINUTE=100000
# Every Playwright worker signs in once as the same admin (ADR-WEB-02 D9), beside the specs' own UI sign-ins, so the
# account's sign-in limit (10 a minute) made them wait out 429s (R46LANE-FU-4). The lane's allowance; the product
# default is unchanged and the specs keep their back-off for stacks without it.
MAX_AUTH_REQUESTS_PER_MINUTE=1000
# run.sh durable: the worker (app.harness_main) runs engine runs (E2E2).
E2E_DURABLE=${E2E_DURABLE:-}
E2E_ENGINE_RUNS=${E2E_ENGINE_RUNS:-false}
# DP7-MVP-1: the coding engine of the engine agent and of a new default chat (engines.py).
E2E_ENGINE=${E2E_ENGINE:-$(default_engine)}
# E2E_MCP_HUB=1: the hub and the STDIO runners on, and the scripted MCP servers (docker-compose.mcp.yml).
E2E_MCP_HUB=${E2E_MCP_HUB:-}
E2E_MCP_RUNNER_IMAGE=${E2E_MCP_RUNNER_IMAGE:-}
E2E_MCP_PORT=$(free_port)
# Seconds the hub keeps a server's tool list: the MCP-E2E rows watch a change take effect (1); human-up keeps the
# product's default, so a job's carried pins read the list its parent made (R36COVER-FU-7).
E2E_MCP_SCHEMA_TTL_S=${E2E_MCP_SCHEMA_TTL_S:-1}
E2E_MCP_RUNNER_IDLE_TTL_S=${E2E_MCP_RUNNER_IDLE_TTL_S:-60}
# run.sh human-up: docker-compose.human.yml, and where the stack keeps what outlives a container (model cache).
E2E_HUMAN=${E2E_HUMAN:-}
E2E_HUMAN_DIR=${E2E_HUMAN_DIR:-}
E2E_OWN_REDIS=${E2E_OWN_REDIS:-${E2E_HUMAN:-}}
E2E_APP_REDIS_PASSWORD=$( [[ "${E2E_OWN_REDIS:-${E2E_HUMAN:-}}" == 1 ]] && printf '%s' "$redis_password" )
# The shared pxh-redis and pxh-mongo-rs take no login, so a stack that borrows either only warns; its own Redis is still
# probed, and a finding for it would be logged (store_auth.unauthenticated_store).
E2E_STORE_AUTH_CHECK=$( [[ "${E2E_OWN_REDIS:-${E2E_HUMAN:-}}" == 1 && "$(printf '%s' "$profile_env" | sed -n 's/^E2E_MONGO=//p')" == own ]] && echo enforce || echo warn )
# The query service under the supervisor (human-up, matrix-up): kept in the file, so a rebuild restarts it supervised and
# the recovery journeys can still kill and restart it.
E2E_SUPERVISE_QUERY=${E2E_SUPERVISE_QUERY:-}
# The sign-in add-on (TUNNELS_OVERLAY) and the deployment's sign-in keep times (empty: the product defaults).
E2E_ENGINE_SIGNIN=${E2E_ENGINE_SIGNIN:-}
E2E_SIGNIN_IDLE_MINUTES=${E2E_SIGNIN_IDLE_MINUTES:-}
E2E_SIGNIN_MAX_HOURS=${E2E_SIGNIN_MAX_HOURS:-}
# The stack's public name, when a proxy in front of the tunnel serves it (human-test-stack.sh --public-url).
E2E_PUBLIC_URL=${E2E_PUBLIC_URL:-}
# E2E_DOC_STORE=postgres (docker-compose.pg.yml): the database, its administrator and the three logins the lane uses.
E2E_DOC_STORE=${E2E_DOC_STORE:-mongo}
# E2E_AIRGAP=1 (docker-compose.airgap.yml): no network of the stack routes out.
E2E_AIRGAP=${E2E_AIRGAP:-}
# E2E_FAKE_GIT=1 (docker-compose.fakegit.yml): the git host the repository journeys clone from, and its state folder.
E2E_FAKE_GIT=${E2E_FAKE_GIT:-}
E2E_FAKE_GIT_DIR=$WORK/fake-git
# R35-SLACK (E2E_FAKE_SLACK=1): the Slack bot runs against the scripted Slack workspace (fake_saas/fake_slack.py).
E2E_FAKE_SLACK=${E2E_FAKE_SLACK:-}
E2E_PG_MIGRATE_SITE=${E2E_PG_MIGRATE_SITE:-}
E2E_PGBOUNCER_IMAGE=$E2E_PGBOUNCER_IMAGE
PG_DATABASE=pipeshub
PG_ADMIN_PASSWORD=$(rand 16)
PG_MIGRATE_PASSWORD=$(rand 16)
PG_NODE_PASSWORD=$(rand 16)
PG_JOBS_PASSWORD=$(rand 16)
EOF
  write_public_url
  chmod 600 "$ENV_FILE"
  stack_trust_root
}

# FRONTEND_PUBLIC_URL and ALLOWED_ORIGINS from the env file's E2E_PUBLIC_URL. Node serves the UI (the image's layout), so
# the app port is the one origin, which a person's browser reaches through an SSH tunnel as 127.0.0.1 or localhost; a
# public URL becomes FRONTEND_PUBLIC_URL and one more origin. The previous public URL (this option's or one edited in by
# hand) leaves the origins with it; any other origin stays.
write_public_url() {
  local port public previous loopback origin seen="," kept=() current=()
  port="$(envv E2E_APP_PORT)"
  public="$(envv E2E_PUBLIC_URL || true)"
  previous="$(envv FRONTEND_PUBLIC_URL || true)"
  loopback="http://127.0.0.1:$port"
  IFS=, read -ra current <<<"$(envv ALLOWED_ORIGINS || true)"
  for origin in "$loopback" "http://localhost:$port" "${current[@]}" ${public:+"$public"}; do
    [[ -n "$origin" && "$seen" != *",$origin,"* ]] || continue
    [[ "$origin" == "$previous" && "$origin" != "$loopback" && "$origin" != "$public" ]] && continue
    kept+=("$origin")
    seen+="$origin,"
  done
  set_envv ALLOWED_ORIGINS "$(IFS=,; echo "${kept[*]}")"
  set_envv FRONTEND_PUBLIC_URL "${public:-$loopback}"
}

build_node_ui() {
  local dist="$1"
  export PATH="$NODE_BIN:$PATH"
  say "building Node (tsc) from this tree"
  (cd "$ROOT/backend/nodejs/apps" && npx tsc -p tsconfig.json --outDir "$dist") >"$WORK/logs/tsc.log" 2>&1 \
    || { tail -20 "$WORK/logs/tsc.log"; die "Node did not build"; }
  mkdir -p "$dist/node_modules" "$dist/public"
  build_ui "$dist"
}

# The UI as the image builds it (Dockerfile stage 2: a static export Node serves from dist/public).
# E2E_UI_CACHE=1 keeps the export (about 20 MB) keyed by the content of frontend/, so a later run of
# an unchanged tree skips the build; `run.sh clean` removes it.
build_ui() {
  local dist="$1" key cache=""
  [[ "${E2E_FAULT:-}" == "ui" ]] && return 0
  if [[ "${E2E_UI_CACHE:-}" == "1" ]]; then
    key="$( { git -C "$ROOT" ls-files -s -- frontend; git -C "$ROOT" diff HEAD -- frontend;
              git -C "$ROOT" ls-files --others --exclude-standard -- frontend | xargs -r -I{} sha256sum "$ROOT/{}"; } \
            | sha256sum | cut -c1-16)"
    cache="$STATE_DIR/ui-$key"
    if [[ -f "$cache/index.html" ]]; then
      cp -a "$cache/." "$dist/public/"
      return 0
    fi
  fi
  say "building the UI (static export, as the image does)"
  # What `next build` writes beside the source is removed after, unless it was there before.
  local generated=() file
  for file in next-env.d.ts tsconfig.tsbuildinfo; do
    [[ -e "$ROOT/frontend/$file" ]] || generated+=("$ROOT/frontend/$file")
  done
  (cd "$ROOT/frontend" && rm -rf out && ELECTRON_STATIC=1 NEXT_TELEMETRY_DISABLED=1 npm run build) >"$WORK/logs/ui-build.log" 2>&1 \
    || { tail -30 "$WORK/logs/ui-build.log"; die "the UI did not build"; }
  cp -a "$ROOT/frontend/out/." "$dist/public/"
  if [[ -n "$cache" ]]; then
    find "$STATE_DIR" -maxdepth 1 -name 'ui-*' -type d -exec rm -rf {} + 2>/dev/null || true
    mkdir -p "$cache" && cp -a "$ROOT/frontend/out/." "$cache/"
  fi
  rm -rf "$ROOT/frontend/out" "$ROOT/frontend/.next"
  rm -f "${generated[@]}"
}

health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || echo missing; }

fail_service() {
  local name="$1" logfile="${2:-}"
  touch "$WORK/failed"
  echo "E2E: $name did not come up" >&2
  # A container the daemon refused to start has no logs; its State.Error is the only record of why.
  local cids; cids="$(compose ps -aq "$name" 2>/dev/null || true)"
  [[ -z "$cids" ]] || docker inspect -f 'E2E: {{.Name}} state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}}' $cids >&2 2>/dev/null || true
  if [[ -n "$logfile" && -f "$logfile" ]]; then
    tail -40 "$logfile" >&2
  else
    compose logs --no-color --tail 40 "$name" >&2 2>/dev/null || true
  fi
  exit 1
}

wait_compose_healthy() {
  local service="$1" timeout="$2" cid status start=$SECONDS
  cid="$(compose ps -aq "$service")"
  while (( SECONDS - start < timeout )); do
    status="$(health_of "$cid")"
    [[ "$status" == "healthy" ]] && return 0
    [[ "$status" == "exited" || "$status" == "dead" || "$status" == "missing" ]] && break
    sleep 2
  done
  fail_service "$service"
}

app_exec_http() {
  # GET a URL from inside the app container (its Python services have no published port).
  docker exec "$(compose ps -q pipeshub-ai)" python -c \
    "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=3)" "$1" >/dev/null 2>&1
}

wait_app_process() {
  local name="$1" url="$2" timeout="$3" start=$SECONDS
  while (( SECONDS - start < timeout )); do
    app_exec_http "$url" && return 0
    [[ -f "$WORK/logs/$name.exited" ]] && break
    sleep 2
  done
  fail_service "$name" "$WORK/logs/$name.log"
}

wait_host_url() {
  local name="$1" url="$2" timeout="$3" logfile="$4" start=$SECONDS
  while (( SECONDS - start < timeout )); do
    curl -sf -o /dev/null --max-time 60 "$url" && return 0
    [[ -f "$WORK/logs/$name.exited" ]] && break
    sleep 2
  done
  fail_service "$name" "$logfile"
}

# ADR-CAP-01 D4: Node's capabilities are settled once every probe has answered (the broker starts after Node, so the
# boot probe cannot ask it). A lane that drives the stack before then races the probe; a stack whose broker is left
# out on purpose (E2E_FAULT) never settles and is not waited on. No agentHarness.capabilities: the harness is off.
wait_capabilities() {
  local timeout="$1" start=$SECONDS state=""
  case "${E2E_FAULT:-}" in sandbox-broker|egress-proxy|node) return 0 ;; esac
  while (( SECONDS - start < timeout )); do
    state="$(curl -sf --max-time 10 "http://127.0.0.1:$(envv E2E_APP_PORT)/api/v1/health" \
      | python3 -c 'import json, sys; print(json.load(sys.stdin).get("agentHarness", {}).get("capabilities", "absent"))' \
      2>/dev/null)"
    [[ "$state" == "settled" || "$state" == "absent" ]] && return 0
    sleep 1
  done
  die "Node's capabilities did not settle in ${timeout}s (last: ${state:-no answer}); see $WORK/logs/node.log (capability.probe_failed)"
}

# The durable worker is up when it has started and, with engine runs on, advertises executor_engine (it plans
# engine runs only if the broker answered when it started).
wait_worker() {
  local timeout="$1" start=$SECONDS
  while (( SECONDS - start < timeout )); do
    if grep -q '"worker.started"\|worker.started' "$WORK/logs/worker.log" 2>/dev/null; then
      if [[ "$(envv E2E_ENGINE_RUNS)" != "true" ]] \
        || grep 'worker.capabilities' "$WORK/logs/worker.log" | tail -1 | grep -q executor_engine; then
        return 0
      fi
    fi
    [[ -f "$WORK/logs/worker.exited" ]] && break
    sleep 2
  done
  fail_service worker "$WORK/logs/worker.log"
}

# The local embedding service answers before its model is loaded, and the first start downloads the model into
# E2E_HUMAN_DIR; indexing needs it loaded.
wait_embedding_model() {
  local timeout="$1" start=$SECONDS
  while (( SECONDS - start < timeout )); do
    docker exec "$(compose ps -q pipeshub-ai)" python -c \
      'import json, sys, urllib.request; sys.exit(json.load(urllib.request.urlopen("http://localhost:8002/health", timeout=5)).get("status") != "healthy")' \
      >/dev/null 2>&1 && return 0
    [[ -f "$WORK/logs/embedding.exited" ]] && break
    sleep 5
  done
  fail_service embedding "$WORK/logs/embedding.log"
}

# The volumes the app writes are created as root (the app image has no such folders, so Docker makes their mount
# points root's); the app runs as the host user, without the shipped image's entrypoint that hands them to its user.
# Each one it writes is given to that user here, on a new stack and on a refreshed one: the blob volume
# (docker-compose.e2e.yml: pipeshub_root_local at /tmp/.local) and the data volume at /data/pipeshub, where the
# worker keeps its repository cache (without it every repository_fetch was refused EACCES). Only each volume's
# top folder: what is inside was written by the host user.
APP_WRITABLE_VOLUMES=(pipeshub_root_local pipeshub_data)
own_app_volumes() {
  local args=(docker run --rm --label "$OWNER_LABEL" --label "$RUN_LABEL_KEY=$RUN") mounts=() i=0 name
  for name in "${APP_WRITABLE_VOLUMES[@]}"; do
    args+=(-v "${PROJECT}_$name:/v/$i")
    mounts+=("/v/$i")
    i=$((i + 1))
  done
  "${args[@]}" python:3.12-slim chown "$(envv E2E_UID):$(envv E2E_GID)" "${mounts[@]}"
}

# A container from before the blob volume's mount (one of $1) kept its blobs in its own /tmp/.local: they are
# copied onto the volume, so a rebuild keeps them.
own_blob_volume() {
  local old="${1:-}" volume="${PROJECT}_pipeshub_root_local" helper
  helper=(docker run --rm -i --label "$OWNER_LABEL" --label "$RUN_LABEL_KEY=$RUN" -v "$volume:/v")
  own_app_volumes
  if [[ -n "$old" ]] && ! docker inspect -f '{{range .Mounts}}{{.Destination}} {{end}}' "$old" | grep -qw /tmp/.local; then
    say "copying the blobs the old container kept outside the volume onto it"
    docker cp "$old:/tmp/.local/." - | "${helper[@]}" --user "$(envv E2E_UID):$(envv E2E_GID)" python:3.12-slim \
      tar -x -C /v
  fi
}

# routefix: a person's stack from before its own Redis kept its config in a pxh-redis DB, where another job's
# FLUSHALL wiped it. Its keys are copied as stored (MIGRATE COPY: raw values, nothing decrypted) into the project's
# Redis, which the stack uses from then on. The shared DB keeps its copy until someone clears it.
adopt_own_redis() {
  local db target
  db="$(cat "$WORK/redis-db")"
  compose up -d --no-deps redis >>"$WORK/logs/compose-up.log" 2>&1 || die "compose could not start the stack's Redis"
  wait_compose_healthy redis 60
  target="$(docker inspect -f '{{.Name}}' "$(compose ps -q redis)")"
  say "copying the stack's config from $REDIS_CONTAINER DB $db into its own Redis"
  # pxh-redis is on the project network, where it reaches the new Redis by container name.
  docker exec "$REDIS_CONTAINER" redis-cli -n "$db" --scan \
    | xargs -r -d '\n' -n 200 docker exec "$REDIS_CONTAINER" redis-cli -n "$db" MIGRATE "${target#/}" 6379 "" 0 10000 \
        COPY REPLACE AUTH "$(envv REDIS_PASSWORD)" KEYS >/dev/null
  local had now
  had="$(docker exec "$REDIS_CONTAINER" redis-cli -n "$db" DBSIZE)"
  now="$(compose exec -T redis redis-cli DBSIZE)"
  [[ "$had" == "$now" ]] || die "the stack's Redis holds $now keys, $REDIS_CONTAINER DB $db $had"
  local net
  for net in pipeshub sandbox_kv; do
    docker network disconnect -f "${PROJECT}_$net" "$REDIS_CONTAINER" >/dev/null 2>&1 || true
  done
  sed -i 's/^REDIS_DB=.*/REDIS_DB=0/; s/^E2E_OWN_REDIS=.*//; s/^E2E_APP_REDIS_PASSWORD=.*//; s/^E2E_STORE_AUTH_CHECK=.*//' "$ENV_FILE"
  printf 'E2E_OWN_REDIS=1\nE2E_APP_REDIS_PASSWORD=%s\nE2E_STORE_AUTH_CHECK=%s\n' "$(envv REDIS_PASSWORD)" \
    "$([[ "$(envv E2E_MONGO)" == own ]] && echo enforce || echo warn)" >>"$ENV_FILE"
  mv "$WORK/redis-db" "$WORK/redis-db.adopted"
}

# E2E_DOC_STORE=postgres: the migrate job to every head (a stack rebuilt from a newer tree gets the newer revisions, as
# an upgrade does), then PgBouncer. Node waits for the schema it needs (waitForSchema).
migrate_pg() {
  say "migrating PostgreSQL (db.bootstrap_roles, db.migrate)"
  compose run --rm --no-deps pg-migrate >"$WORK/logs/pg-migrate.log" 2>&1 || fail_service pg-migrate "$WORK/logs/pg-migrate.log"
  compose up -d --no-deps pgbouncer >>"$WORK/logs/compose-up.log" 2>&1 \
    || { tail -20 "$WORK/logs/compose-up.log" >&2; fail_service pgbouncer; }
  wait_compose_healthy pgbouncer 60
}

# The app container's processes, in the order app_from_source.sh starts them.
wait_app() {
  wait_host_url node "http://127.0.0.1:$(envv E2E_APP_PORT)/api/v1/health" 180 "$WORK/logs/node.log"
  wait_app_process connectors http://localhost:8088/health 300
  wait_app_process query http://localhost:8000/health 300
  # The gateway runs with the harness switched on (app_from_source.sh, ADR-OPS-06).
  [[ "$(envv E2E_HARNESS)" == off ]] || wait_app_process model-gateway http://localhost:8096/healthz 120
  [[ "$(envv E2E_INDEXING)" == "1" ]] && wait_app_process indexing http://localhost:8091/health 300
  [[ "$(envv E2E_HUMAN)" == "1" ]] && wait_embedding_model 1800
  return 0
}

# The stack's stores, one `<kind> <service>` line each: a `job` runs once and must exit 0 before any store starts (a
# store's `service_completed_successfully` dependency: etcd waits for etcd-tls's certificates), a `store` is a
# long-running service run.sh waits healthy. R46-LANE: etcd alone, without its job, failed ST-4 ("etcd is missing
# dependency etcd-tls"); tests/cohesion/test_r46_lane_scripts.py holds every profile's list to the Compose file.
stack_stores() {
  local profiles=",$(envv COMPOSE_PROFILES),"
  case "$profiles" in *,graph-arango,*) echo "store arango" ;; *) echo "store neo4j" ;; esac
  case "$profiles" in *,broker-kafka,*) echo "store kafka-1" ;; esac
  case "$profiles" in *,kv-etcd,*) echo "job etcd-tls"; echo "store etcd" ;; esac
  case "$profiles" in *,mongo-standalone,*) echo "store pxh-mongo-standalone" ;; esac
  [[ "$(envv E2E_MONGO)" == "own" ]] && echo "store mongodb"
  # The only socket holder (R38IDENT-FU-1): the broker reaches Docker through it, the app too on a matrix stack.
  echo "store docker-socket-proxy"
  [[ "$(envv E2E_OWN_REDIS)" == "1" ]] && echo "store redis"
  [[ "$(envv E2E_DOC_STORE)" == "postgres" ]] && echo "store postgres"
  return 0
}

# A one-shot job of the stack, started and waited for until it exits; any status but 0 fails the stack.
run_stack_job() {
  local job="$1" timeout="$2" cid code
  compose start "$job" >>"$WORK/logs/compose-up.log" 2>&1 || fail_service "$job"
  cid="$(compose ps -aq "$job")"
  code="$(timeout "$timeout" docker wait "$cid" 2>/dev/null)" || fail_service "$job"
  [[ "$code" == "0" ]] || fail_service "$job"
}

up_stack() {
  local started=$SECONDS
  local stores=() jobs=() store kind name
  while read -r kind name; do
    case "$kind" in job) jobs+=("$name") ;; store) stores+=("$name") ;; esac
  done < <(stack_stores)
  assign_subnets || die "could not choose the stack's network subnets"
  local extra=()
  [[ "$(envv E2E_DOC_STORE)" == "postgres" ]] && extra+=(pgbouncer)
  [[ "$(envv E2E_AIRGAP)" == "1" ]] && extra+=(pxh-ingress)
  compose up --no-start --no-deps "${jobs[@]}" "${stores[@]}" "${extra[@]}" qdrant pxh-fake-llm egress-proxy sandbox-broker pipeshub-ai >"$WORK/logs/compose-up.log" 2>&1 \
    || { cat "$WORK/logs/compose-up.log" >&2; die "compose could not create the stack"; }
  own_blob_volume
  # The shared Mongo and Redis under the names the Compose services use.
  [[ "$(envv E2E_MONGO)" == "replset" ]] && docker network connect --alias mongodb "${PROJECT}_pipeshub" "$MONGO_CONTAINER"
  if [[ "$(envv E2E_OWN_REDIS)" != "1" ]]; then
    docker network connect --alias redis "${PROJECT}_pipeshub" "$REDIS_CONTAINER"
    docker network connect --alias redis "${PROJECT}_sandbox_kv" "$REDIS_CONTAINER"
  fi

  for name in "${jobs[@]}"; do
    run_stack_job "$name" 180
  done
  compose start "${stores[@]}" qdrant pxh-fake-llm >>"$WORK/logs/compose-up.log" 2>&1 \
    || { tail -5 "$WORK/logs/compose-up.log" >&2; die "compose could not start ${stores[*]}, qdrant, pxh-fake-llm"; }
  for svc in "${stores[@]}" qdrant pxh-fake-llm; do
    [[ "${E2E_FAULT:-}" == "$svc" ]] && compose stop "$svc" >/dev/null 2>&1
  done
  wait_compose_healthy pxh-fake-llm 60
  if [[ "$(envv E2E_DURABLE)" == "1" ]]; then
    compose up -d --no-deps pxh-fake-saas >>"$WORK/logs/compose-up.log" 2>&1 \
      || { tail -20 "$WORK/logs/compose-up.log" >&2; fail_service pxh-fake-saas; }
    wait_compose_healthy pxh-fake-saas 60
  fi
  if [[ "$(envv E2E_MCP_HUB)" == "1" ]]; then
    compose up -d --no-deps pxh-fake-mcp >>"$WORK/logs/compose-up.log" 2>&1 \
      || { tail -20 "$WORK/logs/compose-up.log" >&2; fail_service pxh-fake-mcp; }
    wait_compose_healthy pxh-fake-mcp 60
  fi
  if [[ "$(envv E2E_FAKE_GIT)" == "1" ]]; then
    mkdir -p "$(envv E2E_FAKE_GIT_DIR)"
    compose up -d --no-deps pxh-fake-git >>"$WORK/logs/compose-up.log" 2>&1 \
      || { tail -20 "$WORK/logs/compose-up.log" >&2; fail_service pxh-fake-git; }
    wait_compose_healthy pxh-fake-git 60
  fi
  wait_compose_healthy qdrant 120
  for store in "${stores[@]}"; do
    wait_compose_healthy "$store" 240
  done
  [[ "$(envv E2E_DOC_STORE)" == "postgres" ]] && migrate_pg
  if [[ "$(envv E2E_AIRGAP)" == "1" ]]; then
    compose start pxh-ingress >>"$WORK/logs/compose-up.log" 2>&1 \
      || { tail -20 "$WORK/logs/compose-up.log" >&2; fail_service pxh-ingress; }
    wait_compose_healthy pxh-ingress 60
  fi

  compose start pipeshub-ai >>"$WORK/logs/compose-up.log" 2>&1 \
    || { tail -20 "$WORK/logs/compose-up.log" >&2; fail_service pipeshub-ai; }
  wait_app

  # Node has written the worker credential and /services/endpoints the plane reads.
  compose start egress-proxy sandbox-broker >>"$WORK/logs/compose-up.log" 2>&1 \
    || { tail -20 "$WORK/logs/compose-up.log" >&2; die "compose could not start egress-proxy, sandbox-broker"; }
  for svc in egress-proxy sandbox-broker; do
    [[ "${E2E_FAULT:-}" == "$svc" ]] && compose stop "$svc" >/dev/null 2>&1
  done
  wait_compose_healthy egress-proxy 120
  wait_compose_healthy sandbox-broker 120
  [[ "$(envv E2E_DURABLE)" == "1" ]] && wait_worker 240
  wait_capabilities 120

  # The UI from Node's public/ (E2E_FAULT=ui leaves it out).
  wait_host_url ui "http://127.0.0.1:$(envv E2E_APP_PORT)/login/" 30 "$WORK/logs/node.log"
  if [[ "$(envv E2E_AIRGAP)" == "1" ]]; then
    say "air gap: no network of $PROJECT routes out (C15.04)"
    python3 "$HERE/airgap_check.py" "$PROJECT" | tee "$WORK/logs/airgap.log"
    [[ "${PIPESTATUS[0]}" == 0 ]] || { touch "$WORK/failed"; die "the stack has a route out"; }
  fi
  echo $(( SECONDS - started )) >"$WORK/up-seconds"
  say "stack up in $(cat "$WORK/up-seconds") s at http://127.0.0.1:$(envv E2E_APP_PORT)"
}

seed() {
  E2E_API="http://127.0.0.1:$(envv E2E_APP_PORT)" \
  E2E_ADMIN_EMAIL="$(envv E2E_ADMIN_EMAIL)" E2E_ADMIN_PASSWORD="$(envv E2E_ADMIN_PASSWORD)" \
  E2E_LLM_KEY="$(envv E2E_LLM_KEY)" E2E_STATE="$SEED_STATE" E2E_MONGO="$(envv E2E_MONGO)" \
  E2E_ENGINE="$(envv E2E_ENGINE)" \
  E2E_SEED_EMBEDDING="$([[ "$(envv E2E_HUMAN)" == "1" ]] && echo 0 || echo 1)" \
    python3 "$HERE/seed.py" "$@"
}

# --- specs ------------------------------------------------------------------------

# Playwright's own image: a host without Chromium's system libraries or root cannot run the host Chromium.
# The sign-in state goes to the run's directory, never over a developer's frontend/.auth.
playwright() {
  local project_args=("$@")
  mkdir -p "$WORK/pw/auth"
  if [[ ! -d "$ROOT/frontend/.auth" ]]; then
    mkdir "$ROOT/frontend/.auth" && touch "$WORK/made-auth-dir"
  fi
  # A worktree links node_modules to another checkout; mount the target where the link points. A link to a link (the
  # gate's worktree links to the candidate's worktree, which links to a checkout) is followed hop by hop: the
  # container resolves each one, so each path on the way holds the target.
  local modules=() hop="$ROOT/frontend/node_modules" target next
  if [[ -L "$hop" ]]; then
    target="$(readlink -f "$hop")"
    while [[ -L "$hop" ]]; do
      next="$(readlink "$hop")"
      [[ "$next" == /* ]] || next="$(dirname "$hop")/$next"
      modules+=(-v "$target:$next:ro")
      hop="$next"
    done
  fi
  # QA-AUTO F4: in the stack's network namespace, not the host's, where Chromium reads each Docker network another
  # lane adds or removes as an address change and fails requests in flight (net::ERR_NETWORK_CHANGED). pw_forward.js
  # serves the stack's host ports on the container's loopback, so every URL stays as it was. E2E_PW_NETWORK=host
  # runs on the host's network as before.
  local network=(--network host) launch=(npx playwright test)
  if [[ "${E2E_PW_NETWORK:-stack}" != "host" ]] && docker network inspect "${PROJECT}_pipeshub" >/dev/null 2>&1; then
    local app_port llm_port saas_port
    app_port="$(envv E2E_APP_PORT)" llm_port="$(envv E2E_LLM_PORT)" saas_port="$(envv E2E_SAAS_PORT)"
    network=(--network "${PROJECT}_pipeshub" -v "$HERE/pw_forward.js:/work/pw_forward.js:ro")
    launch=(bash -c 'node /work/pw_forward.js "$1=pipeshub-ai:3000" "$2=pxh-fake-llm:8080" "$3=pxh-fake-saas:8090" &
      for _ in $(seq 1 100); do (: >"/dev/tcp/127.0.0.1/$1") 2>/dev/null && break; sleep 0.1; done
      shift 3; exec npx playwright test "$@"' pw "$app_port" "$llm_port" "$saas_port")
  fi
  docker run --rm --name "${PROJECT}-pw" "${network[@]}" --ipc host \
    "${modules[@]}" \
    --label "$OWNER_LABEL" --label "$RUN_LABEL_KEY=$RUN" --label "io.pipeshub.e2e.role=playwright" \
    --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -v "$ROOT/frontend:/work/frontend" -w /work/frontend -v "$WORK/pw/auth:/work/frontend/.auth" \
    -v "$ROOT/contracts:/work/contracts:ro" \
    -v "$ROOT/backend/nodejs/apps/tests/contracts/golden:/work/backend/nodejs/apps/tests/contracts/golden:ro" \
    -e PLAYWRIGHT_NO_SERVER=1 -e CI="${CI:-}" \
    -e BASE_URL="http://127.0.0.1:$(envv E2E_APP_PORT)" \
    -e NEXT_PUBLIC_API_BASE_URL="http://127.0.0.1:$(envv E2E_APP_PORT)" \
    -e TEST_USER_EMAIL="$(envv E2E_ADMIN_EMAIL)" -e TEST_USER_PASSWORD="$(envv E2E_ADMIN_PASSWORD)" \
    -e E2E_LLM_URL="http://127.0.0.1:$(envv E2E_LLM_PORT)" -e E2E_LLM_CONTROL="$(envv E2E_LLM_CONTROL)" \
    -e E2E_AGENT_KEY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["agentKey"])' "$SEED_STATE")" \
    -e E2E_ENGINE_AGENT_KEY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("engineAgentKey",""))' "$SEED_STATE")" \
    -e E2E_TOOL_AGENT_KEY="$(python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print(s.get("toolAgentKey") or s.get("agentLoop",""))' "$SEED_STATE")" \
    -e E2E_SAAS_URL="http://127.0.0.1:$(envv E2E_SAAS_PORT)" -e E2E_CONTROL_DIR=/work/control \
    -e E2E_HARNESS_LANE=1 -e UI_CASE_SHOTS="${E2E_UI_CASE_SHOTS:-}" -e E2E_CODING_ENGINE="$(envv E2E_ENGINE)" \
    -e PXH_UX_BASELINES="${PXH_UX_BASELINES:-}" -e PXH_QA_RESULTS="${PXH_QA_RESULTS:-}" \
    -e E2E_DISPOSABLE_STACK="$([[ "$(envv E2E_HUMAN)" == "1" ]] && echo 0 || echo 1)" \
    -e E2E_LLM_MODEL_KEY="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("llmKey",""))' "$SEED_STATE")" \
    -v "$WORK/control:/work/control" \
    -e PLAYWRIGHT_HTML_REPORT="/work/pw/report-${E2E_PW_TAG:-flag-on}" -e PLAYWRIGHT_JUNIT_OUTPUT_NAME="/work/pw/junit-${E2E_PW_TAG:-flag-on}.xml" \
    -e PLAYWRIGHT_JSON_OUTPUT_NAME="/work/pw/results-${E2E_PW_TAG:-flag-on}.json" \
    -v "$WORK/pw:/work/pw" \
    "$PW_IMAGE" "${launch[@]}" --output "/work/pw/results-${E2E_PW_TAG:-flag-on}" "${project_args[@]}"
}

fake_llm_log() {
  curl -sf -H "X-E2E-Control: $(envv E2E_LLM_CONTROL)" "http://127.0.0.1:$(envv E2E_LLM_PORT)/__control/requests${2:-}" \
    >"$WORK/logs/fake-llm-$1.json" || echo '{"requests": []}' >"$WORK/logs/fake-llm-$1.json"
}

# A real-service lane that skips reads green while testing nothing (test plan 00 Q10): a skip, or no test, fails it.
# `check_junit TAG listed` (the authenticated project, WP-71) lets a spec skip itself for a stated reason about the
# stack (no SMTP, no indexing, an enterprise build), and lists each skip with its reason; a failure still fails it.
check_junit() {
  python3 - "$WORK/pw/junit-$1.xml" "${2:-none}" <<'PY'
import sys
import xml.etree.ElementTree as ET
try:
    root = ET.parse(sys.argv[1]).getroot()
except (OSError, ET.ParseError) as error:
    sys.exit(f"E2E: no JUnit report ({error})")
suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
tests = skipped = 0
for suite in suites:
    tests += int(suite.get("tests", 0))
    skipped += int(suite.get("skipped", 0))
name = sys.argv[1].rsplit('/', 1)[-1]
# Playwright's JUnit report keeps a skip's reason as the test's `skip` annotation, a property.
def reason(case):
    return " ".join((p.get("value") or "").strip() for p in case.iter("property") if p.get("name") in ("skip", "fixme")).strip()
skips = [(c.get("name"), reason(c)) for c in root.iter("testcase") if c.find("skipped") is not None]
if tests == 0 or (skipped and sys.argv[2] != "listed"):
    sys.exit(f"E2E: {tests} tests, {skipped} skipped on a real-service lane: {[n for n, _ in skips]}")
unstated = [n for n, why in skips if not why]
for test, why in skips:
    print(f"E2E: skipped: {test}: {why or 'NO REASON GIVEN'}")
if unstated:
    sys.exit(f"E2E: {len(unstated)} skip(s) give no reason: {unstated}")
print(f"E2E: JUnit {name}: {tests} tests, {skipped} skipped" + (" for the reasons above" if skipped else ""))
PY
}

run_durable_specs() {
  local status=0
  say "specs: a durable engine run on the real path (E2E2 DE-1..DE-6)"
  E2E_PW_TAG=durable playwright --project harness-engine --workers 1 --reporter list,junit 2>&1 \
    | tee "$WORK/logs/specs-durable.log" || status=1
  check_junit durable || status=1
  fake_llm_log durable '?all=1'
  curl -sf -H "X-E2E-Control: $(envv E2E_LLM_CONTROL)" "http://127.0.0.1:$(envv E2E_SAAS_PORT)/__control/effects" \
    >"$WORK/logs/fake-saas.json" || echo '{"effects": []}' >"$WORK/logs/fake-saas.json"
  check_egress_durable || status=1
  return $status
}

# One worker: the specs share one org, its approval policy and its Labs flags, so they run one at a time.
run_harness_specs() {
  local status=0
  say "specs: the harness project (QA-5 Es-PR)"
  E2E_PW_TAG=harness playwright --project harness --workers 1 --reporter list,junit "$@" 2>&1 \
    | tee "$WORK/logs/specs-harness.log" || status=1
  check_junit harness || status=1
  fake_llm_log harness '?all=1'
  curl -sf -H "X-E2E-Control: $(envv E2E_LLM_CONTROL)" "http://127.0.0.1:$(envv E2E_SAAS_PORT)/__control/effects" \
    >"$WORK/logs/fake-saas.json" || echo '{"effects": []}' >"$WORK/logs/fake-saas.json"
  return $status
}

# QAFINAL-FU-ERASE: after the durable specs (its own conversation, so their checks are untouched), on the default stores.
run_erase_world() {
  say "erasure: erased content never comes back in search or an answer (erase_world.py)"
  python3 "$HERE/erase_world.py" "$WORK" | tee "$WORK/logs/erase-world.log"
  return "${PIPESTATUS[0]}"
}

# The whole flow on one stack (`pg`, `airgap`): a durable engine run, then the harness project's chat-loop turns, asks,
# approvals and stops. The durable specs go first: they delete their conversations, which pg_world.py checks erased.
run_flow_specs() {
  local status=0
  run_durable_specs || status=1
  run_harness_specs || status=1
  return $status
}

# WP-71: the authenticated project after the harness one, on the same stack (the specs share its org, so the harness
# project still runs on one worker). E2E_PW_WORKERS caps the authenticated project's workers (default 2: a laptop).
run_browser_specs() {
  local status=0
  run_harness_specs || status=1
  say "specs: the authenticated project (WP-71), ${E2E_PW_WORKERS:-2} workers"
  E2E_PW_TAG=authenticated playwright --project authenticated --workers "${E2E_PW_WORKERS:-2}" --reporter list,junit 2>&1 \
    | tee "$WORK/logs/specs-authenticated.log" || status=1
  check_junit authenticated listed || status=1
  return $status
}

check_egress_durable() {
  say "egress (durable): the proxy, the worker's tool gateway, the model gateway, the scripted model and SaaS"
  compose logs --no-color egress-proxy >"$WORK/logs/egress-proxy.log" 2>&1 || true
  compose logs --no-color sandbox-broker >"$WORK/logs/sandbox-broker.log" 2>&1 || true
  compose logs --no-color pxh-fake-saas >"$WORK/logs/fake-saas.log" 2>&1 || true
  local proxy_ip app_ip org left status=0
  proxy_ip="$(docker inspect -f "{{(index .NetworkSettings.Networks \"${PROJECT}_sandbox_plane\").IPAddress}}" "$(compose ps -q egress-proxy)")"
  app_ip="$(docker inspect -f "{{(index .NetworkSettings.Networks \"${PROJECT}_pipeshub\").IPAddress}}" "$(compose ps -q pipeshub-ai)")"
  python3 "$HERE/check_egress.py" --durable "$WORK/logs" "$proxy_ip" "$app_ip" || status=1
  org="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["orgId"])' "$SEED_STATE")"
  left="$(docker ps -aq --filter "label=$BROKER_MANAGED" --filter "label=orgId=$org")"
  if [[ -n "$left" ]]; then
    echo "  FAIL EG-D5 sandbox containers of the run's org are left: $left"
    status=1
  else
    echo "  ok   EG-D5 no sandbox container of the run's org is left"
  fi
  # DE-7: the spec deleted its conversations; erasure (E7 sandbox_folders) removes their folders on the broker's
  # volume, which durable runs keep by design (E1), under this install's id (the Compose project).
  local erased="$WORK/control/erased-sessions" kept="" start=$SECONDS
  if [[ -s "$erased" ]]; then
    while (( SECONDS - start < 120 )); do
      kept="$(data_volume sh -c "for s in $(tr '\n' ' ' <"$erased"); do [ -e /d/$PROJECT/$org/\$s ] && echo \$s; done; true")"
      [[ -z "$kept" ]] && break
      sleep 3
    done
    if [[ -n "$kept" ]]; then
      echo "  FAIL DE-7 erased conversations still have folders on $BROKER_DATA_VOLUME: $kept"
      status=1
    else
      echo "  ok   DE-7 every erased conversation's folder left $BROKER_DATA_VOLUME ($(grep -c . "$erased") sessions)"
    fi
  else
    echo "  FAIL DE-7 the spec recorded no erased conversations"
    status=1
  fi
  return $status
}

# A one-shot helper on the broker's global data volume, labelled as this run's (read-only unless asked).
data_volume() {
  local mode=ro
  [[ "${DATA_VOLUME_RW:-}" == "1" ]] && mode=rw
  docker run --rm --label "$OWNER_LABEL" --label "$RUN_LABEL_KEY=$RUN" -v "$BROKER_DATA_VOLUME:/d:$mode" python:3.12-slim "$@"
}

# --- commands -----------------------------------------------------------------------

cmd_up() {
  reap_dead_runs
  local run="${E2E_RUN:-$(rand 4)}" started=$SECONDS
  prepare "$run"
  echo $(( SECONDS - started )) >"$WORK/build-seconds"
  say "built Node and the UI in $(cat "$WORK/build-seconds") s"
  up_stack
  say "seeding"
  seed seed >"$WORK/logs/seed.log" 2>&1 || { cat "$WORK/logs/seed.log" >&2; touch "$WORK/failed"; die "seeding failed"; }
  if [[ "$(envv E2E_DURABLE)" == "1" ]]; then
    seed_durable >>"$WORK/logs/seed.log" 2>&1 || { cat "$WORK/logs/seed.log" >&2; touch "$WORK/failed"; die "seeding the durable lane failed"; }
  fi
  if [[ "${E2E_LANE:-}" == "harness" || "${E2E_LANE:-}" == "matrix" ]]; then
    seed harness >>"$WORK/logs/seed.log" 2>&1 || { cat "$WORK/logs/seed.log" >&2; touch "$WORK/failed"; die "seeding the harness lane failed"; }
  fi
  if [[ "$(envv E2E_HUMAN)" == "1" ]]; then
    seed_human >>"$WORK/logs/seed.log" 2>&1 || { cat "$WORK/logs/seed.log" >&2; touch "$WORK/failed"; die "seeding the human stack failed"; }
  fi
  echo $(( SECONDS - started )) >"$WORK/ready-seconds"
}

seed_human() {
  E2E_API="http://127.0.0.1:$(envv E2E_APP_PORT)" \
  E2E_ADMIN_EMAIL="$(envv E2E_ADMIN_EMAIL)" E2E_ADMIN_PASSWORD="$(envv E2E_ADMIN_PASSWORD)" \
  E2E_LLM_KEY="$(envv E2E_LLM_KEY)" E2E_STATE="$SEED_STATE" E2E_MEMBER_FILE="$WORK/member.json" \
    python3 "$HERE/seed_human.py" || return 1
  local instance; instance="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mcpInstanceId"])' "$SEED_STATE")"
  docker exec -e E2E_KEEP_CLASS_RULES="${E2E_KEEP_CLASS_RULES:-}" "$(compose ps -q pipeshub-ai)" \
    python /e2e/pyrun.py /e2e/seed_config.py human "$instance"
}

# SEC-D-06 for a stack written before every store took a credential (as install.sh --upgrade heals an .env): Redis and
# Kafka read theirs from the environment at each start, so generating one keeps every stored value readable.
heal_store_credentials() {
  # REDIS_PASSWORD and KAFKA_PASSWORD come from backfill_env (both stores take them afresh at each start). A stack on
  # the shared pxh-mongo-rs wrote MONGO_PASSWORD empty: its own mongodb service never starts, but Compose needs a value.
  # An own MongoDB keeps its password on its volume, so that one is never replaced.
  if [[ "$(envv E2E_MONGO)" != own && -z "$(envv MONGO_PASSWORD)" ]]; then set_envv MONGO_PASSWORD "$(rand 16)"; fi
  if [[ "$(envv E2E_OWN_REDIS)" == "1" ]]; then
    set_envv E2E_APP_REDIS_PASSWORD "$(envv REDIS_PASSWORD)"
    set_envv E2E_STORE_AUTH_CHECK "$([[ "$(envv E2E_MONGO)" == own ]] && echo enforce || echo warn)"
  else
    set_envv E2E_APP_REDIS_PASSWORD ""
    set_envv E2E_STORE_AUTH_CHECK warn
  fi
}

# The human-test stack: rebuild Node and the UI from the tree as it is now (scripts/human-test-stack.sh refresh has moved it to a newer
# commit) beside the running build, then recreate the stack's services on it. Data (the project's volumes, its Mongo
# database and Redis DB) stays; the seed runs again and adds only what is missing.
rebuild() {
  run_vars "$1"
  [[ -f "$ENV_FILE" ]] || die "no stack $RUN in $STATE_DIR"
  backfill_env
  heal_store_credentials
  # The caller's choice for the sign-in add-on, its keep times and the public URL, when it made one; otherwise the
  # stack keeps its own.
  local name
  for name in E2E_ENGINE_SIGNIN E2E_SIGNIN_IDLE_MINUTES E2E_SIGNIN_MAX_HOURS E2E_PUBLIC_URL; do
    if [[ -n "${!name+set}" ]]; then set_envv "$name" "${!name}"; fi
  done
  if [[ -n "${E2E_PUBLIC_URL+set}" ]]; then write_public_url; fi
  local next="$WORK/node-dist.next" svc
  local services=(qdrant pxh-fake-llm pxh-fake-saas docker-socket-proxy egress-proxy sandbox-broker)
  restack_runtime
  stack_trust_root
  rm -rf "$next" && mkdir -p "$next"
  ensure_app_image
  build_node_ui "$next"
  local old_app; old_app="$(compose ps -aq pipeshub-ai)"
  # The upgrade's downtime (R30-LAT, SLO.md upgrade_ready_ms): from the old app stopped to the new one serving.
  local down_at=$SECONDS
  compose stop pipeshub-ai >/dev/null 2>&1 || true
  own_blob_volume "$old_app"
  [[ "$(envv E2E_HUMAN)" == "1" && "$(envv E2E_OWN_REDIS)" != "1" ]] && adopt_own_redis
  rm -rf "$WORK/node-dist.old" && mv "$WORK/node-dist" "$WORK/node-dist.old" && mv "$next" "$WORK/node-dist"
  rm -rf "$WORK/node-dist.old"
  # This build's logs only: the waits below read them.
  rm -rf "$WORK/logs/before-rebuild" && mkdir -p "$WORK/logs/before-rebuild"
  find "$WORK/logs" -maxdepth 1 -type f \( -name '*.log' -o -name '*.exited' -o -name '*.pid' \) \
    -exec mv {} "$WORK/logs/before-rebuild/" \;
  case ",$(envv COMPOSE_PROFILES)," in *,graph-arango,*) services+=(arango) ;; *) services+=(neo4j) ;; esac
  [[ "$(envv E2E_MCP_HUB)" == "1" ]] && services+=(pxh-fake-mcp)
  [[ "$(envv E2E_OWN_REDIS)" == "1" ]] && services+=(redis)
  [[ "$(envv E2E_DOC_STORE)" == "postgres" ]] && services+=(postgres)
  # A network this commit added (R43-SOCK's sandbox_docker) gets its subnet from the stack's range, as at creation.
  assign_subnets --new || die "could not choose the stack's network subnets"
  # A service whose configuration this commit changed is recreated; its volumes stay.
  compose up -d --no-deps "${services[@]}" >"$WORK/logs/compose-up.log" 2>&1 \
    || { cat "$WORK/logs/compose-up.log" >&2; die "compose could not update the stack"; }
  # The stack's own Redis takes REDIS_PASSWORD only at start (SEC-D-06): recreated while the app is stopped, it runs
  # the env file's password whatever it ran before, and its healthcheck (which signs in) proves it below.
  if [[ "$(envv E2E_OWN_REDIS)" == "1" ]]; then
    compose up -d --no-deps --force-recreate redis >>"$WORK/logs/compose-up.log" 2>&1 \
      || { cat "$WORK/logs/compose-up.log" >&2; die "compose could not recreate the stack's Redis"; }
  fi
  # Every service that runs this tree's code from a bind mount is recreated (code_services.py): `up` keeps a container
  # whose configuration did not change, so it would go on running the old code (CTL-6 the scripted model, HTIMG the
  # broker's engine allowlist). The app is recreated last, below.
  local listed code=()
  listed="$(compose config --format json | python3 "$HERE/code_services.py" "$ROOT")" \
    || die "could not list the services that run this tree's code"
  mapfile -t code < <(grep -vx pipeshub-ai <<<"$listed")
  compose up -d --no-deps --force-recreate "${code[@]}" >>"$WORK/logs/compose-up.log" 2>&1 \
    || { cat "$WORK/logs/compose-up.log" >&2; die "compose could not recreate the services that run this tree's code"; }
  # The egress proxy and broker log in to Redis as the ACL users the app creates at start (ADR-KEY-02), so on a stack
  # made before those users existed they turn healthy only after the app runs; they are waited for after it.
  for svc in "${services[@]}"; do
    case "$svc" in egress-proxy|sandbox-broker) continue ;; esac
    wait_compose_healthy "$svc" 240
  done
  [[ "$(envv E2E_DOC_STORE)" == "postgres" ]] && migrate_pg
  compose up -d --no-deps --force-recreate pipeshub-ai >>"$WORK/logs/compose-up.log" 2>&1 \
    || { cat "$WORK/logs/compose-up.log" >&2; die "compose could not recreate the app"; }
  wait_app
  wait_compose_healthy egress-proxy 240
  wait_compose_healthy sandbox-broker 240
  [[ "$(envv E2E_DURABLE)" == "1" ]] && wait_worker 240
  wait_capabilities 120
  wait_host_url ui "http://127.0.0.1:$(envv E2E_APP_PORT)/login/" 30 "$WORK/logs/node.log"
  echo $(( SECONDS - down_at )) >"$WORK/upgrade-seconds"
  say "rebuilt and restarted $PROJECT at http://127.0.0.1:$(envv E2E_APP_PORT) (down $(cat "$WORK/upgrade-seconds") s)"
  seed seed >"$WORK/logs/seed.log" 2>&1 || { cat "$WORK/logs/seed.log" >&2; die "seeding failed"; }
  [[ "$(envv E2E_DURABLE)" == "1" ]] && { seed_durable >>"$WORK/logs/seed.log" 2>&1 || { cat "$WORK/logs/seed.log" >&2; die "seeding failed"; }; }
  if [[ "$(envv E2E_HUMAN)" == "1" ]]; then
    seed_human >>"$WORK/logs/seed.log" 2>&1 || { cat "$WORK/logs/seed.log" >&2; die "seeding the human stack failed"; }
  fi
}

# The durable lane's seed: through Node's API, then the approvals document through ConfigurationService in the
# app container (the operator's document: FLAGS.md).
seed_durable() {
  seed durable || return 1
  # A person's stack writes its own rule after seed_human.py (seed_config.py human).
  [[ "$(envv E2E_HUMAN)" == "1" ]] && return 0
  local org; org="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["orgId"])' "$SEED_STATE")"
  docker exec "$(compose ps -q pipeshub-ai)" python /e2e/pyrun.py /e2e/seed_config.py approvals "$org"
}

# KEYRING-FU-1: the SECRET_KEY rotation of guides/runbooks.md ("Rotating SECRET_KEY") on a run's stack. The data
# steps are the runbook's own command in the app container; the key steps edit the env file as the runbook edits
# `.env` and bring the stack up on it. Keys go only to the env file (600); nothing here prints one.
rotation_step() {
  local step="$1" db=' --mongo-uri "$MONGO_URI" --db "$MONGO_DB_NAME"'
  [[ "$(envv E2E_DOC_STORE)" == "postgres" ]] && db=''
  say "rotate-secret-key: $step"
  # The runbook's line: `docker compose exec -w /app/backend pipeshub-ai sh -c 'node dist/... <step> --mongo-uri ...'`.
  if ! compose exec -T -w /app/backend pipeshub-ai \
    sh -c "node dist/modules/agent-harness/ops/rotate-secret-key-cli.js $step$db" | tee -a "$WORK/logs/rotate-secret-key.log"; then
    die "rotate-secret-key $step failed (its lines are above and in $WORK/logs/rotate-secret-key.log)"
  fi
}

# The stack up again on its env file after a key change: what reads a key is recreated (the app last, as `rebuild`
# does), and the waits read the new start's logs only.
restart_on_keys() {
  local kept="$WORK/logs/before-$1" listed code=() svc
  rm -rf "$kept" && mkdir -p "$kept"
  find "$WORK/logs" -maxdepth 1 -type f \( -name '*.log' -o -name '*.exited' -o -name '*.pid' \) \
    ! -name rotate-secret-key.log -exec mv {} "$kept/" \;
  listed="$(compose config --format json | python3 "$HERE/code_services.py" "$ROOT")" \
    || die "could not list the services that run this tree's code"
  mapfile -t code < <(grep -vx pipeshub-ai <<<"$listed")
  compose up -d --no-deps "${code[@]}" >>"$WORK/logs/compose-up.log" 2>&1 \
    || { cat "$WORK/logs/compose-up.log" >&2; die "compose could not bring the stack up on its new keys"; }
  for svc in egress-proxy sandbox-broker; do wait_compose_healthy "$svc" 240; done
  compose up -d --no-deps pipeshub-ai >>"$WORK/logs/compose-up.log" 2>&1 \
    || { cat "$WORK/logs/compose-up.log" >&2; die "compose could not bring the app up on its new keys"; }
  wait_app
  [[ "$(envv E2E_DURABLE)" == "1" ]] && wait_worker 240
  wait_capabilities 120
  return 0
}

key_step() {
  case "$1" in
    introduce)
      [[ -z "$(envv SECRET_KEY_PREVIOUS || true)" && -z "$(envv SECRET_KEY_NEXT || true)" ]] \
        || die "a rotation is already under way"
      set_envv SECRET_KEY_NEXT "$(rand 32)"
      ;;
    switch)
      local old new
      old="$(envv SECRET_KEY || true)"
      new="$(envv SECRET_KEY_NEXT || true)"
      [[ -n "$old" && -n "$new" ]] || die "switch needs SECRET_KEY and SECRET_KEY_NEXT: run introduce first"
      set_envv SECRET_KEY "$new"
      set_envv SECRET_KEY_PREVIOUS "$old"
      set_envv SECRET_KEY_NEXT ""
      ;;
    drop-previous)
      [[ -n "$(envv SECRET_KEY_PREVIOUS || true)" ]] || die "there is no SECRET_KEY_PREVIOUS to drop"
      set_envv SECRET_KEY_PREVIOUS ""
      ;;
  esac
  say "rotate-secret-key: $1 (the stack comes up on the env file's keys)"
  restart_on_keys "$1"
}

# `run.sh rotate-secret-key RUN STEP...`: the steps in order; the data steps alone by default.
rotate_secret_key() {
  run_vars "$1"
  [[ -f "$ENV_FILE" ]] || die "no stack $RUN in $STATE_DIR"
  shift
  local step steps=("$@")
  [[ ${#steps[@]} -gt 0 ]] || steps=(dry-run rotate verify retire)
  for step in "${steps[@]}"; do
    case "$step" in
      introduce|switch|drop-previous) key_step "$step" ;;
      dry-run|rotate|verify|retire) rotation_step "$step" ;;
      *) die "rotate-secret-key: unknown step $step (introduce, switch, dry-run, rotate, verify, retire, drop-previous)" ;;
    esac
  done
}

# The whole runbook, then a scripted turn on the new key alone (the model's sealed key, the proxy's state and the
# org data keys all read under it), as `run.sh keyring` proves it on a fresh stack.
ROTATION_RUNBOOK=(introduce switch dry-run rotate verify retire drop-previous verify)

case "${1:-harness}" in
  specs)
    run_vars "${2:?run id}"
    shift 2
    run_harness_specs "$@"
    ;;
  pw)
    run_vars "${2:?run id}"
    shift 2
    E2E_PW_TAG="${E2E_PW_TAG:-adhoc}" playwright "$@"
    ;;
  down)
    down_run "${2:?run id}"
    ;;
  clean)
    reap_dead_runs
    find "$STATE_DIR" -maxdepth 1 \( -name 'ui-*' -o -name 'kept-*' \) -type d -exec rm -rf {} + 2>/dev/null || true
    ;;
  durable)
    # E2E2: the same stack plus the durable worker running engine runs; only the durable specs. Indexing runs too:
    # the answer contract's found and near-miss cases search records the specs upload (ANSWER-CONTRACT-FU-1).
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=true E2E_INDEXING="${E2E_INDEXING:-1}"
    started=$SECONDS
    trap 'status=$?; if [[ -n "${WORK:-}" ]]; then [[ $status -ne 0 ]] && touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    cmd_up
    status=0
    run_durable_specs || status=1
    run_erase_world || status=1
    [[ $status == 0 ]] || { touch "$WORK/failed"; say "FAILED (stack up in $(cat "$WORK/up-seconds") s, total $(( SECONDS - started )) s)"; exit 1; }
    say "passed (stack up in $(cat "$WORK/up-seconds") s, seeded at $(cat "$WORK/ready-seconds") s, total $(( SECONDS - started )) s)"
    ;;
  erase-world)
    run_vars "${2:?run id}"
    run_erase_world
    ;;
  seckey)
    run_vars "${2:?run id}"
    say "R31-SECKEY: no credential in an engine sandbox, no direct upstream, tokens only to the run's grants (seckey.py)"
    # seckey.py reads harness_core's canary forms and token shapes: the venv the stack's packages come from, on the host
    # (as PYTHONPATH that site-packages would shadow the standard library with a stray Python 2 uuid.py).
    VENV_PY="$(cd "$E2E_SITE_PACKAGES/../../.." && pwd)/bin/python"
    [[ -x "$VENV_PY" ]] || die "seckey: no Python beside E2E_SITE_PACKAGES ($E2E_SITE_PACKAGES)"
    "$VENV_PY" "$HERE/seckey.py" "$WORK" --engines "${E2E_SECKEY_ENGINES:-}" \
      | tee "$WORK/logs/seckey.log"
    exit "${PIPESTATUS[0]}"
    ;;
  durable-specs)
    run_vars "${2:?run id}"
    run_durable_specs
    ;;
  pg|airgap)
    # WP114-FU-E2E (pg): the document store on PostgreSQL. C15.04 (airgap): no network of the stack routes out.
    if [[ "$1" == pg ]]; then export E2E_DOC_STORE=postgres; else export E2E_AIRGAP=1; fi
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=true E2E_LANE=harness
    started=$SECONDS
    trap 'status=$?; if [[ -n "${WORK:-}" ]]; then [[ $status -ne 0 ]] && touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    trap 'exit 143' TERM INT
    cmd_up
    status=0
    run_flow_specs || status=1
    if [[ "$1" == pg ]]; then
      say "PostgreSQL: the rows, erasure and the org's deletion (pg_world.py)"
      python3 "$HERE/pg_world.py" "$WORK" | tee "$WORK/logs/pg-world.log"
      [[ "${PIPESTATUS[0]}" == 0 ]] || status=1
    fi
    [[ $status == 0 ]] || { touch "$WORK/failed"; say "FAILED (stack up in $(cat "$WORK/up-seconds") s, total $(( SECONDS - started )) s)"; exit 1; }
    say "passed (stack up in $(cat "$WORK/up-seconds") s, seeded at $(cat "$WORK/ready-seconds") s, total $(( SECONDS - started )) s)"
    ;;
  pg-world)
    run_vars "${2:?run id}"
    python3 "$HERE/pg_world.py" "$WORK"
    ;;
  durable-up)
    # QA-CLASSIC: the durable lane's stack left running, so one engine spec at a time runs against it (pw RUN ARGS).
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=true E2E_INDEXING="${E2E_INDEXING:-1}"
    trap 'status=$?; if [[ $status -ne 0 && -n "${WORK:-}" ]]; then touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    cmd_up
    rm -f "$WORK/owner.pid"
    trap - EXIT
    say "left running: run=$RUN (bash $0 durable-specs $RUN | bash $0 pw $RUN --project harness-engine … | bash $0 down $RUN)"
    ;;
  all|harness)
    # QA-5 Es-PR (ST-1): the durable worker for loop runs and the scripted SaaS, no engine runs; the harness project.
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=false E2E_LANE=harness
    started=$SECONDS
    trap 'status=$?; if [[ -n "${WORK:-}" ]]; then [[ $status -ne 0 ]] && touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    cmd_up
    run_harness_specs || { touch "$WORK/failed"; say "FAILED (stack up in $(cat "$WORK/up-seconds") s, total $(( SECONDS - started )) s)"; exit 1; }
    say "passed (stack up in $(cat "$WORK/up-seconds") s, seeded at $(cat "$WORK/ready-seconds") s, total $(( SECONDS - started )) s)"
    ;;
  browser)
    # WP-71: the browser lane of the verification gate (PXH_VERIFY_E2E=1): a stack made for the run, the harness and the
    # authenticated Playwright projects on the scripted model, then torn down, pass or fail.
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=false E2E_LANE=harness
    started=$SECONDS
    trap 'status=$?; if [[ -n "${WORK:-}" ]]; then [[ $status -ne 0 ]] && touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    # A gate's timeout sends TERM: exit through the EXIT trap, so the stack is torn down then too.
    trap 'exit 143' TERM INT
    cmd_up
    run_browser_specs || { touch "$WORK/failed"; say "FAILED (stack up in $(cat "$WORK/up-seconds") s, total $(( SECONDS - started )) s)"; exit 1; }
    say "passed (stack up in $(cat "$WORK/up-seconds") s, seeded at $(cat "$WORK/ready-seconds") s, total $(( SECONDS - started )) s)"
    ;;
  browser-specs)
    run_vars "${2:?run id}"
    run_browser_specs
    ;;
  matrix-up)
    # QA-6: one stack profile (E2E_STACK) for integration-tests/harness/e2e, with the query service supervised so
    # the runner can set a row's process settings, and the store profile's services.
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=false E2E_LANE=matrix E2E_MATRIX=1 E2E_SUPERVISE_QUERY=1 E2E_INDEXING=1
    trap 'status=$?; if [[ $status -ne 0 && -n "${WORK:-}" ]]; then touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    cmd_up
    rm -f "$WORK/owner.pid"
    trap - EXIT
    say "left running: run=$RUN stack=${E2E_STACK:-ST-1} (bash $0 down $RUN)"
    ;;
  human-up)
    # scripts/human-test-stack.sh names the run, the project prefix, the state directory and the port. Engine runs
    # on, indexing with the local embedding service, the providers' real endpoints; left running.
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=true E2E_LANE=human E2E_INDEXING=1 E2E_HUMAN=1
    # The repository journeys' git host; human-test-stack.sh turns it off, its people read the real github.com.
    export E2E_FAKE_GIT="${E2E_FAKE_GIT:-1}"
    # R35-SLACK: the Slack journeys' workspace; human-test-stack.sh turns it off, its people use a real Slack.
    export E2E_FAKE_SLACK="${E2E_FAKE_SLACK:-1}"
    # R36-COVER (R31JOURNEY-FU-4): the MCP hub, STDIO runners and the scripted MCP and OAuth servers, so the journeys'
    # hub and OAuth turns run on the same stack as the rest; human-test-stack.sh turns it off. The runner image is the sandbox image
    # with the echo server added (integration-tests/harness/e2e/mcp_rows.py build_runner_image).
    export E2E_MCP_HUB="${E2E_MCP_HUB:-1}"
    [[ "$E2E_MCP_HUB" == "1" ]] && export E2E_MCP_RUNNER_IMAGE="${E2E_MCP_RUNNER_IMAGE:-pxh-mcp-runner:mcpe2e}"
    export E2E_MCP_SCHEMA_TTL_S="${E2E_MCP_SCHEMA_TTL_S:-600}"
    # R33-JGAPS: the query service under the supervisor too, as the matrix lane runs it: it claims a turn's run inline,
    # so the journeys' recovery path kills it with the worker (control/kill-query) and it must start again.
    export E2E_SUPERVISE_QUERY="${E2E_SUPERVISE_QUERY:-1}"
    trap 'status=$?; if [[ $status -ne 0 && -n "${WORK:-}" ]]; then touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    cmd_up
    rm -f "$WORK/owner.pid"
    trap - EXIT
    say "left running: run=$RUN project=$PROJECT"
    ;;
  rebuild)
    rebuild "${2:?run id}"
    ;;
  dev-up)
    # G-50 (C20.51): a laptop's stack, left running: the stack's own MongoDB replica set and Redis, the durable worker
    # for chat-loop runs, the scripted model behind the model gateway, the scripted SaaS, coding engines on hardened
    # runc (PIPESHUB_ENGINES_WITHOUT_GVISOR) and off unless E2E_ENGINE_RUNS=true. Seeded, then one scripted turn.
    export E2E_STACK=dev-harness E2E_DEV=1 E2E_OWN_REDIS=1 E2E_DURABLE=1 E2E_ENGINE_RUNS="${E2E_ENGINE_RUNS:-false}"
    export E2E_LANE=dev E2E_PROJECT_PREFIX="${E2E_PROJECT_PREFIX:-pxh-dev-}"
    export E2E_SANDBOX_IMAGE="${E2E_SANDBOX_IMAGE:-pipeshubai/pipeshub-sandbox:latest}"
    SANDBOX_IMAGE="$E2E_SANDBOX_IMAGE"
    trap 'status=$?; if [[ $status -ne 0 && -n "${WORK:-}" ]]; then touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    cmd_up
    python3 "$HERE/dev_smoke.py" "$WORK" || die "the stack is up but did not serve a scripted turn"
    rm -f "$WORK/owner.pid"
    trap - EXIT
    say "left running: http://127.0.0.1:$(envv E2E_APP_PORT) as $(envv E2E_ADMIN_EMAIL) (password: E2E_ADMIN_PASSWORD in $ENV_FILE)"
    say "bash $0 dev-smoke $RUN | bash $0 down $RUN"
    ;;
  dev-smoke)
    run_vars "${2:?run id}"
    python3 "$HERE/dev_smoke.py" "$WORK"
    ;;
  rotate-secret-key)
    rotate_secret_key "${2:?run id}" "${@:3}"
    ;;
  keyring)
    # KEYRING-FU-1: a fresh stack rotates SECRET_KEY by the runbook end to end, then serves a scripted turn.
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=false E2E_LANE=harness
    started=$SECONDS
    trap 'status=$?; if [[ -n "${WORK:-}" ]]; then [[ $status -ne 0 ]] && touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    trap 'exit 143' TERM INT
    cmd_up
    # A turn first, so the org has a data key (and the journal content under it) for the rotation to rewrap.
    python3 "$HERE/dev_smoke.py" "$WORK" || die "the stack did not serve a scripted turn before the rotation"
    rotate_secret_key "$RUN" "${ROTATION_RUNBOOK[@]}"
    python3 "$HERE/rotation_check.py" "$WORK/logs/rotate-secret-key.log" \
      || die "the rotation's counts are not those of a whole rotation"
    python3 "$HERE/dev_smoke.py" "$WORK" || die "the stack rotated its key but did not serve a scripted turn"
    say "passed (stack up in $(cat "$WORK/up-seconds") s, total $(( SECONDS - started )) s)"
    ;;
  off|off-up)
    # R50-OFF (ADR-OPS-06): the harness switched off, as an install that never turned it on. The stack is seeded as a
    # release without the harness, harness_off_check.py checks it serves nothing of it and the classic paths work,
    # then (off) the authenticated Playwright project runs on it and it is torn down; off-up leaves it running.
    export E2E_HARNESS=off E2E_DURABLE= E2E_ENGINE_RUNS=false E2E_LANE=classic
    mode="$1"
    started=$SECONDS
    trap 'status=$?; if [[ -n "${WORK:-}" ]]; then [[ $status -ne 0 ]] && touch "$WORK/failed"; [[ "$mode" == off || $status -ne 0 ]] && down_run "$RUN"; fi; exit $status' EXIT
    trap 'exit 143' TERM INT
    cmd_up
    say "harness off: the stack serves none of the harness, and the classic paths"
    set +e
    python3 "$HERE/harness_off_check.py" "$WORK" 2>&1 | tee "$WORK/logs/harness-off.log"
    checked="${PIPESTATUS[0]}"
    set -e
    # off-up keeps a stack that failed its checks, to look at (python3 deployment/e2e/harness_off_check.py WORK again).
    [[ "$checked" == 0 || "$mode" == off-up ]] || die "the stack with the harness switched off failed its checks"
    if [[ "$mode" == off-up ]]; then
      rm -f "$WORK/owner.pid"
      trap - EXIT
      say "left running: run=$RUN (bash $0 pw $RUN … | bash $0 down $RUN)"
      exit 0
    fi
    say "specs: the authenticated project with the harness switched off, ${E2E_PW_WORKERS:-2} workers"
    E2E_PW_TAG=authenticated playwright --project authenticated --workers "${E2E_PW_WORKERS:-2}" --reporter list,junit 2>&1 \
      | tee "$WORK/logs/specs-authenticated.log" || die "the authenticated project failed with the harness switched off"
    check_junit authenticated listed || die "the authenticated project's report"
    say "passed (stack up in $(cat "$WORK/up-seconds") s, total $(( SECONDS - started )) s)"
    ;;
  up|harness-up)
    export E2E_DURABLE=1 E2E_ENGINE_RUNS=false E2E_LANE=harness
    trap 'status=$?; if [[ $status -ne 0 && -n "${WORK:-}" ]]; then touch "$WORK/failed"; down_run "$RUN"; fi; exit $status' EXIT
    cmd_up
    rm -f "$WORK/owner.pid"
    trap - EXIT
    say "left running: run=$RUN (bash $0 specs $RUN | bash $0 down $RUN)"
    ;;
  *)
    die "usage: run.sh [harness|off|off-up|browser|browser-specs RUN|durable|pg|pg-world RUN|airgap|keyring|rotate-secret-key RUN [STEP...]|up|dev-up|dev-smoke RUN|matrix-up|human-up|rebuild RUN|specs RUN [ARGS]|durable-specs RUN|erase-world RUN|seckey RUN|durable-up|pw RUN ARGS|down RUN|clean]"
    ;;
esac

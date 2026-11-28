#!/usr/bin/env bash
# Requires: bash 3.2+, Docker Engine with Compose v2.20+
# ==============================================================================
# PipesHub AI — Interactive Installer  v1.0.0
# ==============================================================================
# Supports macOS, Linux (x86_64 / arm64) and Windows via WSL or Git Bash.
# Only depends on: bash, docker (compose v2), grep, tr, head, df, printf, read.
#
# Usage:
#   ./install.sh                 # interactive
#   ./install.sh --yes           # accept all defaults, non-interactive (CI)
#   ./install.sh --version 0.7.0 # pin a specific image tag
#   ./install.sh --build         # build image locally instead of pulling from Docker Hub
#   ./install.sh --no-pull       # start from the cached image (air-gapped / keep current)
#   ./install.sh --offline-bundle pipeshub-offline-1.2.3-amd64.manifest
#                                # air-gapped: verify the release's signed bundle offline, then load it
#   ./install.sh --print-env-only  # write .env and print compose command, don't launch
#   ./install.sh --reconfigure   # overwrite an existing .env (re-run wizard)
#   ./install.sh --upgrade       # pull/rebuild images and recreate containers
#   ./install.sh --rotate-signing-secrets  # replace JWT/cookie signing secrets (logs everyone out)
#   ./install.sh --stop          # stop the running stack (data preserved)
#   ./install.sh --uninstall     # stop the stack and remove all data volumes
#   ./install.sh --help
#
# Environment overrides for CI / scripted installs (all optional):
#   PIPESHUB_DEPLOY_TYPE     full | slim | eval
#   PIPESHUB_GRAPH_DB        arango | neo4j
#   PIPESHUB_BROKER          kafka | redis
#   PIPESHUB_KV_STORE        etcd | redis
#   PIPESHUB_VERSION         image tag (e.g. latest, slim, 0.7.0); for local builds the tag
#                            applied to the locally built image (default: local)
#   PIPESHUB_IMAGE_SOURCE    prebuilt | local (default: prebuilt)
#   PIPESHUB_PORT            host port to expose on (default 3000; 3200 for a separate instance)
#   PIPESHUB_PROJECT         Compose project name (default pipeshub-ai; use to run a second copy)
#   PIPESHUB_PUBLIC_URL      public HTTPS URL for external access (optional)
#   PIPESHUB_BIND_ADDRESS    host address the app port is published on (hardened default 127.0.0.1;
#                            0.0.0.0 when a public URL is set or under the compatibility profile)
#   PIPESHUB_GVISOR          auto (default: offer gVisor on a Linux Docker Engine; --yes skips it) | install | skip
#   PIPESHUB_HARDENED        true | false: the security profile (default: true on a fresh install; an upgrade keeps
#                            the release's behaviour it comes from)
# ==============================================================================
set -euo pipefail

INSTALLER_VERSION="1.0.0"
# The operator's security-profile request (ADR-OPS-07), read before an .env is sourced over it.
PIPESHUB_HARDENED_REQUESTED="${PIPESHUB_HARDENED-}"

# ── Transparent sudo re-exec (Linux: Docker socket not accessible to current user) ──
# If the Docker socket file exists but docker info fails, the user is almost
# certainly not in the 'docker' group. Re-exec with sudo so the rest of the
# installer works without a cryptic permission error.
# This runs before arg parsing so "$@" is still the full original argument list.
if [[ $EUID -ne 0 ]] && \
   command -v docker >/dev/null 2>&1 && \
   [[ -S /var/run/docker.sock ]] && \
   ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1; then
    # sudo resets the environment, so every setting read below that is set here is named to keep. --preserve-env=list, not
    # `sudo env NAME=value`: it keeps the values off sudo's logged command line and also works where sudo allows only this script.
    _keep=""
    for _v in PIPESHUB_DEPLOY_TYPE PIPESHUB_GRAPH_DB PIPESHUB_BROKER PIPESHUB_KV_STORE PIPESHUB_VERSION PIPESHUB_IMAGE_SOURCE \
              PIPESHUB_NO_PULL PIPESHUB_PORT PIPESHUB_PROJECT PIPESHUB_PUBLIC_URL PIPESHUB_IMAGE PIPESHUB_COMPOSE_OVERRIDES \
              PIPESHUB_ENV_EXTRA PIPESHUB_INSTALL_REF PIPESHUB_BLOCK_PRIVATE_ADDRESSES HEALTH_WAIT_SECS APP_MEMORY_LIMIT \
              APP_MEMSWAP_LIMIT APP_VERSION GIT_COMMIT IMAGE_TAG DEPLOY_TYPE DATA_STORE KV_STORE_TYPE MESSAGE_BROKER \
              COMPOSE_PROFILES QDRANT_MEMORY_LIMIT QDRANT_MEMORY_RESERVATION PG_MEMORY_LIMIT PG_SHARED_BUFFERS PG_SHM \
              NEO4J_HEAP_INITIAL NEO4J_HEAP_MAX NEO4J_PAGECACHE NEO4J_MEMORY_LIMIT NEO4J_MEMORY_RESERVATION \
              MONGO_GLIBC_TUNABLES MONGO_IMAGE_TAG MONGO_CACHE_GB MONGO_MEMORY_LIMIT MONGO_MEMORY_RESERVATION SANDBOX_DOCKER_IMAGE \
              PIPESHUB_AGENT_HARNESS PIPESHUB_HARDENED PIPESHUB_BIND_ADDRESS PIPESHUB_SANDBOX_IMAGE \
              PIPESHUB_IMAGE_CAPABILITIES PIPESHUB_OFFLINE_BUNDLE PIPESHUB_IMAGE_VERIFY PIPESHUB_IMAGE_VERIFY_NO_TLOG \
              PIPESHUB_IMAGE_PUBKEY PIPESHUB_SIGNER_REPOSITORY PIPESHUB_SIGNER_WORKFLOW PIPESHUB_GVISOR \
              PIPESHUB_GVISOR_RELEASE PIPESHUB_GVISOR_BASE_URL PIPESHUB_GVISOR_BIN_DIR PIPESHUB_GVISOR_SHA512 \
              SANDBOX_MODE SANDBOX_BROKER_WAIT_SECS DOCKER_HOST MONGO_URI PG_MONITOR_PASSWORD; do
      [[ -n "${!_v+x}" ]] && _keep="${_keep:+$_keep,}$_v"
    done
    # With nothing set the call stays plain sudo, which needs no permission to keep variables.
    if [[ -n "$_keep" ]]; then exec sudo --preserve-env="$_keep" "${BASH_SOURCE[0]}" "$@"; fi
    exec sudo "${BASH_SOURCE[0]}" "$@"
  fi
  # sudo not available — fall through; the pre-flight check will give a clear error
fi

# ── colour helpers (degrade gracefully when not in a colour terminal) ─────────
if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
  BOLD=$(tput bold)
  DIM=$(tput dim 2>/dev/null || echo "")
  RED=$(tput setaf 1)
  GREEN=$(tput setaf 2)
  YELLOW=$(tput setaf 3)
  CYAN=$(tput setaf 6)
  RESET=$(tput sgr0)
else
  BOLD="" DIM="" RED="" GREEN="" YELLOW="" CYAN="" RESET=""
fi

info()    { printf "${CYAN}  >${RESET} %s\n" "$*"; }
success() { printf "${GREEN}  ✔${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}  !${RESET} %s\n" "$*"; }
error()   { printf "${RED}  ✖${RESET} %s\n" "$*" >&2; }
header()  { printf "\n${BOLD}${CYAN}%s${RESET}\n${DIM}" "$*"; printf '─%.0s' {1..60}; printf "${RESET}\n"; }
die()     { error "$*"; exit 1; }

# ── constants ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
ENV_FILE="${SCRIPT_DIR}/.env"
COMPOSE_FILE="${SCRIPT_DIR}/docker-compose.yml"
# PIPESHUB_IMAGE and PIPESHUB_COMPOSE_OVERRIDES are saved in .env when the
# installer writes it, so later runs (--stop, --upgrade, ...) keep using them.
_saved_env_val() { [[ -f "$ENV_FILE" ]] && grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- || true; }
PIPESHUB_IMAGE="${PIPESHUB_IMAGE:-$(_saved_env_val PIPESHUB_IMAGE)}"
PIPESHUB_COMPOSE_OVERRIDES="${PIPESHUB_COMPOSE_OVERRIDES:-$(_saved_env_val PIPESHUB_COMPOSE_OVERRIDES)}"

# Every compose call reads docker-compose.yml plus the files listed in
# PIPESHUB_COMPOSE_OVERRIDES (colon-separated; relative paths start here). Every later
# choice of files starts from BASE_COMPOSE_FILES, so none of them drops the overrides.
BASE_COMPOSE_FILES=(-f "$COMPOSE_FILE")
if [[ -n "${PIPESHUB_COMPOSE_OVERRIDES:-}" ]]; then
  IFS=':' read -r -a _compose_overrides <<< "$PIPESHUB_COMPOSE_OVERRIDES"
  for _override in "${_compose_overrides[@]}"; do
    [[ -n "$_override" ]] || continue
    [[ -f "$_override" ]] || die "PIPESHUB_COMPOSE_OVERRIDES: $_override not found"
    BASE_COMPOSE_FILES+=(-f "$_override")
  done
fi
COMPOSE_FILES=("${BASE_COMPOSE_FILES[@]}")
# The same files, as the hints print them for an operator to paste.
COMPOSE_FILES_SHOWN="${BASE_COMPOSE_FILES[*]}"
APP_IMAGE_REPO="${PIPESHUB_IMAGE:-pipeshubai/pipeshub-ai}"
DEFAULT_PROJECT="pipeshub-ai"
PROJECT_NAME="$DEFAULT_PROJECT"
INSTALL_SEPARATE=false
# Fresh install uses 3000. A second copy on the same host defaults to 3200 so it
# does not steal the first instance's published port.
DEFAULT_APP_PORT=3000
# First start downloads the embedding model and cold-starts the full stack, which
# on smaller hosts can edge past 5 min; default generously and allow overriding.
HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-420}"
# APP_PORT and HEALTH_URL are resolved later (after port selection in the wizard)

# ── CLI flags ─────────────────────────────────────────────────────────────────
FLAG_YES=false
FLAG_PRINT_ENV_ONLY=false
FLAG_RECONFIGURE=false
FLAG_UPGRADE=false
FLAG_STOP=false
FLAG_UNINSTALL=false
FLAG_DELETE_POSTGRES=false
FLAG_BUILD=false
FLAG_NO_PULL=false
OFFLINE_BUNDLE="${PIPESHUB_OFFLINE_BUNDLE:-}"
FLAG_ROTATE_SIGNING_SECRETS=false
FLAG_SKIP_GVISOR=false
FLAG_INSTALL_GVISOR=false
CLI_VERSION=""

# ── CLI argument parsing ──────────────────────────────────────────────────────
usage() {
  cat <<EOF
${BOLD}PipesHub AI Installer v${INSTALLER_VERSION}${RESET}

Usage: $(basename "$0") [OPTIONS]

Options:
  -y, --yes            Accept all defaults, skip interactive prompts (CI)
      --version TAG    Pin a specific image tag (e.g. 0.7.0, latest, slim)
      --build          Build image locally from source instead of pulling from Docker Hub
      --no-pull        Do not refresh the image; start from the locally cached one
                       (air-gapped hosts, or to keep a known-good/old image)
      --offline-bundle MANIFEST
                       Air-gapped install from the release's offline bundle: checks its
                       signature offline (needs cosign on the host), then each file's
                       checksum, then loads the images (implies --no-pull)
      --print-env-only Write .env and print the compose command; do not launch
      --reconfigure    Overwrite an existing .env (re-run the wizard)
      --upgrade        Pull or rebuild images and recreate containers (data preserved)
      --rotate-signing-secrets
                       Replace JWT, scoped JWT, and cookie signing secrets.
                       Every session, refresh token, password-reset link, and
                       in-flight service token stops working. Requires an
                       existing .env. Combine with --upgrade to rotate during
                       an image refresh. Pass --yes to skip the confirm prompt.
      --skip-gvisor    Do not install gVisor (runsc); sandboxes use hardened runc
      --install-gvisor Install gVisor without asking, also under --yes (it edits
                       /etc/docker/daemon.json and reloads Docker; --yes alone never does)
      --stop           Stop the running stack (data preserved)
      --uninstall      Stop and remove ALL data volumes (irreversible)
      --delete-postgres  With --uninstall: also delete the PostgreSQL data volume. Without it, --yes keeps that volume and an
                       interactive run asks.
  -h, --help           Show this help

Environment overrides (bypass prompts in CI):
  PIPESHUB_DEPLOY_TYPE   full | slim | eval
  PIPESHUB_GRAPH_DB      arango | neo4j
  PIPESHUB_BROKER        kafka | redis
  PIPESHUB_KV_STORE      etcd | redis
  PIPESHUB_IMAGE_SOURCE  prebuilt | local  (default: prebuilt)
  PIPESHUB_NO_PULL       1 | true to skip the image refresh (same as --no-pull)
  PIPESHUB_OFFLINE_BUNDLE  the offline bundle's manifest (same as --offline-bundle)
  PIPESHUB_VERSION       image tag (prebuilt) or local build tag (default: local)
  PIPESHUB_PORT          host port (default: 3000; 3200 when installing a second copy)
  PIPESHUB_PROJECT       Compose project name (default: pipeshub-ai)
  PIPESHUB_IMAGE         app image repository (default: pipeshubai/pipeshub-ai)
  PIPESHUB_SANDBOX_IMAGE code sandbox image, a full reference (default: pipeshubai/pipeshub-sandbox:<tag>;
                         a prebuilt install writes it to .env as SANDBOX_DOCKER_IMAGE)
  PIPESHUB_COMPOSE_OVERRIDES  extra compose files, colon-separated, layered over docker-compose.yml
                         (relative paths start in this directory; saved in .env with PIPESHUB_IMAGE)
  PIPESHUB_ENV_EXTRA     file whose lines are appended to a newly written .env (no keys the
                         installer writes itself; relative paths start in this directory)
  PIPESHUB_PUBLIC_URL    public HTTPS URL (e.g. https://pipeshub.yourdomain.com)
  PIPESHUB_BIND_ADDRESS  host address the app port is published on (default 127.0.0.1, this machine
                         only; 0.0.0.0 when a public URL is set). Saved in .env as APP_BIND_ADDRESS
  PIPESHUB_GVISOR        auto | install | skip  (gVisor for code sandboxes on a Linux Docker Engine;
                         auto asks, and --yes then skips it)
  PIPESHUB_AGENT_HARNESS on | off  (the agent harness, saved in .env as AGENT_HARNESS_WORKER_ENABLED;
                         default on, an existing .env keeps its choice; orgs still turn it on in Labs.
                         on also moves an upgraded install's code execution to the sandbox broker,
                         which coding engines need)
  PIPESHUB_IMAGE_CAPABILITIES  what the app image can run, when its label cannot be read (comma list
                         sandbox-broker,secret-key-file,agent-harness; none for an image older than them)
  PIPESHUB_HARDENED      true | false  (the security profile, saved in .env; a fresh install is hardened,
                         an upgrade from a release without it keeps that release's behaviour until
                         PIPESHUB_HARDENED=true ./install.sh --upgrade; an existing .env keeps its choice)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)            FLAG_YES=true ;;
    --version)           [[ $# -lt 2 ]] && die "--version requires a TAG argument (e.g. --version 0.7.0)"; CLI_VERSION="$2"; shift ;;
    --build)             FLAG_BUILD=true ;;
    --no-pull)           FLAG_NO_PULL=true ;;
    --offline-bundle)    [[ $# -lt 2 ]] && die "--offline-bundle requires the bundle's .manifest file"; OFFLINE_BUNDLE="$2"; shift ;;
    --print-env-only)    FLAG_PRINT_ENV_ONLY=true ;;
    --reconfigure)       FLAG_RECONFIGURE=true ;;
    --upgrade)           FLAG_UPGRADE=true ;;
    --rotate-signing-secrets) FLAG_ROTATE_SIGNING_SECRETS=true ;;
    --skip-gvisor)       FLAG_SKIP_GVISOR=true ;;
    --install-gvisor)    FLAG_INSTALL_GVISOR=true ;;
    --stop)              FLAG_STOP=true ;;
    --uninstall)         FLAG_UNINSTALL=true ;;
    --delete-postgres)   FLAG_DELETE_POSTGRES=true ;;
    -h|--help)           usage; exit 0 ;;
    *) die "Unknown option: $1. Use --help for usage." ;;
  esac
  shift
done

if [[ -n "$OFFLINE_BUNDLE" ]]; then
  $FLAG_BUILD && die "--offline-bundle loads release images; it cannot be combined with --build."
  FLAG_NO_PULL=true
  # The tag the bundle's images carry; the signature over this line is checked before anything is loaded.
  if [[ -z "$CLI_VERSION" && -z "${PIPESHUB_VERSION:-}" && -f "$OFFLINE_BUNDLE" ]]; then
    CLI_VERSION="$(sed -n 's/^version \([A-Za-z0-9._-]*\)$/\1/p' "$OFFLINE_BUNDLE" | head -1)"
  fi
fi

if $FLAG_ROTATE_SIGNING_SECRETS && $FLAG_STOP; then
  die "--rotate-signing-secrets cannot be combined with --stop."
fi
if $FLAG_ROTATE_SIGNING_SECRETS && $FLAG_UNINSTALL; then
  die "--rotate-signing-secrets cannot be combined with --uninstall."
fi

# ── helpers ───────────────────────────────────────────────────────────────────

# Generate a random hex string of length (output bytes) using only /dev/urandom
# + tr. Produces strictly [a-f0-9] — safe for URIs, shell variables, and all
# database auth strings (no @, $, :, #, etc.).
gen_secret() {
  local length="${1:-32}"
  # Run in a subshell with pipefail disabled: when head(1) exits after reading
  # enough bytes, tr gets SIGPIPE (exit 141). pipefail would propagate that
  # non-zero status and trip set -e in the caller.
  ( set +o pipefail; LC_ALL=C tr -dc 'a-f0-9' < /dev/urandom 2>/dev/null | head -c "$((length * 2))" )
}

# SEC-D-05: the checks the services make at start that a shell can make here (the services also refuse a key that
# is a pattern or encoded text). Prints the reason, or nothing for a key that passes.
weak_secret_reason() {
  local key="$1" folded distinct
  folded="$(printf '%s' "$key" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
  case "$folded" in
    *yourrandomencryptionsecretkey*|changeme|secret|secretkey|password|pipeshub|default|example|test|admin)
      echo "is a published placeholder"; return ;;
  esac
  if [[ ${#key} -lt 32 ]]; then echo "is shorter than 32 characters"; return; fi
  distinct="$(printf '%s' "$key" | fold -w1 | sort -u | wc -l | tr -d ' ')"
  if [[ "$distinct" -lt 10 ]]; then echo "repeats too few characters to be random"; fi
}

# SEC-D-06: credentials every store starts with, whatever the profiles. Each store reads its credential from the
# environment at every start (Redis's requirepass, Qdrant's API key, Kafka's SCRAM user written by kafka-init) and keeps
# none on its volume, so one missing from an older .env can be generated without locking anyone out of their data.
# MONGO_PASSWORD and the graph passwords are not here: those stores keep the password on their volume.
# The component keys and the legacy proxy token (ADR-KEY-02) have the same property.
# DOCKER_PROXY_TOKEN is not here: sync_docker_proxy_app_token writes it only in docker mode.
REGENERABLE_CREDENTIALS=(
  "REDIS_PASSWORD 16" "QDRANT_API_KEY 20" "KAFKA_PASSWORD 24"
  "EGRESS_PROXY_COMPONENT_KEY 32" "SANDBOX_BROKER_COMPONENT_KEY 32"
  "DOCKER_PROXY_BROKER_TOKEN 32"
)

# An .env from an older installer may lack one: generate it and write it back, saying which (never the value).
heal_regenerable_credentials() {
  local entry key length
  for entry in "${REGENERABLE_CREDENTIALS[@]}"; do
    key="${entry% *}"; length="${entry#* }"
    [[ -n "$(get_existing_val "$key" "")" ]] && continue
    printf -v "$key" '%s' "$(gen_secret "$length")"
    persist_env_var "$key" "${!key}"
    info "Generated ${key} in ${ENV_FILE} (SEC-D-06, ADR-KEY-02)."
  done
}

# SEC-D-08: the host address the app port is published on. Docker's published ports bypass ufw and firewalld, so
# keeping the app on this machine has to be the bind address. An explicit choice wins; otherwise a public URL means other
# machines (an HTTPS proxy or load balancer) must reach the port, and anything else means this machine only.
app_bind_address() {
  local explicit="$1" public_url="$2" host
  if [[ -n "$explicit" ]]; then printf '%s' "$explicit"; return; fi
  # tr, not ${x,,}: the installer supports bash 3.2 (macOS).
  host="$(printf '%s' "${public_url#*://}" | tr '[:upper:]' '[:lower:]')"
  if [[ "$host" == \[* ]]; then host="${host%%]*}]"; else host="${host%%[/:]*}"; fi
  case "$host" in
    ""|localhost|127.*|"[::1]") printf '127.0.0.1' ;;
    *) printf '0.0.0.0' ;;
  esac
}

# What the bind address means for the operator, and what to set to change it.
explain_bind_address() {
  local address="$1" port="$2" public_url="$3"
  case "$address" in
    127.0.0.1|localhost|::1|"[::1]")
      info "The app listens on ${address}:${port}: only this machine can reach it (http://localhost:${port})."
      info "To serve other machines, put HTTPS in front and set FRONTEND_PUBLIC_URL (./install.sh --reconfigure),"
      info "or set APP_BIND_ADDRESS in .env (ADVANCED_DEPLOYMENT.md, \"Remote access and HTTPS\")."
      ;;
    *)
      warn "The app listens on ${address}:${port} over plain HTTP, reachable from other machines."
      warn "Docker's published ports bypass ufw and firewalld, so a host firewall does not close it."
      if [[ "$public_url" == http://* ]]; then
        warn "FRONTEND_PUBLIC_URL is plain http: passwords and sign-in tokens cross the network unencrypted."
        warn "Put HTTPS in front (Caddy or a Cloudflare Tunnel: ADVANCED_DEPLOYMENT.md, \"Remote access and HTTPS\")."
      fi
      warn "If your HTTPS proxy runs on this machine, set APP_BIND_ADDRESS=127.0.0.1 in .env so port ${port} stays local."
      ;;
  esac
}

# SEC-A-03: the block device under the daemon's volumes, which the sandbox broker caps each sandbox's writes on (the
# kernel's io.max takes a whole disk or a device-mapper device, never a partition). Blank when it cannot be found:
# the broker then runs without the cap and says so at start.
sandbox_data_device() {
  local root src majmin name kind parent
  root="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)" || return 0
  src="$(findmnt -no SOURCE --target "$root" 2>/dev/null | head -n1)" || return 0
  src="${src%%\[*}"
  [[ "$src" == /dev/* ]] || return 0
  kind="$(lsblk -dno TYPE "$src" 2>/dev/null | head -n1)"
  parent="$(lsblk -dno PKNAME "$src" 2>/dev/null | head -n1)"
  if [[ -z "$kind" ]]; then
    # Cloud images mount / as /dev/root, a name with no device node behind it: the mount's device number finds the
    # real one, and a name the kernel cannot cap is never written.
    majmin="$(findmnt -no MAJ:MIN --target "$root" 2>/dev/null | head -n1)"
    majmin="${majmin// /}"
    read -r name kind parent < <(lsblk -rno MAJ:MIN,NAME,TYPE,PKNAME 2>/dev/null |
      awk -v m="$majmin" '$1 == m { print $2, $3, $4; exit }') || return 0
    src="/dev/$name"
  fi
  case "$kind" in
    "") ;;
    part) [[ -n "$parent" ]] && printf '/dev/%s' "$parent" ;;
    *) printf '%s' "$src" ;;
  esac
  return 0
}

# ADR-OPS-06 (amended 2026-11-20): the agent harness's switch, AGENT_HARNESS_WORKER_ENABLED. On for a fresh install
# and for an upgrade whose .env does not name it: the harness runs idle until an org admin turns on Agent harness
# (Beta) in Labs, which is off by default. An .env that names it keeps its choice, so an upgrade never turns a
# disabled harness on. PIPESHUB_AGENT_HARNESS=on|off decides instead.
agent_harness_switch() { # -> true|false
  case "$(printf '%s' "${PIPESHUB_AGENT_HARNESS:-}" | tr '[:upper:]' '[:lower:]')" in
    on|true|yes|1)  echo true; return ;;
    off|false|no|0) echo false; return ;;
    "") ;;
    *) die "PIPESHUB_AGENT_HARNESS must be on or off, not '${PIPESHUB_AGENT_HARNESS}'" ;;
  esac
  local named
  named="$(get_existing_val AGENT_HARNESS_WORKER_ENABLED "")"
  if [[ -n "$named" ]]; then echo "$named"; return; fi
  echo true
}

# Whether this run asks for the harness (PIPESHUB_AGENT_HARNESS=on). The switch's default, which an upgrade writes into
# .env, is not a request: an upgrade under the compatibility profile keeps running code as the release it came from did
# (ADR-OPS-07), and moves to the sandbox broker only when the operator asks for the harness or the hardened profile.
agent_harness_requested() { # -> true|false
  case "$(printf '%s' "${PIPESHUB_AGENT_HARNESS:-}" | tr '[:upper:]' '[:lower:]')" in
    on|true|yes|1) echo true ;;
    *)             echo false ;;
  esac
}

# What the app image can run, from its label (the Dockerfile writes it; release_tooling_test.sh keeps this list equal
# to it). An image without the label predates the sandbox broker: this installer then sets it up as the upstream
# installer did (SANDBOX_MODE=docker, no broker), so a release image that is not published yet breaks nothing.
APP_IMAGE_CAPABILITIES_LABEL="ai.pipeshub.capabilities"
APP_IMAGE_CAPABILITIES_ALL="sandbox-broker,secret-key-file,agent-harness"

# -> the label's list; "none" when the image has no label; "unknown" when neither the local copy nor the registry can
# be read (an image not pulled yet with no registry access, or no buildx): this release's own list is assumed then.
# PIPESHUB_IMAGE_CAPABILITIES names the list instead (a mirror whose labels cannot be read; "none" for an older image).
app_image_capabilities() {
  if [[ -n "${PIPESHUB_IMAGE_CAPABILITIES+set}" ]]; then printf '%s' "${PIPESHUB_IMAGE_CAPABILITIES:-none}"; return 0; fi
  if [[ "${IMAGE_SOURCE:-prebuilt}" == local ]]; then printf '%s' "$APP_IMAGE_CAPABILITIES_ALL"; return 0; fi
  local ref="${APP_IMAGE_REPO}:${IMAGE_TAG:-latest}" out
  if out="$(docker image inspect "$ref" --format "{{ index .Config.Labels \"${APP_IMAGE_CAPABILITIES_LABEL}\" }}" 2>/dev/null)"; then
    [[ -z "$out" || "$out" == "<no value>" ]] && out=none
    printf '%s' "$out"
    return 0
  fi
  if out="$(docker buildx imagetools inspect "$ref" --format '{{ json .Image }}' 2>/dev/null)"; then
    out="$(printf '%s' "$out" | grep -oE '"ai\.pipeshub\.capabilities": *"[^"]*"' | head -1 | sed -E 's/.*: *"([^"]*)"$/\1/')"
    printf '%s' "${out:-none}"
    return 0
  fi
  printf unknown
}

image_can() { # args: capability capabilities -> status
  [[ "$2" == unknown || ",$2," == *",$1,"* ]]
}

# ADR-OPS-07: the security profile, PIPESHUB_HARDENED. A fresh install is hardened: nothing it has could break. An
# .env from a release without the switch keeps that release's behaviour (false, written down so it is visible) until
# the operator opts in; an .env this installer wrote before the switch existed ran the hardened defaults (its sandbox
# keys or bind address are the mark), so it stays hardened; an .env that names it keeps it. PIPESHUB_HARDENED given to
# the installer decides instead. Run before heal_regenerable_credentials, which writes those marks.
security_profile_choice() { # args: env_existed(true|false) -> true|false
  local existed="$1" named
  case "$(printf '%s' "${PIPESHUB_HARDENED_REQUESTED:-}" | tr '[:upper:]' '[:lower:]')" in
    true|on|yes|1)  echo true; return ;;
    false|off|no|0) echo false; return ;;
    "") ;;
    *) die "PIPESHUB_HARDENED must be true or false, not '${PIPESHUB_HARDENED_REQUESTED}'" ;;
  esac
  [[ "$existed" == true ]] || { echo true; return; }
  named="$(printf '%s' "$(get_existing_val PIPESHUB_HARDENED "")" | tr '[:upper:]' '[:lower:]')"
  case "$named" in
    true|on|yes|1)  echo true; return ;;
    false|off|no|0) echo false; return ;;
  esac
  local mark
  for mark in SANDBOX_BROKER_COMPONENT_KEY EGRESS_PROXY_COMPONENT_KEY DOCKER_PROXY_BROKER_TOKEN APP_BIND_ADDRESS; do
    if [[ -n "$(get_existing_val "$mark" "")" ]]; then echo true; return; fi
  done
  echo false
}

# What the profile means, said once per run where it was decided.
explain_security_profile() { # args: hardened(true|false)
  if [[ "$1" == true ]]; then
    info "Security profile: hardened (PIPESHUB_HARDENED=true): stores require credentials, a weak SECRET_KEY is replaced,"
    info "the app port binds 127.0.0.1 unless a public URL is set, the CSP is enforced (guides/HARNESS-OFF.md)."
  else
    warn "Security profile: compatibility (PIPESHUB_HARDENED=false): this install keeps the behaviour of the release it"
    warn "came from: open stores stay open, the app port stays on every interface, iframes and API clients work as before."
    warn "To adopt the hardened defaults: PIPESHUB_HARDENED=true ./install.sh --upgrade (guides/HARNESS-OFF.md)."
  fi
}

# SEC-A-04: the app's credential at the Docker API proxy, held only while the app creates sandboxes itself
# (SANDBOX_MODE=docker). On the broker it is blank, so the proxy admits the app to nothing.
sync_docker_proxy_app_token() { # args: sandbox_mode
  if [[ "$1" == docker ]]; then
    [[ -n "$(get_existing_val DOCKER_PROXY_TOKEN "")" ]] && return 0
    DOCKER_PROXY_TOKEN="$(gen_secret 32)"
    persist_env_var DOCKER_PROXY_TOKEN "$DOCKER_PROXY_TOKEN"
    info "Generated DOCKER_PROXY_TOKEN in ${ENV_FILE}: the app's credential at the Docker API proxy (SEC-A-04)."
    return 0
  fi
  if [[ -n "$(get_existing_val DOCKER_PROXY_TOKEN "")" ]]; then
    DOCKER_PROXY_TOKEN=""
    persist_env_var DOCKER_PROXY_TOKEN ""
    info "Cleared DOCKER_PROXY_TOKEN: on SANDBOX_MODE=${1:-(off)} the app does not reach the Docker API proxy."
  fi
}

# The Docker socket's group as the Docker API proxy sees it: the proxy joins that group instead of running as root
# (SEC-D-02). Read fresh on every run, since a host's docker group id can change; Docker Desktop's socket is root:root.
docker_socket_gid() {
  if [[ "$(uname -s)" == "Linux" && -S /var/run/docker.sock ]]; then
    stat -c %g /var/run/docker.sock
  else
    echo 0
  fi
}

# SEC-D-05 under the hardened profile: a weak SECRET_KEY moves to SECRET_KEY_PREVIOUS (still read, ADR-KEY-01) and a
# new random key takes its place. The compatibility profile keeps it with a warning: the services accept it there,
# and a release without the key ring (a rollback) can still open everything written under it.
secure_secret_key() { # args: hardened(true|false); reads and may set SECRET_KEY, SECRET_KEY_PREVIOUS
  local weak
  weak="$(weak_secret_reason "$SECRET_KEY")"
  [[ -n "$weak" ]] || return 1
  if [[ "$1" != true ]]; then
    warn "SECRET_KEY in $ENV_FILE $weak. Kept (compatibility profile); generate one with openssl rand -base64 32"
    warn "and rotate (guides/runbooks.md \"Rotating SECRET_KEY\"), or opt in: PIPESHUB_HARDENED=true ./install.sh --upgrade."
    return 1
  fi
  if [[ -n "$SECRET_KEY_PREVIOUS" ]]; then
    die "SECRET_KEY in $ENV_FILE $weak, and a rotation is already under way (SECRET_KEY_PREVIOUS is set). Finish it first (guides/runbooks.md \"Rotating SECRET_KEY\")."
  fi
  warn "SECRET_KEY in $ENV_FILE $weak. Generating a new random key; the old one is kept as SECRET_KEY_PREVIOUS so"
  warn "everything stored under it still opens. Once PipesHub is up, move your data to the new key:"
  warn "  docker compose exec pipeshub-ai node dist/modules/agent-harness/ops/rotate-secret-key-cli.js rotate"
  warn "  docker compose exec pipeshub-ai node dist/modules/agent-harness/ops/rotate-secret-key-cli.js retire"
  warn "then remove SECRET_KEY_PREVIOUS from $ENV_FILE and run ./install.sh again."
  SECRET_KEY_PREVIOUS="$SECRET_KEY"
  SECRET_KEY="$(gen_secret 32)"
  return 0
}

# Retrieve an existing value from .env (if the file exists), falling back to
# the supplied default when the key is absent or empty.  Used during
# --reconfigure to preserve secrets that were already used to initialise
# database volumes — regenerating them would break authentication.

# The PostgreSQL password rule of postgres/pg-entrypoint.sh (check_password); tests/installer_test.sh pins the two together.
pg_password_ok() {
  [[ "$1" != *[!A-Za-z0-9._~-]* ]]
}

# A value saved in .env quoted, as Compose allows ('x' or "x"), read back bare.
env_unquote() {
  local v="$1"
  if (( ${#v} >= 2 )) && [[ ( "$v" == \'*\' ) || ( "$v" == \"*\" ) ]]; then v="${v:1:${#v}-2}"; fi
  printf '%s' "$v"
}

get_existing_val() {
  local key="$1" default="$2" val=""
  if [[ -f "$ENV_FILE" ]]; then
    val="$(grep -E "^${key}=" "$ENV_FILE" | cut -d'=' -f2-)"
  fi
  printf '%s' "${val:-$default}"
}

# Render an optional knob for .env: the operator's value when one is set,
# otherwise a commented hint. .env is rewritten from scratch whenever the wizard
# runs, so a knob documented only in env.template is invisible after a fresh
# install and is dropped by --reconfigure. Callers must pass a value already
# resolved by get_existing_val -- .env is being truncated by the time the
# here-document that calls this is expanded.
# Memory the sibling containers (graph DB, vector DB, Mongo, broker) plus the
# host OS need alongside the app. Sized from what those services actually use
# at rest -- Neo4j dominates at 1.5-2 GB -- not from their compose limits,
# which are ceilings none of them reach simultaneously.
_SIBLING_MEMORY_RESERVE_MB=4096
# Never derive below this: it is the historical default, and the governor's
# ceilings need somewhere to grow into.
_APP_MEMORY_FLOOR_MB=6144
# Past this the app stops converting RAM into throughput -- the ceilings are
# CPU-derived and the extra only widens buffers nothing fills.
_APP_MEMORY_CAP_MB=32768
# Swap headroom kept above the memory limit, matching the shipped 10G/16G
# pair. Lets the kernel page out an idle co-located service instead of
# OOM-killing the container.
_APP_SWAP_HEADROOM_MB=6144

# Largest APP_MEMORY_LIMIT this machine can give the app container, in MB.
# Echoes nothing when the available memory cannot be determined, so the caller
# falls back to the compose default rather than guessing.
recommended_app_memory_mb() {
  local available_mb=0 derived
  # On Docker Desktop the VM is the real ceiling, not host RAM: the host may
  # have 64 GB while the VM is capped at 8.
  if $IS_MACOS || $IS_WINDOWS; then
    available_mb="$(docker_vm_mem_mb)"
  fi
  if (( available_mb <= 0 )); then
    available_mb="${TOTAL_RAM_MB:-0}"
  fi
  (( available_mb > 0 )) || return 0

  derived=$(( available_mb - _SIBLING_MEMORY_RESERVE_MB ))
  (( derived < _APP_MEMORY_FLOOR_MB )) && derived=$_APP_MEMORY_FLOOR_MB
  (( derived > _APP_MEMORY_CAP_MB )) && derived=$_APP_MEMORY_CAP_MB
  # Whole gigabytes: the value goes into .env for humans to read and edit.
  echo $(( derived / 1024 * 1024 ))
}

optional_env_line() {
  local key="$1" val="$2" hint="$3"
  if [[ -n "$val" ]]; then
    printf '%s=%s' "$key" "$val"
  else
    printf '# %s=%s' "$key" "$hint"
  fi
}

# The opt-in PostgreSQL profiles are not derived from a selector: keep the ones already set (in the shell or in the saved .env), and
# postgres and postgres-migrate whenever the document-store override is in use, since it depends on services of both.
opt_in_profiles() {
  local o have="${COMPOSE_PROFILES:-},$(_saved_env_val COMPOSE_PROFILES)"
  for o in postgres postgres-migrate postgres-metrics; do
    if [[ ",${have}," == *",${o},"* || ( "$o" != postgres-metrics && "${PIPESHUB_COMPOSE_OVERRIDES:-}" == *docker-compose.postgres-docstore.yml* ) ]]; then
      echo "$o"
    fi
  done
}

# Derive the COMPOSE_PROFILES that the *currently configured* services require,
# from the canonical selectors persisted in .env. The app talks to whatever
# DATA_STORE / MESSAGE_BROKER / KV_STORE_TYPE say, so the profiles that start the
# matching containers must agree with them. A stale or hand-edited
# COMPOSE_PROFILES (e.g. from an older installer that used different profile
# names) otherwise silently leaves the graph DB or broker container down.
derive_compose_profiles() {
  local p=()
  case "${DATA_STORE:-}" in
    arangodb) p+=("graph-arango") ;;
    neo4j)    p+=("graph-neo4j") ;;
  esac
  [[ "${KV_STORE_TYPE:-}"  == "etcd"  ]] && p+=("kv-etcd")
  [[ "${MESSAGE_BROKER:-}" == "kafka" ]] && p+=("broker-kafka")
  # Code execution: the sandbox image and the Docker API proxy. Eval skips it
  # (Neo4j stays: search ACLs live there; run_code is the cut); e2b/local need
  # none of it. The broker and egress proxy run on the broker and while an install
  # migrates to it; an install that stays on docker starts neither, whatever the
  # harness switch says (its engines then report no sandbox).
  case "${SANDBOX_MODE:-}" in broker|docker) p+=("sandbox") ;; esac
  if [[ "${SANDBOX_MODE:-}" == broker || "${SANDBOX_PLAN:-}" == migrate ]]; then
    p+=("sandbox-broker")
  fi
  local o
  while IFS= read -r o; do [[ -n "$o" ]] && p+=("$o"); done < <(opt_in_profiles)
  # Guard the empty-array case: on bash 3.2 under `set -u`, "${p[*]}" on an empty
  # array is an unbound-variable error.
  if (( ${#p[@]} == 0 )); then echo ""; return; fi
  (IFS=','; echo "${p[*]}")
}

# Update KEY=VALUE in .env in place (replacing an existing line or appending),
# without sed/awk so it works identically on macOS and Linux.
persist_env_var() {
  local key="$1" val="$2" tmp line found=false
  [[ -f "$ENV_FILE" ]] || return 0
  tmp="$(mktemp)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    # Also match the commented placeholder form ("# KEY=hint") so turning a
    # documented knob on replaces its hint instead of leaving both lines.
    if [[ "$line" == "${key}="* || "$line" == "# ${key}="* ]]; then
      printf '%s=%s\n' "$key" "$val"; found=true
    else
      printf '%s\n' "$line"
    fi
  done < "$ENV_FILE" > "$tmp"
  $found || printf '%s=%s\n' "$key" "$val" >> "$tmp"
  # Overwrite contents rather than `mv` so the file keeps its inode, ownership,
  # chmod 600, and any symlinks pointing at it.
  cat "$tmp" > "$ENV_FILE" && rm -f "$tmp"
}

# Docker Desktop VM memory in MB, or 0 when unknown. Guards against docker info
# emitting an empty or non-numeric MemTotal, which would otherwise make the
# arithmetic below fail and abort the whole installer under `set -e`.
docker_vm_mem_mb() {
  local bytes
  bytes="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
  [[ "$bytes" =~ ^[0-9]+$ ]] || bytes=0
  echo $(( bytes / 1024 / 1024 ))
}

# ── Code execution: sandbox broker, legacy socket, gVisor ─────────────────────
# What this run does about code execution, from DEPLOY_TYPE and SANDBOX_MODE as
# .env has them ("__unset__" when the key is absent, which every .env written
# before the sandbox broker existed is: Compose then defaulted to docker with
# the socket in the app container).
#   disabled  eval, or SANDBOX_MODE= (blank): run_code off, no sandbox profile
#   broker    already on the broker
#   migrate   docker, or absent on slim/full, on a hardened install or when this run asks for the agent harness
#             (PIPESHUB_AGENT_HARNESS=on): start the broker, switch once healthy
#   docker    the same under the compatibility profile otherwise, or when the app image cannot run the broker (an
#             image older than it): the app keeps creating sandboxes through the Docker API proxy, as the release it
#             came from did (ADR-OPS-07), even with the harness switch on by default
#   keep      e2b, local or anything else the operator chose: left alone
sandbox_plan() { # args: deploy_type sandbox_mode [hardened] [harness_requested] [image_runs_broker] -> plan
  local deploy_type="$1" mode="$2" hardened="${3:-true}" harness="${4:-false}" broker_ok="${5:-true}"
  if [[ "$deploy_type" == "eval" && ( "$mode" == "__unset__" || -z "$mode" ) ]]; then
    echo disabled; return
  fi
  case "$mode" in
    broker)           echo broker ;;
    docker|__unset__)
      if [[ "$broker_ok" == true && ( "$hardened" == true || "$harness" == true ) ]]; then echo migrate; else echo docker; fi ;;
    "")               echo disabled ;;
    *)                echo keep ;;
  esac
}

# The Compose files this run uses. docker-compose.yml carries SANDBOX_MODE=docker itself (the app reaches the Docker
# API policy proxy on sandbox-control), so docker-compose.sandbox-legacy.yml is no longer added.
COMPOSE_FILES=("${BASE_COMPOSE_FILES[@]}")

# Health of a project service's container: healthy, starting, unhealthy, none
# (no healthcheck) or missing (no container yet).
service_health() { # args: service
  local cid
  cid="$(docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE" ps -q "$1" 2>/dev/null | head -n 1)"
  [[ -n "$cid" ]] || { echo missing; return; }
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo missing
}

wait_service_healthy() { # args: service timeout_secs [poll_secs]
  local service="$1" timeout="$2" poll="${3:-3}" waited=0
  while (( waited < timeout )); do
    [[ "$(service_health "$service")" == "healthy" ]] && return 0
    sleep "$poll"
    waited=$(( waited + poll ))
  done
  [[ "$(service_health "$service")" == "healthy" ]]
}

SANDBOX_BROKER_WAIT_SECS="${SANDBOX_BROKER_WAIT_SECS:-180}"

# SOCKPROXY-1: once the broker serves, no container of the legacy layout may stay up holding the Docker
# socket. Its proxy is the one on the app's sandbox-control network (only the legacy overlay puts it there), whether
# Compose left it as an orphan (a layout that named it differently) or could not recreate it; the broker's front, on
# sandbox_docker, is kept.
remove_legacy_socket_proxies() {
  local proxy
  for proxy in $(docker ps -aq --no-trunc --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
                   --filter "network=${PROJECT_NAME}_sandbox-control" 2>/dev/null); do
    [[ "$(docker inspect --format '{{range .Mounts}}{{.Destination}} {{end}}' "$proxy" 2>/dev/null)" == *"/var/run/docker.sock"* ]] \
      || continue
    # This run's proxy is on sandbox-control too (docker mode lives in the base file); it is the one on sandbox_docker.
    [[ "$(docker inspect --format '{{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}' "$proxy" 2>/dev/null)" \
      == *"${PROJECT_NAME}_sandbox_docker"* ]] && continue
    docker rm -f "$proxy" >/dev/null 2>&1 || true
    info "Removed a legacy Docker API proxy container that still held the Docker socket."
  done
}

# An install on SANDBOX_MODE=docker keeps running code until the broker is
# healthy: start the broker and the egress proxy first, then switch; if the
# broker does not come up (an older pinned image, no socket on this host), the
# app keeps Docker through the legacy overlay and the next run retries.
migrate_sandbox_to_broker() {
  info "Starting the sandbox broker before the app container gives up Docker access..."
  # docker-socket-proxy is named so Compose recreates it from this run's files: the broker's front, not the legacy
  # layout's (root, on the app's network).
  if compose_up docker-socket-proxy sandbox-broker egress-proxy \
      && wait_service_healthy sandbox-broker "$SANDBOX_BROKER_WAIT_SECS"; then
    SANDBOX_MODE=broker
    persist_env_var SANDBOX_MODE broker
    sync_docker_proxy_app_token broker
    remove_legacy_socket_proxies
    success "Sandbox broker is healthy; SANDBOX_MODE=broker. The app container no longer reaches Docker."
    return 0
  fi
  SANDBOX_MODE=docker
  persist_env_var SANDBOX_MODE docker
  sync_docker_proxy_app_token docker
  warn "The sandbox broker did not become healthy within ${SANDBOX_BROKER_WAIT_SECS}s."
  warn "Code execution stays on SANDBOX_MODE=docker (the app creates sandboxes through the Docker API proxy) for now;"
  warn "re-run ./install.sh to retry the switch. Broker logs:"
  warn "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} logs --tail 50 sandbox-broker"
  return 0
}

# gVisor (runsc) gives sandboxes their own kernel. Installed only on a Linux
# Docker Engine this installer can administer; everywhere else sandboxes run on
# hardened runc, which the summary labels as reduced isolation.
# At least 20261005: older runsc, under Docker's systemd cgroup driver, hands block
# I/O limits to systemd as strings it rejects, so no sandbox with a write bound
# (SANDBOX_DATA_DEVICE) starts; from 20261005 it writes io.max itself, as runc does.
GVISOR_MIN_RELEASE=20261005
GVISOR_RELEASE="${PIPESHUB_GVISOR_RELEASE:-20261005}"
GVISOR_BASE_URL="${PIPESHUB_GVISOR_BASE_URL:-https://storage.googleapis.com/gvisor/releases/release}"
GVISOR_BIN_DIR="${PIPESHUB_GVISOR_BIN_DIR:-/usr/local/bin}"
# The SHA-512 of another release's tarball, for whoever sets PIPESHUB_GVISOR_RELEASE.
GVISOR_SHA512="${PIPESHUB_GVISOR_SHA512:-}"
GVISOR_STATE="skip:not_checked"
# Registered with runsc: Docker's seccomp profile then filters each sandbox's syscalls inside gVisor's kernel too
# (defence in depth; without it the guest has no filter of its own and may make user and network namespaces).
GVISOR_RUNTIME_ARGS=(--oci-seccomp)

gvisor_arch() { # args: uname -m -> gVisor's arch name, or empty
  case "$1" in
    x86_64|amd64)  echo x86_64 ;;
    aarch64|arm64) echo aarch64 ;;
    *)             echo "" ;;
  esac
}

# The pinned release's checksums: the .sha512 beside the tarball comes from the
# same bucket, so it proves the transfer, not the bytes.
gvisor_sha512() { # args: release arch -> the tarball's SHA-512, or empty
  if [[ -n "$GVISOR_SHA512" ]]; then echo "$GVISOR_SHA512"; return; fi
  case "$1/$2" in
    20261005/x86_64)  echo 79869ae9a589355a46d46fdac068d9a954e741741ffe77c64e38c56ea955498c1cb04e9bce44e1cce5155333e6b76bee72347c6661bb7c78fd2fe3aad198c8fe ;;
    20261005/aarch64) echo 1bc58be6f881264ced1df8ff61db0205531abedcace005585ac5637c5c987831062b365f99f270ef0939849f130344b84e0f1a663294406f798b8f7c8cd326ac ;;
  esac
}

# The runsc entry's runtimeArgs in `docker info`'s Runtimes JSON, one per line.
runsc_runtime_args() { # args: runtimes_json
  local listed
  listed="$(printf '%s' "$1" | sed -n 's/.*"runsc":{[^{}]*"runtimeArgs":\[\([^]]*\)\].*/\1/p')"
  [[ -n "$listed" ]] || return 0
  printf '%s\n' "$listed" | tr ',' '\n' | sed 's/^ *"//; s/" *$//'
}

# The runsc binary the daemon runs, from the same JSON.
runsc_runtime_path() { # args: runtimes_json
  printf '%s' "$1" | sed -n 's/.*"runsc":{[^{}]*"path":"\([^"]*\)".*/\1/p'
}

# Whether the daemon's runsc carries --oci-seccomp, as Go reads its flags: a
# bare flag or =true|1|t is on, =false|0|f is off, and the last one wins.
runsc_filters_syscalls() { # args: runtimes_json
  local arg on=false
  while IFS= read -r arg; do
    case "$(printf '%s' "$arg" | tr '[:upper:]' '[:lower:]')" in
      -oci-seccomp|--oci-seccomp|-oci-seccomp=true|--oci-seccomp=true|-oci-seccomp=1|--oci-seccomp=1|-oci-seccomp=t|--oci-seccomp=t) on=true ;;
      -oci-seccomp=false|--oci-seccomp=false|-oci-seccomp=0|--oci-seccomp=0|-oci-seccomp=f|--oci-seccomp=f) on=false ;;
    esac
  done < <(runsc_runtime_args "$1")
  [[ "$on" == true ]]
}

# args: choice(auto|skip) sandbox_on(true|false) os(linux|darwin|windows|other)
#       is_wsl(true|false) arch runtimes_json daemon_os security_json docker_host
#       privilege(root|sudo|none)
# -> present | harden (runsc registered without its syscall filter) | install | skip:<reason>
gvisor_plan() {
  local choice="$1" sandbox_on="$2" os="$3" is_wsl="$4" arch="$5" runtimes="$6" \
        daemon_os="$7" security="$8" docker_host="$9" privilege="${10}"
  [[ "$choice" == "skip" ]] && { echo "skip:disabled"; return; }
  [[ "$sandbox_on" == "true" ]] || { echo "skip:sandbox_off"; return; }
  if [[ "$runtimes" == *'"runsc"'* ]]; then
    if runsc_filters_syscalls "$runtimes" || [[ "$privilege" == "none" ]]; then echo present; else echo harden; fi
    return
  fi
  [[ "$os" == "linux" ]] || { echo "skip:not_linux"; return; }
  [[ "$is_wsl" == "true" ]] && { echo "skip:wsl"; return; }
  [[ "$daemon_os" == *"Docker Desktop"* ]] && { echo "skip:docker_desktop"; return; }
  [[ "$security" == *rootless* ]] && { echo "skip:rootless"; return; }
  case "$docker_host" in
    ""|unix://*) ;;
    *) echo "skip:remote_daemon"; return ;;
  esac
  [[ -n "$(gvisor_arch "$arch")" ]] || { echo "skip:arch"; return; }
  [[ "$privilege" == "none" ]] && { echo "skip:no_root"; return; }
  echo install
}

# The release a runsc binary reports ("runsc version release-20261005.0") as
# YYYYMMDD, or empty when it reports none.
runsc_release() { # args: runsc_path
  "$1" --version 2>/dev/null | sed -n 's/^runsc version release-\([0-9]\{8\}\).*/\1/p' | head -n 1
}

# Download the pinned release, verify its checksum and install runsc and its
# containerd shim into GVISOR_BIN_DIR. Binaries are moved into place only after
# the checksum passed; Docker runs the new ones for new containers.
fetch_gvisor() { # args: arch(uname -m) sudo_prefix("" | sudo)
  local arch dir url sum sudo="$2"
  arch="$(gvisor_arch "$1")"
  [[ -n "$arch" ]] || return 1
  if [[ "$GVISOR_RELEASE" =~ ^[0-9]{8} ]] && (( 10#${GVISOR_RELEASE:0:8} < GVISOR_MIN_RELEASE )); then
    warn "gVisor ${GVISOR_RELEASE} is older than ${GVISOR_MIN_RELEASE}, which sandboxes with a disk write bound need. Nothing was installed."
    return 1
  fi
  sum="$(gvisor_sha512 "$GVISOR_RELEASE" "$arch")"
  if [[ -z "$sum" ]]; then
    warn "No pinned checksum for gVisor ${GVISOR_RELEASE} (${arch}); set PIPESHUB_GVISOR_SHA512 to its tarball's SHA-512. Nothing was installed."
    return 1
  fi
  dir="$(mktemp -d)"
  url="${GVISOR_BASE_URL}/${GVISOR_RELEASE}/${arch}"
  if ! curl -fsSL -o "${dir}/gvisor.tar.bz2" "${url}/gvisor.tar.bz2"; then
    warn "Could not download gVisor ${GVISOR_RELEASE} (${arch})."
    rm -rf "$dir"; return 1
  fi
  if ! printf '%s  %s\n' "$sum" "${dir}/gvisor.tar.bz2" | sha512sum -c --status - 2>/dev/null; then
    warn "gVisor download failed its checksum; nothing was installed."
    rm -rf "$dir"; return 1
  fi
  if ! tar -xjf "${dir}/gvisor.tar.bz2" -C "$dir" runsc containerd-shim-runsc-v1; then
    warn "Could not unpack gVisor (needs tar and bzip2); nothing was installed."
    rm -rf "$dir"; return 1
  fi
  if ! $sudo install -m 0755 "${dir}/runsc" "${dir}/containerd-shim-runsc-v1" "${GVISOR_BIN_DIR}/"; then
    warn "Could not install gVisor into ${GVISOR_BIN_DIR}."
    rm -rf "$dir"; return 1
  fi
  rm -rf "$dir"
}

# fetch_gvisor, then register the runtime in /etc/docker/daemon.json (runsc
# install is idempotent) and reload the daemon (running containers keep
# running).
install_gvisor() { # args: arch(uname -m) sudo_prefix("" | sudo)
  local sudo="$2"
  fetch_gvisor "$1" "$sudo" || return 1
  if ! $sudo "${GVISOR_BIN_DIR}/runsc" install -- "${GVISOR_RUNTIME_ARGS[@]}" >/dev/null 2>&1; then
    warn "runsc is installed but could not register itself in /etc/docker/daemon.json."
    return 1
  fi
  if command -v systemctl >/dev/null 2>&1; then
    $sudo systemctl reload docker >/dev/null 2>&1 || true
  fi
  if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'; then
    warn "runsc is registered, but Docker does not list it yet: restart Docker to pick it up (sudo systemctl restart docker)."
    return 1
  fi
}

# A registered runsc older than GVISOR_MIN_RELEASE cannot start a sandbox with a
# disk write bound under Docker's systemd cgroup driver. The runsc this installer
# put in GVISOR_BIN_DIR is replaced in place with the pinned release, its
# registration unchanged; one installed some other way is left to its owner.
refresh_gvisor() { # args: runtimes_json sudo_prefix("" | sudo) privilege(root|sudo|none)
  local runtimes="$1" sudo="$2" privilege="$3" path release
  local why="older runsc cannot start a sandbox with a disk write bound (SANDBOX_DATA_DEVICE) under Docker's systemd cgroup driver"
  path="$(runsc_runtime_path "$runtimes")"
  [[ -n "$path" ]] || path="$(command -v runsc || true)"
  [[ -n "$path" ]] || return 0
  release="$(runsc_release "$path")"
  if [[ -n "$release" ]] && (( 10#$release >= GVISOR_MIN_RELEASE )); then return 0; fi
  if [[ "$path" != "${GVISOR_BIN_DIR%/}/runsc" ]]; then
    warn "Docker's runsc (${path}) is release ${release:-unknown}; gVisor ${GVISOR_MIN_RELEASE} or later is needed: ${why}. It was not installed by this script, so it is left as is: upgrade it, then re-run ./install.sh."
    return 1
  fi
  if [[ "$privilege" == "none" ]]; then
    warn "runsc in ${GVISOR_BIN_DIR} is release ${release:-unknown}; gVisor ${GVISOR_MIN_RELEASE} or later is needed: ${why}. Re-run ./install.sh with sudo to upgrade it."
    return 1
  fi
  info "Upgrading gVisor ${release:-of an unknown release} in ${GVISOR_BIN_DIR} to ${GVISOR_RELEASE}..."
  fetch_gvisor "$ARCH" "$sudo" || return 1
  success "gVisor upgraded to ${GVISOR_RELEASE}; new sandboxes run on it."
}

# Re-registers an installed runsc with its syscall filter, keeping the
# arguments it already has, and reloads Docker: a reload applies the new
# runtime arguments to new containers and leaves running ones alone.
harden_gvisor() { # args: runtimes_json sudo_prefix("" | sudo)
  local runtimes="$1" sudo="$2" path arg
  local args=()
  path="$(runsc_runtime_path "$runtimes")"
  [[ -n "$path" ]] || path="$(command -v runsc || true)"
  [[ -n "$path" ]] || { warn "Could not find the runsc Docker runs."; return 1; }
  while IFS= read -r arg; do
    case "$arg" in -oci-seccomp*|--oci-seccomp*|"") ;; *) args+=("$arg") ;; esac
  done < <(runsc_runtime_args "$runtimes")
  if ! $sudo "$path" install --runtime=runsc -- ${args[@]+"${args[@]}"} "${GVISOR_RUNTIME_ARGS[@]}" >/dev/null 2>&1; then
    warn "Could not register runsc with its syscall filter in /etc/docker/daemon.json."
    return 1
  fi
  if command -v systemctl >/dev/null 2>&1; then
    $sudo systemctl reload docker >/dev/null 2>&1 || true
  fi
  if ! runsc_filters_syscalls "$(docker info --format '{{json .Runtimes}}' 2>/dev/null || true)"; then
    warn "runsc is registered with --oci-seccomp, but Docker has not picked it up yet: reload Docker (sudo systemctl reload docker)."
    return 1
  fi
}

# Gathers the host facts for gvisor_plan, asks when interactive, installs.
# Never fails the install: without gVisor sandboxes run on hardened runc.
ensure_gvisor() { # args: sandbox_on(true|false)
  local os=other privilege=none sudo="" plan runtimes choice="${PIPESHUB_GVISOR:-auto}"
  $FLAG_INSTALL_GVISOR && choice=install
  $FLAG_SKIP_GVISOR && choice=skip
  if $IS_LINUX; then os=linux; elif $IS_MACOS; then os=darwin; elif $IS_WINDOWS; then os=windows; fi
  if [[ $EUID -eq 0 ]]; then
    privilege=root
  elif command -v sudo >/dev/null 2>&1 && { sudo -n true 2>/dev/null || ! $FLAG_YES; }; then
    privilege=sudo; sudo=sudo
  fi
  runtimes="$(docker info --format '{{json .Runtimes}}' 2>/dev/null || true)"
  plan="$(gvisor_plan "$choice" "$1" "$os" "$IS_WSL" "$ARCH" "$runtimes" \
    "$(docker info --format '{{.OperatingSystem}}' 2>/dev/null || true)" \
    "$(docker info --format '{{json .SecurityOptions}}' 2>/dev/null || true)" \
    "${DOCKER_HOST:-}" "$privilege")"
  case "$plan" in
    present|harden) refresh_gvisor "$runtimes" "$sudo" "$privilege" || true ;;
  esac
  case "$plan" in
    present)
      GVISOR_STATE=present
      runsc_filters_syscalls "$runtimes" || GVISOR_STATE="present:unfiltered"
      ;;
    harden)
      GVISOR_STATE="present:unfiltered"
      # The same host-wide change as an install (daemon.json, a Docker reload): an explicit yes only.
      if $FLAG_YES && [[ "$choice" != install ]]; then
        info "gVisor runs without its own syscall filter; pass --install-gvisor to register runsc with --oci-seccomp."
        return 0
      fi
      if ! $FLAG_YES && [[ "$choice" != install ]]; then
        printf '\n  %sTurn on gVisor'"'"'s own syscall filter (runsc --oci-seccomp; reloads Docker, running containers keep running)?%s [Y/n]: ' "$BOLD" "$RESET"
        read -r _gvisor_reply
        [[ "${_gvisor_reply:-Y}" =~ ^[Yy] ]] || return 0
      fi
      info "Registering runsc with --oci-seccomp..."
      if harden_gvisor "$runtimes" "$sudo"; then
        GVISOR_STATE=present
        success "gVisor now filters each sandbox's syscalls with Docker's seccomp profile too."
      fi
      ;;
    install)
      # Installing edits /etc/docker/daemon.json and reloads the host's Docker: a
      # change outside this stack that --yes (accept the defaults) must not make.
      if $FLAG_YES && [[ "$choice" != install ]]; then
        GVISOR_STATE="skip:not_requested"
        info "gVisor not installed (non-interactive); pass --install-gvisor to install it. Sandboxes use hardened runc."
        return 0
      fi
      if ! $FLAG_YES && [[ "$choice" != install ]]; then
        printf '\n  %sInstall gVisor (runsc) so code sandboxes get their own kernel?%s [Y/n]: ' "$BOLD" "$RESET"
        read -r _gvisor_reply
        [[ "${_gvisor_reply:-Y}" =~ ^[Yy] ]] || { GVISOR_STATE="skip:declined"; return 0; }
      fi
      info "Installing gVisor ${GVISOR_RELEASE} and registering the runsc runtime with Docker..."
      if install_gvisor "$ARCH" "$sudo"; then
        GVISOR_STATE=present
        success "gVisor installed; the sandbox broker will run sandboxes on runsc."
      else
        GVISOR_STATE="skip:install_failed"
      fi
      ;;
    *) GVISOR_STATE="$plan" ;;
  esac
}

# The isolation sandboxes get on this host, for the summary.
sandbox_isolation_label() { # args: plan gvisor_state
  case "$1" in
    disabled) echo "off (run_code unavailable)"; return ;;
    keep)     echo "SANDBOX_MODE=${SANDBOX_MODE:-} (unchanged)"; return ;;
  esac
  if [[ "$2" == "present" ]]; then
    echo "gVisor (runsc) via the sandbox broker"
  elif [[ "$2" == "present:unfiltered" ]]; then
    echo "gVisor (runsc) via the sandbox broker, without its own syscall filter (see GVISOR.md, \"Syscall filter\")"
  else
    echo "hardened Docker (runc) via the sandbox broker: reduced isolation, shared host kernel"
  fi
}

# Before the main `up`, with the app image current: gVisor where it applies,
# then the broker switch for an install still on docker.
prepare_sandbox_plane() {
  case "${SANDBOX_PLAN:-}" in
    broker|migrate)
      ensure_gvisor true
      if [[ "$SANDBOX_PLAN" == "migrate" ]]; then
        migrate_sandbox_to_broker
      fi
      info "Code isolation: $(sandbox_isolation_label "$SANDBOX_PLAN" "$GVISOR_STATE")"
      ;;
    *) info "Code isolation: $(sandbox_isolation_label "${SANDBOX_PLAN:-}" "")" ;;
  esac
}

# List working directories of RUNNING containers in a Compose project that were
# launched from a directory other than this one. Compose stamps each container
# with com.docker.compose.project.working_dir. The same project name shares
# volumes; launching from here manages that stack unless the user picks a new name.
compose_other_working_dirs() {
  local project="${1:-$PROJECT_NAME}"
  docker ps \
    --filter "label=com.docker.compose.project=${project}" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
    | grep -v '^$' | sort -u | grep -vxF "$SCRIPT_DIR" || true
}

# Compose project names: lowercase letter or digit, then [a-z0-9_-]*.
valid_compose_project_name() {
  [[ "$1" =~ ^[a-z0-9][a-z0-9_-]*$ ]]
}

sanitize_compose_project_name() {
  local raw
  raw="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9_-' '-')"
  while [[ "$raw" == -* || "$raw" == _* ]]; do
    raw="${raw#-}"
    raw="${raw#_}"
  done
  raw="${raw%-}"
  raw="${raw%_}"
  printf '%s' "$raw"
}

# Default name for a second copy: this directory, or the repo root, or pipeshub-2.
suggest_separate_project_name() {
  local base candidate
  candidate="$(basename "$SCRIPT_DIR")"
  base="$(sanitize_compose_project_name "$candidate")"
  if [[ -z "$base" || "$base" == "docker-compose" || "$base" == "$DEFAULT_PROJECT" ]]; then
    if [[ -f "${SCRIPT_DIR}/../../Dockerfile" ]]; then
      candidate="$(basename "$(cd "${SCRIPT_DIR}/../.." && pwd)")"
      base="$(sanitize_compose_project_name "$candidate")"
    fi
  fi
  if [[ -z "$base" || "$base" == "$DEFAULT_PROJECT" || "$base" == "docker-compose" ]] \
      || ! valid_compose_project_name "$base"; then
    base="pipeshub-2"
  fi
  printf '%s' "$base"
}

# Where to cd before ./install.sh after a successful install.
# Dockerfile + install.sh two levels up is not enough: any containerized app
# can look like that, including a copy of this installer at
# <project>/deployment/docker-compose. Only treat as clone when the root
# install.sh is the PipesHub wrapper (the thing clone users should re-run).
# Sets BANNER_IN_CLONE (true|false) and BANNER_CLI_DIR.
resolve_banner_dirs() {
  local clone_root="" compose_in_clone=""
  BANNER_IN_CLONE=false
  BANNER_CLI_DIR="$SCRIPT_DIR"

  if [[ -f "${SCRIPT_DIR}/../../Dockerfile" && -f "${SCRIPT_DIR}/../../install.sh" ]]; then
    clone_root="$(cd "${SCRIPT_DIR}/../.." && pwd)" || true
    if [[ -n "$clone_root" ]] \
        && grep -q 'INNER_SUBPATH="deployment/docker-compose"' "${clone_root}/install.sh" 2>/dev/null; then
      # Missing compose dir must not abort: this runs after the stack is up.
      compose_in_clone="$(cd "${clone_root}/deployment/docker-compose" 2>/dev/null && pwd)" || true
      if [[ -n "$compose_in_clone" && "$SCRIPT_DIR" == "$compose_in_clone" ]]; then
        BANNER_IN_CLONE=true
        BANNER_CLI_DIR="$clone_root"
      fi
    fi
  fi
}

# Compose --progress tty|plain from a TTY flag. Keep this explicit instead of
# `--progress auto`: auto is the same split inside Compose, but would re-detect
# independently of the health-wait spinner. One _is_tty drives both.
resolve_compose_progress() { # args: is_tty (true|false) -> tty|plain
  [[ "$1" == true ]] && { echo tty; return; }
  echo plain
}

# PIPESHUB_PROJECT wins. Else COMPOSE_PROJECT_NAME in this directory's .env so
# --stop / --uninstall only tear down this copy. Else the default name.
resolve_project_name() {
  if [[ -n "${PIPESHUB_PROJECT:-}" ]]; then
    printf '%s' "$PIPESHUB_PROJECT"
    return
  fi
  local from_env=""
  if [[ -f "${ENV_FILE:-}" ]]; then
    from_env="$(get_existing_val COMPOSE_PROJECT_NAME "")"
  fi
  if [[ -n "$from_env" ]]; then
    printf '%s' "$from_env"
    return
  fi
  printf '%s' "${DEFAULT_PROJECT:-pipeshub-ai}"
}

require_valid_project_name() {
  valid_compose_project_name "$1" || die "Compose project name must be lowercase letters, digits, hyphens, or underscores, starting with a letter or digit (got: $1)."
}

# True when this project still has the old pinned container_name values
# (pipeshub-ai, mongodb, …). The next compose up recreates them as
# {project}-{service}-1.
project_has_pinned_container_names() {
  docker ps -a \
    --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
    --format '{{.Names}}' 2>/dev/null \
    | grep -qxE 'pipeshub-ai|mongodb|redis|qdrant|sandbox-image|arango|neo4j|etcd|zookeeper|kafka-1'
}

warn_pinned_container_rename() {
  info "Containers are being renamed to ${PROJECT_NAME}-<service>-1 (Compose default)."
  info "Data volumes are unchanged. From now on use:"
  info "  docker compose -p ${PROJECT_NAME} exec -T pipeshub-ai …"
  info "  docker compose -p ${PROJECT_NAME} logs -f pipeshub-ai"
  info "Replace docker exec / docker logs of the old container name in any scripts or runbooks."
  if docker network ls --format '{{.Name}}' 2>/dev/null | grep -qx 'pipeshub-ai_network'; then
    info "The old network pipeshub-ai_network may remain unused after this start. Remove it with: docker network rm pipeshub-ai_network"
  fi
}

# Exec in the app service (Compose name, not a pinned container_name).
compose_app_exec() {
  docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE" exec -T pipeshub-ai "$@"
}

app_service_is_running() {
  docker ps \
    --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
    --filter "label=com.docker.compose.service=pipeshub-ai" \
    --format '{{.ID}}' 2>/dev/null | grep -q .
}

# Return 0 if a RUNNING container in our project already publishes the given host
# port (i.e. the port is "in use" by our own stack, so a restart is fine).
port_owned_by_project() {
  local port="$1"
  docker ps \
    --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
    --format '{{.Ports}}' 2>/dev/null | grep -q ":${port}->"
}

# Check if a Docker volume (by exact name) exists.
volume_exists() {
  # an exact lookup, not `volume ls | grep -q`: under pipefail grep's early exit can SIGPIPE docker and read as "absent"
  docker volume inspect "$1" >/dev/null 2>&1
}

# Check if a TCP port is bound on localhost.
# Priority:
#   1. ss (iproute2) — server-side listen check; present on all modern Linux,
#      including hardened builds where /dev/tcp is compiled out.
#   2. bash /dev/tcp — fast, zero-dependency; requires --enable-net-redirections
#      (absent in macOS system bash 3.2 and some hardened Linux builds).
#   3. nc -z — connection probe; ships on macOS and most Linux distros.
# If none of the above can determine state, assume free; Docker will surface a
# clear bind error if the port is actually taken.
port_in_use() {
  local port="$1"
  # ss: Linux (iproute2) — server-side listening check, most reliable
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | grep -q ":${port}\b" && return 0
    return 1
  fi
  # bash /dev/tcp: GNU/Linux bash, Homebrew bash on macOS
  ( : <>/dev/tcp/127.0.0.1/"$port" ) 2>/dev/null && return 0
  # nc -z: macOS system bash, Git Bash, Alpine, BusyBox
  if command -v nc >/dev/null 2>&1; then
    nc -z 127.0.0.1 "$port" 2>/dev/null && return 0
  fi
  return 1
}

# Return 0 if $1 >= $2 as semver. Uses sort -V (GNU coreutils / macOS Ventura+).
# NOTE: sort -V is not POSIX; on unsupported systems this silently passes — acceptable
# because the version check is warn-only (not a hard requirement).
semver_gte() {
  printf '%s\n%s\n' "$2" "$1" | sort -V -C 2>/dev/null
}

# prompt_choice VAR "Question?" "default" opt1 opt2 ...
prompt_choice() {
  local var="$1" question="$2" default="$3"
  shift 3
  local opts=("$@")
  if $FLAG_YES; then printf -v "$var" '%s' "$default"; return; fi
  printf "\n  ${BOLD}%s${RESET}\n" "$question"
  local i=1
  for opt in "${opts[@]}"; do
    if [[ "$opt" == "$default" ]]; then
      printf "  ${GREEN}[%d] %s (default)${RESET}\n" "$i" "$opt"
    else
      printf "  [%d] %s\n" "$i" "$opt"
    fi
    (( i++ ))
  done
  printf "  Choice [${CYAN}1-%d${RESET}, press Enter for default]: " "${#opts[@]}"
  local reply; read -r reply
  if [[ -z "$reply" ]]; then
    printf -v "$var" '%s' "$default"
  elif [[ "$reply" =~ ^[0-9]+$ ]] && (( reply >= 1 && reply <= ${#opts[@]} )); then
    printf -v "$var" '%s' "${opts[$((reply-1))]}"
  else
    warn "Invalid choice, using default: $default"
    printf -v "$var" '%s' "$default"
  fi
}

# prompt_input VAR "Question?" "default"
prompt_input() {
  local var="$1" question="$2" default="$3"
  if $FLAG_YES; then printf -v "$var" '%s' "$default"; return; fi
  printf "\n  ${BOLD}%s${RESET} [${CYAN}%s${RESET}]: " "$question" "$default"
  local reply; read -r reply
  printf -v "$var" '%s' "${reply:-$default}"
}

# ==============================================================================
# 1. BANNER
# Do not clear the screen: curl | bash has just printed which git ref was
# downloaded, and that line must stay visible above the ASCII banner.
# ==============================================================================
cat <<'BANNER'

  ██████╗ ██╗██████╗ ███████╗███████╗██╗  ██╗██╗   ██╗██████╗
  ██╔══██╗██║██╔══██╗██╔════╝██╔════╝██║  ██║██║   ██║██╔══██╗
  ██████╔╝██║██████╔╝█████╗  ███████╗███████║██║   ██║██████╔╝
  ██╔═══╝ ██║██╔═══╝ ██╔══╝  ╚════██║██╔══██║██║   ██║██╔══██╗
  ██║     ██║██║     ███████╗███████║██║  ██║╚██████╔╝██████╔╝
  ╚═╝     ╚═╝╚═╝     ╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝
                        AI Platform Installer
BANNER
printf "  ${DIM}v%s${RESET}\n" "$INSTALLER_VERSION"
if [[ -n "${PIPESHUB_INSTALL_REF:-}" ]]; then
  printf "  ${DIM}Git ref: %s${RESET}\n" "$PIPESHUB_INSTALL_REF"
fi
printf "\n"

# ==============================================================================
# 2. PRE-FLIGHT CHECKS
# ==============================================================================
header "Pre-flight checks"

# Detect environment
OS_TYPE="$(uname -s)"
ARCH="$(uname -m)"
IS_WSL=false
IS_LINUX=false
IS_MACOS=false
IS_WINDOWS=false

case "$OS_TYPE" in
  Linux*)
    IS_LINUX=true
    if grep -qi microsoft /proc/version 2>/dev/null; then
      IS_WSL=true
      info "Detected Windows Subsystem for Linux (WSL)"
    else
      info "Detected Linux ($ARCH)"
    fi
    ;;
  Darwin*)
    IS_MACOS=true
    info "Detected macOS ($ARCH)"
    ;;
  MINGW*|MSYS*|CYGWIN*)
    IS_WINDOWS=true
    info "Detected Windows / Git Bash"
    warn "Git Bash has limited feature parity; WSL is recommended for Windows."
    ;;
  *)
    warn "Unrecognised OS: $OS_TYPE. Proceeding; some checks may not work."
    ;;
esac

# Docker binary — use --version (no daemon required)
if ! command -v docker >/dev/null 2>&1; then
  die "Docker is not installed. Install it from https://docs.docker.com/get-docker/ and re-run."
fi
DOCKER_VERSION="$(docker --version 2>/dev/null || echo "unknown")"
success "Docker found: $DOCKER_VERSION"

# Docker Compose v2 plugin — use version (no daemon required)
if ! docker compose version >/dev/null 2>&1; then
  die "Docker Compose v2 (plugin) is required. Update Docker Desktop or install the plugin: https://docs.docker.com/compose/install/"
fi
COMPOSE_VERSION="$(docker compose version --short 2>/dev/null || echo "unknown")"
success "Docker Compose found: $COMPOSE_VERSION"

# Require Compose >= 2.20 for depends_on required:false
MIN_COMPOSE="2.20.0"
if [[ "$COMPOSE_VERSION" != "unknown" ]]; then
  if ! semver_gte "$COMPOSE_VERSION" "$MIN_COMPOSE" 2>/dev/null; then
    warn "Docker Compose ${COMPOSE_VERSION} < ${MIN_COMPOSE}: depends_on 'required: false' may not work. Please upgrade Docker."
  fi
fi

# compose.yml must be present
if [[ ! -f "$COMPOSE_FILE" ]]; then
  die "docker-compose.yml not found at $COMPOSE_FILE. Run this script from the deployment/docker-compose/ directory."
fi
success "docker-compose.yml found"

# Docker daemon reachable
if ! docker info >/dev/null 2>&1; then
  if [[ -S /var/run/docker.sock ]]; then
    # Socket exists but inaccessible — user not in docker group (and sudo re-exec didn't help)
    die "Cannot access the Docker socket. Options:
  1. Add your user to the docker group (requires logout/login):
       sudo usermod -aG docker \$USER && newgrp docker
  2. Run this installer as root:
       sudo $0"
  else
    die "Docker daemon is not running.
  Linux:   sudo systemctl start docker
  macOS:   start Docker Desktop
  Windows: start Docker Desktop (or use WSL)"
  fi
fi
success "Docker daemon is running"

# Resolve which Compose project this directory manages (needed for --stop too).
PROJECT_NAME="$(resolve_project_name)"
require_valid_project_name "$PROJECT_NAME"
if [[ "$PROJECT_NAME" != "$DEFAULT_PROJECT" && -z "${PIPESHUB_PORT:-}" ]]; then
  DEFAULT_APP_PORT=3200
fi

# ==============================================================================
# 2b. EARLY-EXIT COMMANDS (--stop, --uninstall)
# These run without resource checks since they operate on an existing deployment.
# ==============================================================================
if $FLAG_STOP; then
  header "Stopping PipesHub"
  if [[ -f "$ENV_FILE" ]]; then set -a; . "$ENV_FILE"; set +a; fi
  PROJECT_NAME="$(resolve_project_name)"
  require_valid_project_name "$PROJECT_NAME"
  # Enable ALL profiles so `down` removes every profile-gated container
  # (ArangoDB, Neo4j, etcd, Kafka/Zookeeper) regardless of which profile this
  # .env currently selects. Otherwise a container started under a different
  # profile stays attached to the network and blocks its removal
  # ("Resource is still in use"). --remove-orphans clears containers left by a
  # previously-active profile too.
  export COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka,sandbox,postgres,postgres-migrate,postgres-metrics"
  docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" down --remove-orphans
  success "PipesHub stopped (project ${PROJECT_NAME}). Data volumes are preserved."
  info "To start again: ./install.sh"
  exit 0
fi

if $FLAG_UNINSTALL; then
  header "Uninstalling PipesHub"
  warn "This will PERMANENTLY DELETE all PipesHub data volumes (database, vectors, files)."
  if ! $FLAG_YES; then
    printf "\n  ${BOLD}%s${RESET} [y/N]: " "Are you absolutely sure?"
    read -r _confirm
    [[ "${_confirm:-N}" =~ ^[Yy]$ ]] || { info "Aborted — nothing was changed."; exit 0; }
  fi
  if [[ -f "$ENV_FILE" ]]; then set -a; . "$ENV_FILE"; set +a; fi
  PROJECT_NAME="$(resolve_project_name)"
  require_valid_project_name "$PROJECT_NAME"
  # Enable ALL profiles so down -v includes every profile-gated service's
  # volume (ArangoDB, Neo4j, etcd, Kafka/Zookeeper) regardless of which
  # profile was active for this deployment.  Without this, volumes from a
  # previously-used profile (e.g. arango_data after switching to neo4j) would
  # be silently left behind.
  export COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka,sandbox,postgres,postgres-migrate,postgres-metrics"
  # `down -v` with the postgres profile would delete the database with everything else: keep it unless asked.
  _keep_pg=false
  _pg_vol=""
  if ! $FLAG_DELETE_POSTGRES; then
    # A failed lookup must not read as "no PostgreSQL volume": down -v would then delete it.
    _pg_vol="$(docker volume ls -q --filter "name=^${PROJECT_NAME}_postgres_data$" 2>&1)" \
      || { error "Could not list Docker volumes (${_pg_vol}). Nothing was changed."; exit 1; }
  fi
  if [[ -n "$_pg_vol" ]]; then
    if $FLAG_YES; then
      _keep_pg=true
      info "Keeping the PostgreSQL data volume; pass --delete-postgres to remove it too."
    else
      printf "\n  ${BOLD}%s${RESET} [y/N]: " "Also delete the PostgreSQL data volume?"
      read -r _confirm_pg
      [[ "${_confirm_pg:-N}" =~ ^[Yy]$ ]] || _keep_pg=true
    fi
  fi
  if $_keep_pg; then
    # Stop and remove every container (all profiles), then every volume of the project except the database's.
    docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" down --remove-orphans
    while IFS= read -r _vol; do
      [[ -z "$_vol" || "$_vol" == "${PROJECT_NAME}_postgres_data" ]] && continue
      docker volume rm "$_vol" >/dev/null || warn "Could not remove volume $_vol"
    done < <(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT_NAME}")
    success "PipesHub stopped and data volumes removed, except PostgreSQL (project ${PROJECT_NAME})."
    exit 0
  fi
  docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" down -v --remove-orphans
  success "PipesHub stopped and all data volumes removed (project ${PROJECT_NAME})."
  exit 0
fi

# An exported metrics password is saved to .env when .env has none: refuse one the server would refuse, before anything is written.
# Kept apart because sourcing a .env with an empty PG_MONITOR_PASSWORD= line below would clear it.
PG_MONITOR_EXPORTED="${PG_MONITOR_PASSWORD:-}"
if [[ -n "$PG_MONITOR_EXPORTED" && -z "$(get_existing_val PG_MONITOR_PASSWORD "")" ]] && ! pg_password_ok "$PG_MONITOR_EXPORTED"; then
  die "PG_MONITOR_PASSWORD may contain only letters, digits and . _ ~ - (it goes into connection URLs). Change or unset the exported value."
fi

# ==============================================================================
# 2c. EXISTING DEPLOYMENT
# Fresh install (nothing running as pipeshub-ai): unchanged, that project, port 3000.
# Default project already running from another directory: update it, start a
# separate named copy here, or abort. --yes never invents a second stack.
# ==============================================================================
_warn_takeover() {
  warn "The same Compose project name shares data volumes, so launching from here"
  warn "manages that stack rather than starting an independent one."
  warn "If this directory has a different .env, its secrets may not match the existing"
  warn "data volumes and can cause database auth failures."
  warn "Recommended: manage PipesHub from one directory, or run --uninstall there first."
}

_OTHER_DIRS="$(compose_other_working_dirs)"
if [[ -n "$_OTHER_DIRS" && -z "${PIPESHUB_PROJECT:-}" && "$PROJECT_NAME" == "$DEFAULT_PROJECT" ]]; then
  header "Existing deployment detected"
  warn "PipesHub (project '${DEFAULT_PROJECT}') is already running from another directory:"
  while IFS= read -r _d; do [[ -n "$_d" ]] && warn "    $_d"; done <<< "$_OTHER_DIRS"
  if $FLAG_YES; then
    _warn_takeover
    warn "Non-interactive (--yes): managing the existing '${PROJECT_NAME}' stack from here."
    warn "To install a separate copy instead: PIPESHUB_PROJECT=my-copy PIPESHUB_PORT=3200 $0 --yes"
  else
    printf "\n  ${BOLD}What do you want to do?${RESET}\n"
    printf "  [1] Update the existing stack (same data, same port)\n"
    printf "  [2] Install a separate instance here (new data, different port)\n"
    printf "  [3] Abort (default)\n"
    printf "  Choice [${CYAN}1-3${RESET}, press Enter to abort]: "
    read -r _reply
    case "${_reply:-3}" in
      1)
        _warn_takeover
        ;;
      2)
        warn "A second instance is a full extra copy (databases and RAM)."
        warn "On a machine that is already running PipesHub, prefer the slim deployment type."
        INSTALL_SEPARATE=true
        DEFAULT_APP_PORT=3200
        _suggest="$(suggest_separate_project_name)"
        while true; do
          prompt_input PROJECT_NAME "Compose project name?" "$_suggest"
          PROJECT_NAME="$(printf '%s' "$PROJECT_NAME" | tr '[:upper:]' '[:lower:]')"
          if ! valid_compose_project_name "$PROJECT_NAME"; then
            warn "Use lowercase letters, digits, hyphens, or underscores, starting with a letter or digit."
            continue
          fi
          if [[ "$PROJECT_NAME" == "$DEFAULT_PROJECT" ]]; then
            warn "That name is the stack that is already running. Pick a different name, or choose [1] to update it."
            continue
          fi
          break
        done
        success "Separate instance project: ${PROJECT_NAME} (default port ${DEFAULT_APP_PORT})"
        ;;
      *)
        die "Aborted to avoid clashing with the deployment in the directory above."
        ;;
    esac
  fi
elif [[ -n "$_OTHER_DIRS" ]]; then
  header "Existing deployment detected"
  warn "PipesHub (project '${PROJECT_NAME}') is already running from another directory:"
  while IFS= read -r _d; do [[ -n "$_d" ]] && warn "    $_d"; done <<< "$_OTHER_DIRS"
  _warn_takeover
  if ! $FLAG_YES; then
    printf "\n  ${BOLD}Continue and manage the existing deployment from here?${RESET} [y/N]: "
    read -r _reply
    [[ "${_reply:-N}" =~ ^[Yy]$ ]] || die "Aborted to avoid clashing with the deployment in the directory above."
  fi
fi

# ==============================================================================
# 3. RESOURCE CHECKS (skip for --upgrade / --rotate-signing-secrets; resources
# are already allocated)
# ==============================================================================
if ! $FLAG_UPGRADE && ! $FLAG_ROTATE_SIGNING_SECRETS; then

  # System RAM — 16 GB-class machine recommended (15000 MB floor; see below)
  TOTAL_RAM_MB=0
  if $IS_LINUX || $IS_WSL; then
    if [[ -r /proc/meminfo ]]; then
      while IFS=' :' read -r _key _val _unit; do
        if [[ "$_key" == "MemTotal" ]]; then
          TOTAL_RAM_MB=$(( _val / 1024 ))
          break
        fi
      done < /proc/meminfo
    fi
  elif $IS_MACOS; then
    _mem_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
    TOTAL_RAM_MB=$(( _mem_bytes / 1024 / 1024 ))
  fi

  # WSL caps its VM at whatever the user sets in .wslconfig (default ≈ 50–80% of
  # host RAM). 10 GB in the VM is sufficient; requiring 16 GB would block most
  # WSL users even on well-resourced Windows machines.
  #
  # Native Linux/macOS: a machine marketed as "16 GB" reports less than 16384 MB
  # of MemTotal because firmware, the kernel, and (on iGPU systems) shared video
  # memory are reserved before user space sees it — commonly ~15.3–15.7 GiB. Use
  # a 16 GB-class floor (15000 MB) so genuine 16 GB machines are not warned.
  # Eval is an 8 GB-class laptop demo (Neo4j stays; coding sandbox is omitted).
  if [[ "${PIPESHUB_DEPLOY_TYPE:-}" == "eval" ]]; then
    _RAM_MIN_MB=7500
    _RAM_MIN_LABEL="8 GB"
  elif $IS_WSL; then
    _RAM_MIN_MB=10240
    _RAM_MIN_LABEL="10 GB"
  else
    _RAM_MIN_MB=15000
    _RAM_MIN_LABEL="16 GB"
  fi

  if (( TOTAL_RAM_MB > 0 && TOTAL_RAM_MB < _RAM_MIN_MB )); then
    warn "Low RAM: ${TOTAL_RAM_MB} MB detected. PipesHub recommends a ${_RAM_MIN_LABEL}-class machine."
    warn "Slim and full want ~16 GB. Eval (PIPESHUB_DEPLOY_TYPE=eval) is the 8 GB-class laptop demo."
    if ! $FLAG_YES; then
      printf "\n  ${BOLD}Proceed with installation anyway?${RESET} [y/N]: "
      read -r _proceed
      [[ "${_proceed:-N}" =~ ^[Yy]$ ]] || die "Installation aborted due to insufficient RAM."
    fi
  elif (( TOTAL_RAM_MB >= _RAM_MIN_MB )); then
    success "System RAM: ${TOTAL_RAM_MB} MB"
  fi

  # Docker-allocated RAM check — only relevant on macOS where Docker Desktop runs
  # a Linux VM. On native Linux, docker info reports host RAM (already checked above).
  # Docker Desktop doesn't need 16 GB; 8 GB in the VM is sufficient for PipesHub.
  if $IS_MACOS; then
    _docker_mem_mb="$(docker_vm_mem_mb)"
    if (( _docker_mem_mb > 0 && _docker_mem_mb < 8192 )); then
      warn "Docker Desktop has only ${_docker_mem_mb} MB allocated to its VM. Recommend at least 8 GB in Docker Desktop → Settings → Resources → Memory."
    fi
  fi

  # Docker Desktop on Windows (Git Bash) — host RAM is not readable from Git Bash,
  # so probe the Docker Desktop VM allocation directly (same approach as macOS).
  if $IS_WINDOWS; then
    _docker_mem_mb="$(docker_vm_mem_mb)"
    if (( _docker_mem_mb > 0 && _docker_mem_mb < 8192 )); then
      warn "Docker Desktop has only ${_docker_mem_mb} MB allocated to its VM. Recommend at least 8 GB in Docker Desktop → Settings → Resources → Memory."
    elif (( _docker_mem_mb >= 8192 )); then
      success "Docker Desktop memory: ${_docker_mem_mb} MB"
    fi
  fi

  # CPU cores — minimum 4 required
  TOTAL_CORES=0
  if $IS_LINUX || $IS_WSL; then
    TOTAL_CORES="$(grep -c '^processor' /proc/cpuinfo 2>/dev/null || echo 0)"
  elif $IS_MACOS; then
    TOTAL_CORES="$(sysctl -n hw.logicalcpu 2>/dev/null || echo 0)"
  fi

  if (( TOTAL_CORES > 0 && TOTAL_CORES < 4 )); then
    die "Insufficient CPU cores: ${TOTAL_CORES} detected. PipesHub requires at least 4 CPU cores."
  elif (( TOTAL_CORES >= 4 )); then
    success "CPU cores: ${TOTAL_CORES}"
  fi

  # Free disk on Docker data root — warn if < 20 GB
  DOCKER_DATA_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "")"
  if [[ -n "$DOCKER_DATA_ROOT" ]]; then
    FREE_KB=0
    { read -r _ || true; read -r _ _ _ FREE_KB _ || true; } < <(df -Pk "$DOCKER_DATA_ROOT" 2>/dev/null) || true
    FREE_GB=$(( FREE_KB / 1024 / 1024 ))
    if (( FREE_GB < 20 )); then
      warn "Only ${FREE_GB} GB free on ${DOCKER_DATA_ROOT}. Recommend at least 20 GB."
    else
      success "Free disk: ${FREE_GB} GB"
    fi
  fi

fi  # end resource checks

# ==============================================================================
# 4. EXISTING .env HANDLING
# ==============================================================================
header "Configuration"

ENV_EXISTS=false
[[ -f "$ENV_FILE" ]] && ENV_EXISTS=true

# --version / PIPESHUB_VERSION are normally applied inside the configuration
# wizard, which --upgrade skips. Without this, `--upgrade --version X` sources
# the old IMAGE_TAG back out of .env and restarts the very version the user was
# trying to move off, reporting success.
apply_requested_tag() {
  local requested="${CLI_VERSION:-${PIPESHUB_VERSION:-}}"
  [[ -n "$requested" ]] || return 0

  if [[ "$requested" == "${IMAGE_TAG:-}" ]]; then
    info "Already on image tag ${IMAGE_TAG}; re-pulling and restarting."
    return 0
  fi

  info "Image tag: ${IMAGE_TAG:-latest} -> ${requested}"
  IMAGE_TAG="$requested"

  # Persist it so a later plain --upgrade, or a direct `docker compose up`,
  # agrees with what is actually running.
  if grep -qE '^IMAGE_TAG=' "$ENV_FILE"; then
    sed -i.bak -E "s|^IMAGE_TAG=.*|IMAGE_TAG=${IMAGE_TAG}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    printf 'IMAGE_TAG=%s\n' "$IMAGE_TAG" >>"$ENV_FILE"
  fi

  # Move the sandbox image to the new tag only when it is the default
  # pipeshubai image tracking IMAGE_TAG. A value pointing anywhere else — an
  # air-gapped mirror or a private registry — was set deliberately, and its
  # tag is the operator's to manage, so it is left untouched. An empty value
  # is also left alone: compose then derives the image from IMAGE_TAG.
  if [[ "${SANDBOX_DOCKER_IMAGE:-}" == pipeshubai/pipeshub-sandbox:* ]]; then
    SANDBOX_DOCKER_IMAGE="pipeshubai/pipeshub-sandbox:${IMAGE_TAG}"
    if grep -qE '^SANDBOX_DOCKER_IMAGE=' "$ENV_FILE"; then
      sed -i.bak -E "s|^SANDBOX_DOCKER_IMAGE=.*|SANDBOX_DOCKER_IMAGE=${SANDBOX_DOCKER_IMAGE}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
    fi
  fi
}

# --upgrade always reuses the existing .env. --rotate-signing-secrets does too
# unless --reconfigure was also passed (wizard still runs, then we rotate).
if $FLAG_ROTATE_SIGNING_SECRETS && ! $ENV_EXISTS; then
  die ".env not found. Signing-secret rotation requires an existing install."
fi
if $FLAG_UPGRADE; then
  $ENV_EXISTS || die ".env not found. Run ./install.sh (without --upgrade) to set up first."
  info "Upgrade mode — reusing existing .env."
  set -a; . "$ENV_FILE"; set +a
  SKIP_WIZARD=true
  apply_requested_tag
elif $ENV_EXISTS && ! $FLAG_RECONFIGURE && ! $INSTALL_SEPARATE; then
  # .env exists and --reconfigure was not requested: always reuse without prompting.
  # Use --reconfigure to overwrite. A newly chosen separate instance must not
  # inherit the original stack's project name or port from this file.
  info "Existing .env found — reusing. Pass --reconfigure to overwrite."
  set -a; . "$ENV_FILE"; set +a
  SKIP_WIZARD=true
else
  SKIP_WIZARD=false
  if $INSTALL_SEPARATE && $ENV_EXISTS; then
    info "Separate instance — existing .env will be backed up and rewritten."
  fi
fi

# ==============================================================================
# 5–13. CONFIGURATION WIZARD (skipped when reusing existing .env or upgrading)
# ==============================================================================
if ! ${SKIP_WIZARD:-false}; then

  # ── 5. EXISTING VOLUME DETECTION ────────────────────────────────────────────
  header "Existing data detection"

  DETECTED_ARANGO=false; DETECTED_NEO4J=false; DETECTED_ETCD=false; DETECTED_KAFKA=false

  if volume_exists "${PROJECT_NAME}_arango_data"; then
    DETECTED_ARANGO=true
    success "Found existing ArangoDB volume: ${PROJECT_NAME}_arango_data"
  fi
  if volume_exists "${PROJECT_NAME}_neo4j_data"; then
    DETECTED_NEO4J=true
    success "Found existing Neo4j volume: ${PROJECT_NAME}_neo4j_data"
  fi
  if volume_exists "${PROJECT_NAME}_etcd_data"; then
    DETECTED_ETCD=true
    success "Found existing etcd volume: ${PROJECT_NAME}_etcd_data"
  fi
  if volume_exists "${PROJECT_NAME}_kafka_data" || volume_exists "${PROJECT_NAME}_zookeeper_data"; then
    DETECTED_KAFKA=true
    success "Found existing Kafka volume: ${PROJECT_NAME}_kafka_data"
  fi
  if ! $DETECTED_ARANGO && ! $DETECTED_NEO4J; then
    info "No existing graph database volumes found — starting fresh."
  fi

  # ── 6. DEPLOYMENT TYPE ──────────────────────────────────────────────────────
  header "Deployment type"

  printf "\n  ${BOLD}Choose a deployment type:${RESET}\n\n"
  printf "  ${GREEN}[1] Slim${RESET}  — Smaller image (model downloads on first use), fewer containers.\n"
  printf "         Broker: Redis Streams  |  KV store: Redis  |  Graph: Neo4j  |  Sandbox: yes\n"
  printf "         Recommended for: laptops, low-resource servers.\n\n"
  printf "  [2] Full  — Larger image with the embedding model bundled.\n"
  printf "         Broker: Redis Streams (Kafka kept if already installed)  |  KV store: Redis  |  Graph: Neo4j  |  Sandbox: yes\n"
  printf "         Recommended for: production servers, air-gapped deployments.\n\n"
  printf "  [3] Eval  — Slim image without the coding-sandbox pull or Slack bot.\n"
  printf "         Broker: Redis Streams  |  KV store: Redis  |  Graph: Neo4j  |  Sandbox: no\n"
  printf "         Neo4j stays (search ACLs live there). run_code is unavailable.\n"
  printf "         Recommended for: 8 GB-class laptops, MCP search/ask demos.\n\n"

  if [[ -n "${PIPESHUB_DEPLOY_TYPE:-}" ]]; then
    DEPLOY_TYPE="$PIPESHUB_DEPLOY_TYPE"
    info "Using PIPESHUB_DEPLOY_TYPE=$DEPLOY_TYPE"
  else
    prompt_choice DEPLOY_TYPE "Deployment type?" "slim" "slim" "full" "eval"
  fi

  case "$DEPLOY_TYPE" in
    full) DEFAULT_IMAGE_TAG="latest"; DEFAULT_GRAPH="neo4j";  DEFAULT_BROKER="redis"; DEFAULT_KV="redis" ;;
    eval) DEFAULT_IMAGE_TAG="slim";   DEFAULT_GRAPH="neo4j";  DEFAULT_BROKER="redis"; DEFAULT_KV="redis" ;;
    *)    DEPLOY_TYPE="slim"
          DEFAULT_IMAGE_TAG="slim";   DEFAULT_GRAPH="neo4j";  DEFAULT_BROKER="redis"; DEFAULT_KV="redis" ;;
  esac

  # Volume detection overrides graph/kv defaults
  if $DETECTED_ARANGO && ! $DETECTED_NEO4J; then
    DEFAULT_GRAPH="arango"; info "Defaulting graph DB to ArangoDB to reuse existing data volume."
  elif $DETECTED_NEO4J && ! $DETECTED_ARANGO; then
    DEFAULT_GRAPH="neo4j";  info "Defaulting graph DB to Neo4j to reuse existing data volume."
  fi
  if $DETECTED_KAFKA; then
    DEFAULT_BROKER="kafka"; info "Keeping Kafka to reuse the existing broker volume."
  fi
  if $DETECTED_ETCD; then
    DEFAULT_KV="etcd"; info "Defaulting KV store to etcd to reuse existing data volume."
  fi

  if [[ "$DEPLOY_TYPE" == "eval" && "$DEFAULT_GRAPH" != "neo4j" ]]; then
    warn "Eval requires Neo4j (search ACLs live in the graph). Using neo4j instead of $DEFAULT_GRAPH."
    DEFAULT_GRAPH="neo4j"
  fi

  # ── 7. IMAGE SOURCE & VERSION ───────────────────────────────────────────────
  header "Image source & version"

  # Resolve image source: CLI flag > env override > interactive prompt
  if $FLAG_BUILD; then
    IMAGE_SOURCE="local"
  elif [[ "${PIPESHUB_IMAGE_SOURCE:-}" == "local" ]]; then
    IMAGE_SOURCE="local"
    FLAG_BUILD=true
  elif [[ "${PIPESHUB_IMAGE_SOURCE:-}" == "prebuilt" ]]; then
    IMAGE_SOURCE="prebuilt"
  else
    # Interactive — only ask if no env override and not --yes
    IMAGE_SOURCE="prebuilt"
    if ! $FLAG_YES; then
      printf "\n  ${BOLD}Image source:${RESET}\n\n"
      printf "  ${GREEN}[1] Prebuilt${RESET}  — Pull from Docker Hub (recommended, fast).\n"
      printf "  [2] Build from source — Compile locally from the repository\n"
      printf "         (developer / contributor option, takes 10–30+ minutes).\n\n"
      prompt_choice IMAGE_SOURCE "Image source?" "prebuilt" "prebuilt" "local"
      [[ "$IMAGE_SOURCE" == "local" ]] && FLAG_BUILD=true
    fi
  fi

  if [[ "$IMAGE_SOURCE" == "local" ]]; then
    # Verify the Dockerfile is reachable (repo root is two levels up from the compose dir)
    REPO_ROOT="$(cd "${SCRIPT_DIR}/../../" && pwd)"
    [[ ! -f "${REPO_ROOT}/Dockerfile" ]] && \
      die "Dockerfile not found at ${REPO_ROOT}/Dockerfile. Run install.sh from deployment/docker-compose/ inside the repository."
    if [[ ! -f "${REPO_ROOT}/deployment/sandbox/Dockerfile" ]]; then
      warn "deployment/sandbox/Dockerfile not found — sandbox container build will fail."
    fi

    # When building locally, IMAGE_TAG is the tag applied to the built image.
    # Default: "local". Can be overridden with --version for versioned dev builds.
    if [[ -n "$CLI_VERSION" ]]; then
      IMAGE_TAG="$CLI_VERSION"
      info "Building from source, image will be tagged: ${APP_IMAGE_REPO}:${IMAGE_TAG}"
    elif [[ -n "${PIPESHUB_VERSION:-}" ]]; then
      IMAGE_TAG="$PIPESHUB_VERSION"
    else
      IMAGE_TAG="local"
      info "Building from source — image will be tagged: ${APP_IMAGE_REPO}:local"
    fi
    SANDBOX_DOCKER_IMAGE="pipeshubai/pipeshub-sandbox:${IMAGE_TAG}"
    warn "First local build can take 10–30+ minutes depending on your machine."

  else
    IMAGE_SOURCE="prebuilt"
    # Empty lets compose.yml default apply: pipeshubai/pipeshub-sandbox:${IMAGE_TAG}. A mirror (an air-gapped
    # install) names its own copy, which a later --upgrade then leaves alone.
    SANDBOX_DOCKER_IMAGE="${PIPESHUB_SANDBOX_IMAGE:-}"
    if [[ -n "$CLI_VERSION" ]]; then
      IMAGE_TAG="$CLI_VERSION"
      info "Using pinned version from --version flag: $IMAGE_TAG"
    elif [[ -n "${PIPESHUB_VERSION:-}" ]]; then
      IMAGE_TAG="$PIPESHUB_VERSION"
      info "Using PIPESHUB_VERSION: $IMAGE_TAG"
    else
      printf "\n  ${BOLD}Image tag:${RESET}\n"
      printf "  - ${GREEN}%s${RESET} (rolling tag — always the latest published release)\n" "$DEFAULT_IMAGE_TAG"
      printf "  - A specific version (e.g. 0.7.0) for reproducible deployments.\n"
      printf "    Available tags: https://hub.docker.com/r/pipeshubai/pipeshub-ai/tags\n\n"
      prompt_input IMAGE_TAG "Image tag to deploy?" "$DEFAULT_IMAGE_TAG"
    fi
  fi

  # ── 8. COMPONENT SELECTION ──────────────────────────────────────────────────
  header "Component selection"

  GRAPH_DB="${PIPESHUB_GRAPH_DB:-}"
  BROKER="${PIPESHUB_BROKER:-}"
  KV_STORE="${PIPESHUB_KV_STORE:-}"

  if [[ -z "$GRAPH_DB" ]] && [[ -z "$BROKER" ]] && [[ -z "$KV_STORE" ]]; then
    if ! $FLAG_YES; then
      printf "\n  ${BOLD}Default configuration for '%s':${RESET}\n" "$DEPLOY_TYPE"
      printf "    Graph DB : %s\n" "$DEFAULT_GRAPH"
      printf "    Broker   : %s\n" "$DEFAULT_BROKER"
      printf "    KV store : %s\n\n" "$DEFAULT_KV"
      printf "  [1] Use defaults (recommended)\n"
      printf "  [2] Customize each component\n"
      printf "  Choice [1]: "
      read -r _cust_reply
      [[ "${_cust_reply:-1}" == "2" ]] && DO_CUSTOMIZE=true || DO_CUSTOMIZE=false
    else
      DO_CUSTOMIZE=false
    fi

    if $DO_CUSTOMIZE; then
      printf "\n  ${BOLD}Graph database:${RESET}\n"
      printf "  neo4j    — graph-first DB, lighter footprint, plugin ecosystem.\n"
      printf "  arango   — multi-model (graph + document + KV), strong OSS edition.\n"
      prompt_choice GRAPH_DB "Graph DB?" "$DEFAULT_GRAPH" "neo4j" "arango"

      printf "\n  ${BOLD}Message broker:${RESET}\n"
      printf "  redis  — Redis Streams; no extra containers, lower overhead.\n"
      printf "  kafka  — Apache Kafka; higher throughput, replay, distributed consumers.\n"
      prompt_choice BROKER "Message broker?" "$DEFAULT_BROKER" "redis" "kafka"

      printf "\n  ${BOLD}Key-value / config store:${RESET}\n"
      printf "  redis — uses the always-on Redis instance; no extra overhead.\n"
      printf "  etcd  — purpose-built distributed config store.\n"
      prompt_choice KV_STORE "KV store?" "$DEFAULT_KV" "redis" "etcd"
    else
      GRAPH_DB="$DEFAULT_GRAPH"; BROKER="$DEFAULT_BROKER"; KV_STORE="$DEFAULT_KV"
    fi
  else
    GRAPH_DB="${GRAPH_DB:-$DEFAULT_GRAPH}"
    BROKER="${BROKER:-$DEFAULT_BROKER}"
    KV_STORE="${KV_STORE:-$DEFAULT_KV}"
    info "Using component overrides: graph=$GRAPH_DB broker=$BROKER kv=$KV_STORE"
  fi

  # The security profile and the harness switch decide defaults below (code execution, the bind address, the key).
  _env_existed="$ENV_EXISTS"
  ${INSTALL_SEPARATE:-false} && _env_existed=false
  PIPESHUB_HARDENED="$(security_profile_choice "$_env_existed")"
  AGENT_HARNESS_WORKER_ENABLED="$(agent_harness_switch)"
  explain_security_profile "$PIPESHUB_HARDENED"

  # ── 9. RESOLVE COMPOSE_PROFILES ─────────────────────────────────────────────
  PROFILES=()
  case "$GRAPH_DB" in
    arango*) PROFILES+=("graph-arango") ;;
    neo4j*)  PROFILES+=("graph-neo4j") ;;
  esac
  [[ "$KV_STORE"  == "etcd"  ]] && PROFILES+=("kv-etcd")
  [[ "$BROKER"    == "kafka" ]] && PROFILES+=("broker-kafka")
  # Code execution. --reconfigure keeps an explicit choice; docker stays docker
  # so the launch below moves it to the broker only once the broker is healthy.
  SANDBOX_MODE="$(get_existing_val SANDBOX_MODE "")"
  SANDBOX_DATA_DEVICE="$(get_existing_val SANDBOX_DATA_DEVICE "$(sandbox_data_device)")"
  if [[ "$DEPLOY_TYPE" == "eval" ]]; then
    SANDBOX_MODE=""
  else
    # Hardened, a fresh install with the harness, or the harness asked for: the broker. Otherwise (an existing .env
    # under the compatibility profile) docker, as the release it comes from ran code.
    case "$SANDBOX_MODE" in
      broker|docker|e2b|local) ;;
      *) if ! image_can sandbox-broker "$(app_image_capabilities)"; then SANDBOX_MODE=docker
         elif [[ "$PIPESHUB_HARDENED" == true || "$(agent_harness_requested)" == true ]] \
           || [[ "$AGENT_HARNESS_WORKER_ENABLED" == true && "$_env_existed" != true ]]; then SANDBOX_MODE=broker
         else SANDBOX_MODE=docker; fi ;;
    esac
  fi
  case "$SANDBOX_MODE" in broker|docker) PROFILES+=("sandbox") ;; esac
  if [[ "$SANDBOX_MODE" == broker ]]; then PROFILES+=("sandbox-broker"); fi
  # --reconfigure rebuilds the profiles from the choices above: the opt-in ones must survive it (the saved override needs them).
  while IFS= read -r _opt; do [[ -n "$_opt" ]] && PROFILES+=("$_opt"); done < <(opt_in_profiles)
  COMPOSE_PROFILES="$(IFS=','; echo "${PROFILES[*]}")"

  case "$GRAPH_DB" in
    arango*) DATA_STORE="arangodb" ;;
    neo4j*)  DATA_STORE="neo4j" ;;
  esac

  # ── 10. PORT SELECTION ──────────────────────────────────────────────────────
  header "Port selection"

  if [[ -n "${PIPESHUB_PORT:-}" ]]; then
    DESIRED_PORT="$PIPESHUB_PORT"
  elif $FLAG_RECONFIGURE && $ENV_EXISTS && ! ${INSTALL_SEPARATE:-false}; then
    # Keep the port this stack already publishes; the scan below would
    # otherwise treat our own listener as busy and walk to DESIRED+1.
    DESIRED_PORT="$(get_existing_val APP_PORT "$DEFAULT_APP_PORT")"
  else
    DESIRED_PORT="$DEFAULT_APP_PORT"
  fi
  if ! $FLAG_YES; then
    prompt_input DESIRED_PORT "Port to expose PipesHub on?" "$DESIRED_PORT"
  fi

  # Validate it's a number
  [[ "$DESIRED_PORT" =~ ^[0-9]+$ ]] || die "Invalid port: $DESIRED_PORT"
  APP_PORT="$DESIRED_PORT"
  MAX_PORT=$(( DESIRED_PORT + 20 ))

  while port_in_use "$APP_PORT" 2>/dev/null && ! port_owned_by_project "$APP_PORT" && (( APP_PORT < MAX_PORT )); do
    warn "Port ${APP_PORT} is in use, trying $(( APP_PORT + 1 ))..."
    APP_PORT=$(( APP_PORT + 1 ))
  done

  if port_in_use "$APP_PORT" 2>/dev/null && ! port_owned_by_project "$APP_PORT"; then
    die "No free port found in range ${DESIRED_PORT}–${MAX_PORT}. Free a port or set PIPESHUB_PORT."
  fi

  if (( APP_PORT != DESIRED_PORT )); then
    info "Port ${DESIRED_PORT} was in use. Using port ${APP_PORT} instead."
  else
    success "Port ${APP_PORT} is available."
  fi

  # ── 11. SECRET GENERATION ───────────────────────────────────────────────────
  header "Generating secrets"

  # Preserve any secrets that already exist in .env so that --reconfigure does
  # not rotate credentials for already-initialised database volumes.
  SECRET_KEY="$(get_existing_val SECRET_KEY "$(gen_secret 32)")"
  SECRET_KEY_PREVIOUS="$(get_existing_val SECRET_KEY_PREVIOUS "")"
  # SEC-D-05: a hardened install moves a weak key to SECRET_KEY_PREVIOUS (still read) and generates a new one.
  secure_secret_key "$PIPESHUB_HARDENED" || true
  # ADR-KEY-02 (SEC-D-03): the egress proxy's and the sandbox broker's own keys. They never hold SECRET_KEY.
  EGRESS_PROXY_COMPONENT_KEY="$(get_existing_val EGRESS_PROXY_COMPONENT_KEY "$(gen_secret 32)")"
  SANDBOX_BROKER_COMPONENT_KEY="$(get_existing_val SANDBOX_BROKER_COMPONENT_KEY "$(gen_secret 32)")"
  # The app's credential at the Docker API proxy (SEC-A-04), shared by the app and the proxy only, and held only in
  # docker mode: on the broker the proxy admits the app to nothing.
  if [[ "$SANDBOX_MODE" == docker ]]; then
    DOCKER_PROXY_TOKEN="$(get_existing_val DOCKER_PROXY_TOKEN "$(gen_secret 32)")"
  else
    DOCKER_PROXY_TOKEN=""
  fi
  # The sandbox broker's credential at the same proxy (R38IDENT-FU-1): the broker holds no Docker socket.
  DOCKER_PROXY_BROKER_TOKEN="$(get_existing_val DOCKER_PROXY_BROKER_TOKEN "$(gen_secret 32)")"
  # Kept across --reconfigure so a previous --rotate-signing-secrets id is not
  # dropped. A new rotate run overwrites it after the wizard writes .env.
  ROTATE_SIGNING_SECRETS="$(get_existing_val ROTATE_SIGNING_SECRETS "")"
  MONGO_USERNAME="$(get_existing_val MONGO_USERNAME "admin")"
  MONGO_PASSWORD="$(get_existing_val MONGO_PASSWORD "$(gen_secret 16)")"
  REDIS_PASSWORD="$(get_existing_val REDIS_PASSWORD "$(gen_secret 16)")"
  QDRANT_API_KEY="$(get_existing_val QDRANT_API_KEY "$(gen_secret 20)")"
  # Generated whatever the broker: Compose refuses to render without it (SEC-D-06), even with the Kafka profile off.
  KAFKA_PASSWORD="$(get_existing_val KAFKA_PASSWORD "$(gen_secret 24)")"
  # PostgreSQL profile (opt-in): kept across --reconfigure like the other credentials.
  PG_MAJOR="$(get_existing_val PG_MAJOR "18")"
  for _pg in ADMIN NODE CONNECTORS INDEXING QUERY KV JOBS MIGRATE BACKUP MONITOR; do
    _pw="$(gen_secret 16)"
    # The metrics login was set by hand before the installer wrote it, so an exported value may be what its role already holds.
    [[ "$_pg" == MONITOR ]] && _pw="${PG_MONITOR_EXPORTED:-$_pw}"
    printf -v "PG_${_pg}_PASSWORD" '%s' "$(get_existing_val "PG_${_pg}_PASSWORD" "$_pw")"
  done
  PG_MONITOR_PASSWORD="$(env_unquote "$PG_MONITOR_PASSWORD")"

  # Optional MongoDB knobs from env.template. The wizard never asks about these,
  # but .env is rewritten in full below, so read back anything the operator set
  # by hand -- otherwise --reconfigure silently reverts their tuning and MongoDB
  # goes back to the defaults that made them set it in the first place.
  MONGO_GLIBC_TUNABLES="$(get_existing_val MONGO_GLIBC_TUNABLES "${MONGO_GLIBC_TUNABLES:-}")"
  MONGO_IMAGE_TAG="$(get_existing_val MONGO_IMAGE_TAG "${MONGO_IMAGE_TAG:-}")"
  MONGO_CACHE_GB="$(get_existing_val MONGO_CACHE_GB "${MONGO_CACHE_GB:-}")"
  MONGO_MEMORY_LIMIT="$(get_existing_val MONGO_MEMORY_LIMIT "${MONGO_MEMORY_LIMIT:-}")"
  MONGO_MEMORY_RESERVATION="$(get_existing_val MONGO_MEMORY_RESERVATION "${MONGO_MEMORY_RESERVATION:-}")"
  # Datastore memory sizing (docs/deployment/vector-db-sizing.md): an operator
  # lowers these on a small machine, so a rewrite must not put the defaults back.
  NEO4J_HEAP_INITIAL="$(get_existing_val NEO4J_HEAP_INITIAL "${NEO4J_HEAP_INITIAL:-}")"
  NEO4J_HEAP_MAX="$(get_existing_val NEO4J_HEAP_MAX "${NEO4J_HEAP_MAX:-}")"
  NEO4J_PAGECACHE="$(get_existing_val NEO4J_PAGECACHE "${NEO4J_PAGECACHE:-}")"
  NEO4J_MEMORY_LIMIT="$(get_existing_val NEO4J_MEMORY_LIMIT "${NEO4J_MEMORY_LIMIT:-}")"
  NEO4J_MEMORY_RESERVATION="$(get_existing_val NEO4J_MEMORY_RESERVATION "${NEO4J_MEMORY_RESERVATION:-}")"
  QDRANT_MEMORY_RESERVATION="$(get_existing_val QDRANT_MEMORY_RESERVATION "${QDRANT_MEMORY_RESERVATION:-}")"
  PG_MEMORY_LIMIT="$(get_existing_val PG_MEMORY_LIMIT "${PG_MEMORY_LIMIT:-}")"
  PG_SHARED_BUFFERS="$(get_existing_val PG_SHARED_BUFFERS "${PG_SHARED_BUFFERS:-}")"
  PG_SHM="$(get_existing_val PG_SHM "${PG_SHM:-}")"
  # Qdrant's memory limit decides search speed once the index grows
  # (docs/deployment/vector-db-sizing.md); keep an operator's value.
  QDRANT_MEMORY_LIMIT="$(get_existing_val QDRANT_MEMORY_LIMIT "${QDRANT_MEMORY_LIMIT:-}")"

  # Same for the private-address switch: dropping it on --reconfigure would let AI
  # model endpoints reach internal addresses again without anyone noticing.
  PIPESHUB_BLOCK_PRIVATE_ADDRESSES="$(get_existing_val PIPESHUB_BLOCK_PRIVATE_ADDRESSES "${PIPESHUB_BLOCK_PRIVATE_ADDRESSES:-}")"

  # App container memory. An explicit APP_MEMORY_LIMIT always wins -- from the
  # environment for scripted installs, or from an existing .env so
  # --reconfigure never silently resizes a tuned deployment. Only a fresh
  # install with neither derives from what this machine actually has, so a
  # 32 GB host stops being held to the 10 GB default while a small one is not
  # over-committed into the OOM/thrash the governor then has to brake around.
  APP_MEMORY_LIMIT="$(get_existing_val APP_MEMORY_LIMIT "${APP_MEMORY_LIMIT:-}")"
  APP_MEMSWAP_LIMIT="$(get_existing_val APP_MEMSWAP_LIMIT "${APP_MEMSWAP_LIMIT:-}")"
  if [[ -z "$APP_MEMORY_LIMIT" ]]; then
    _derived_app_mem_mb="$(recommended_app_memory_mb)"
    if [[ -n "$_derived_app_mem_mb" ]] && (( _derived_app_mem_mb > 0 )); then
      APP_MEMORY_LIMIT="$(( _derived_app_mem_mb / 1024 ))G"
      info "Sizing app container memory: APP_MEMORY_LIMIT=${APP_MEMORY_LIMIT} (reserving $(( _SIBLING_MEMORY_RESERVE_MB / 1024 ))G for the datastores and OS). Override by setting APP_MEMORY_LIMIT before running, or edit .env."
      if [[ -z "$APP_MEMSWAP_LIMIT" ]]; then
        APP_MEMSWAP_LIMIT="$(( (_derived_app_mem_mb + _APP_SWAP_HEADROOM_MB) / 1024 ))G"
      fi
    fi
  fi

  # The Docker socket's group as the Docker API proxy sees it (docker_socket_gid): the proxy joins that
  # group instead of running as root (SEC-D-02). It is read fresh on every run.
  DOCKER_GID="$(docker_socket_gid)"

  if [[ "$DATA_STORE" == "arangodb" ]]; then
    ARANGO_PASSWORD="$(get_existing_val ARANGO_PASSWORD "$(gen_secret 16)")"; NEO4J_PASSWORD=""
  else
    NEO4J_PASSWORD="$(get_existing_val NEO4J_PASSWORD "$(gen_secret 16)")";  ARANGO_PASSWORD=""
  fi

  success "Secrets ready (existing values preserved; new ones generated for any that were missing)."

  # ── 12. PUBLIC URL ──────────────────────────────────────────────────────────
  header "Public URL"

  printf "\n  ${BOLD}Public HTTPS URL${RESET} (optional — required for cloud / external deployments)\n\n"
  printf "  When hosting PipesHub on a server with a public domain name, set this to\n"
  printf "  your HTTPS URL (e.g. https://pipeshub.yourdomain.com). This enables:\n"
  printf "    • OAuth callbacks from Google, Microsoft, Slack, etc.\n"
  printf "    • Webhook notifications from external services\n"
  printf "    • Correct browser security (prevents white-screen on plain HTTP)\n\n"
  printf "  Leave blank for local / localhost-only access.\n\n"

  FRONTEND_PUBLIC_URL="${PIPESHUB_PUBLIC_URL:-}"
  if [[ -z "$FRONTEND_PUBLIC_URL" ]] && ! $FLAG_YES; then
    prompt_input FRONTEND_PUBLIC_URL "Public HTTPS URL?" ""
  fi
  FRONTEND_PUBLIC_URL="${FRONTEND_PUBLIC_URL%/}"  # strip trailing slash

  if [[ -n "$FRONTEND_PUBLIC_URL" ]]; then
    success "Public URL: $FRONTEND_PUBLIC_URL"
  else
    info "No public URL set."
  fi
  if [[ "$PIPESHUB_HARDENED" == true ]]; then
    APP_BIND_ADDRESS="$(app_bind_address "${PIPESHUB_BIND_ADDRESS:-$(get_existing_val APP_BIND_ADDRESS "")}" "$FRONTEND_PUBLIC_URL")"
  else
    # Compatibility: an explicit choice, else unset, which Compose publishes on every interface as before.
    APP_BIND_ADDRESS="${PIPESHUB_BIND_ADDRESS:-$(get_existing_val APP_BIND_ADDRESS "")}"
  fi
  explain_bind_address "${APP_BIND_ADDRESS:-0.0.0.0}" "$APP_PORT" "$FRONTEND_PUBLIC_URL"

  # ── 13. WRITE .env ──────────────────────────────────────────────────────────
  header "Writing .env"

  # Backup existing .env before overwriting. The backup holds the same secrets,
  # so lock it down to owner-only too (don't rely on the caller's umask).
  if [[ -f "$ENV_FILE" ]]; then
    _backup="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$ENV_FILE" "$_backup"
    chmod 600 "$_backup" 2>/dev/null || true
    info "Backed up existing .env to $(basename "$_backup")"
  fi

  cat > "$ENV_FILE" <<ENVFILE
# ======================================================================
# PipesHub AI — generated by install.sh v${INSTALLER_VERSION} on $(date -u '+%Y-%m-%d %H:%M UTC')
# Edit this file to customise the deployment.
# DO NOT commit this file — it contains secrets.
# Re-run install.sh --reconfigure to regenerate.
# ======================================================================

# ── Deployment meta ─────────────────────────────────────────────────────────
DEPLOY_TYPE=${DEPLOY_TYPE}
IMAGE_TAG=${IMAGE_TAG}
# prebuilt = pull from Docker Hub | local = build from source (--build)
IMAGE_SOURCE=${IMAGE_SOURCE}
# Override sandbox image tag for local builds; leave blank to use compose default
SANDBOX_DOCKER_IMAGE=${SANDBOX_DOCKER_IMAGE}
# Eval skips the in-container Slack bot. Slim/full leave this false.
PIPESHUB_SKIP_SLACKBOT=$([ "${DEPLOY_TYPE}" = "eval" ] && echo true || echo false)

# ── Compose project (isolates volumes/network from other copies on this host) ─
COMPOSE_PROJECT_NAME=${PROJECT_NAME}

# ── Compose profiles (controls which optional containers start) ──────────────
# Values: graph-arango | graph-neo4j | kv-etcd | broker-kafka | sandbox  (comma-separated)
COMPOSE_PROFILES=${COMPOSE_PROFILES}

# ── Code execution ──────────────────────────────────────────────────────────
# broker: sandboxes run through the sandbox-broker container, the only one with
# the Docker socket (needs the sandbox profile). Blank: run_code off (eval).
# docker: the app creates sandboxes through the Docker API policy proxy, kept only until the broker is healthy.
SANDBOX_MODE=${SANDBOX_MODE}
# The Docker socket's group id; the Docker API proxy joins it and runs as uid 1000.
DOCKER_GID=${DOCKER_GID}
# The disk under Docker's volumes: the broker caps each sandbox's writes to it (SANDBOX_WRITE_MBPS, default 32 MiB/s).
SANDBOX_DATA_DEVICE=${SANDBOX_DATA_DEVICE}

# ── Core ─────────────────────────────────────────────────────────────────────
NODE_ENV=production
LOG_LEVEL=info
SECRET_KEY=${SECRET_KEY}
# Set only while SECRET_KEY is rotated (guides/runbooks.md "Rotating SECRET_KEY"): read, never written under.
SECRET_KEY_PREVIOUS=${SECRET_KEY_PREVIOUS}
# The egress proxy's and the sandbox broker's own keys (ADR-KEY-02): they hold these instead of SECRET_KEY.
EGRESS_PROXY_COMPONENT_KEY=${EGRESS_PROXY_COMPONENT_KEY}
SANDBOX_BROKER_COMPONENT_KEY=${SANDBOX_BROKER_COMPONENT_KEY}
# The Docker API proxy's caller credentials: the app's (SANDBOX_MODE=docker) and the sandbox broker's.
DOCKER_PROXY_TOKEN=${DOCKER_PROXY_TOKEN}
DOCKER_PROXY_BROKER_TOKEN=${DOCKER_PROXY_BROKER_TOKEN}
# One-shot id for ./install.sh --rotate-signing-secrets. Changing it rotates
# JWT/cookie signing secrets on next app start (logs everyone out).
ROTATE_SIGNING_SECRETS=${ROTATE_SIGNING_SECRETS}

# Public URL — HTTPS domain for cloud/external deployments (leave blank for localhost)
# Required for OAuth callbacks, webhook integrations, and browser security.
# Example: https://pipeshub.yourdomain.com
FRONTEND_PUBLIC_URL=${FRONTEND_PUBLIC_URL}

# Host port PipesHub is exposed on
APP_PORT=${APP_PORT}
# Host address the port is published on (SEC-D-08). 127.0.0.1: this machine only. 0.0.0.0 (or blank, the
# compatibility default): every interface, for an HTTPS proxy or load balancer on another machine (Docker's
# published ports bypass ufw and firewalld).
APP_BIND_ADDRESS=${APP_BIND_ADDRESS}

# ── Graph database ──────────────────────────────────────────────────────────
# DATA_STORE: "arangodb" or "neo4j"
DATA_STORE=${DATA_STORE}

# ArangoDB (active when DATA_STORE=arangodb)
ARANGO_DB_NAME=es
ARANGO_USERNAME=root
ARANGO_PASSWORD=${ARANGO_PASSWORD}

# Neo4j (active when DATA_STORE=neo4j)
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=${NEO4J_PASSWORD}
NEO4J_DATABASE=neo4j
# Neo4j memory. Keep heap max + page cache clearly below the container limit,
# and the initial heap no larger than the maximum.
$(optional_env_line NEO4J_HEAP_INITIAL "$NEO4J_HEAP_INITIAL" "1G")
$(optional_env_line NEO4J_HEAP_MAX "$NEO4J_HEAP_MAX" "2G")
$(optional_env_line NEO4J_PAGECACHE "$NEO4J_PAGECACHE" "1G")
$(optional_env_line NEO4J_MEMORY_LIMIT "$NEO4J_MEMORY_LIMIT" "4G")
$(optional_env_line NEO4J_MEMORY_RESERVATION "$NEO4J_MEMORY_RESERVATION" "2G")

# ── Key-value / config store ─────────────────────────────────────────────────
# "redis" (default, uses always-on Redis) | "etcd" (dedicated container)
KV_STORE_TYPE=${KV_STORE}

# ── Message broker ──────────────────────────────────────────────────────────
# "redis" (Redis Streams, default) | "kafka" (Kafka + Zookeeper)
MESSAGE_BROKER=${BROKER}
REDIS_STREAMS_MAXLEN=500000

# ── Redis ────────────────────────────────────────────────────────────────────
# Required (SEC-D-06): Redis starts only with it, and the app refuses a Redis that answers without one.
REDIS_PASSWORD=${REDIS_PASSWORD}

# ── Kafka (MESSAGE_BROKER=kafka) ─────────────────────────────────────────────
# The SCRAM-SHA-512 login kafka-init writes and every client uses (SEC-D-06). Generated even when Kafka is off.
KAFKA_PASSWORD=${KAFKA_PASSWORD}
# ── PostgreSQL (opt-in profile "postgres"; unused unless it is in COMPOSE_PROFILES) ──
# The major is pinned so --upgrade never swaps it under an existing data directory.
PG_MAJOR=${PG_MAJOR}
PG_ADMIN_PASSWORD=${PG_ADMIN_PASSWORD}
PG_NODE_PASSWORD=${PG_NODE_PASSWORD}
PG_CONNECTORS_PASSWORD=${PG_CONNECTORS_PASSWORD}
PG_INDEXING_PASSWORD=${PG_INDEXING_PASSWORD}
PG_QUERY_PASSWORD=${PG_QUERY_PASSWORD}
PG_KV_PASSWORD=${PG_KV_PASSWORD}
PG_JOBS_PASSWORD=${PG_JOBS_PASSWORD}
PG_MIGRATE_PASSWORD=${PG_MIGRATE_PASSWORD}
PG_BACKUP_PASSWORD=${PG_BACKUP_PASSWORD}
# The login of the postgres-metrics exporters (pg_monitor only, reads no data).
PG_MONITOR_PASSWORD='${PG_MONITOR_PASSWORD}'
# Server memory: the container limit, shared_buffers (about a quarter of the limit) and /dev/shm for parallel index builds.
$(optional_env_line PG_MEMORY_LIMIT "$PG_MEMORY_LIMIT" "4G")
$(optional_env_line PG_SHARED_BUFFERS "$PG_SHARED_BUFFERS" "1GB")
$(optional_env_line PG_SHM "$PG_SHM" "1g")

# ── MongoDB ──────────────────────────────────────────────────────────────────
MONGO_USERNAME=${MONGO_USERNAME}
MONGO_PASSWORD=${MONGO_PASSWORD}
# If MongoDB crash-loops with a segfault (exit 139) on a newer host kernel, try
# the rseq tunable first so the supported MongoDB version can stay pinned.
$(optional_env_line MONGO_GLIBC_TUNABLES "$MONGO_GLIBC_TUNABLES" "glibc.pthread.rseq=1")
# Pin the MongoDB image. The value below is the tag compose already defaults to,
# so uncommenting it changes nothing on its own -- set an older tag here only to
# recover from a version-specific bug. MongoDB 8.x data is not readable by 7.x,
# so wipe the mongo volume before downgrading.
$(optional_env_line MONGO_IMAGE_TAG "$MONGO_IMAGE_TAG" "8.0.17")
# WiredTiger cache cap and container memory limit. Raise both together on
# larger or dedicated hosts; avoid dropping the cache below 1 GB.
$(optional_env_line MONGO_CACHE_GB "$MONGO_CACHE_GB" "1")
$(optional_env_line MONGO_MEMORY_LIMIT "$MONGO_MEMORY_LIMIT" "2G")
$(optional_env_line MONGO_MEMORY_RESERVATION "$MONGO_MEMORY_RESERVATION" "512M")

# App container memory ceiling and its combined memory+swap ceiling. Derived
# from this machine on a fresh install; set them yourself to pin. The governor
# sizes indexing/parsing concurrency from whatever APP_MEMORY_LIMIT allows, so
# raising it raises throughput until CPU becomes the bound. APP_MEMSWAP_LIMIT
# must stay >= APP_MEMORY_LIMIT.
$(optional_env_line APP_MEMORY_LIMIT "$APP_MEMORY_LIMIT" "10G")
$(optional_env_line APP_MEMSWAP_LIMIT "$APP_MEMSWAP_LIMIT" "16G")

# Set to true when organisations that don't trust each other share this deployment:
# AI model endpoints on private or internal addresses are then refused.
$(optional_env_line PIPESHUB_BLOCK_PRIVATE_ADDRESSES "$PIPESHUB_BLOCK_PRIVATE_ADDRESSES" "true")

# ── Qdrant ───────────────────────────────────────────────────────────────────
QDRANT_API_KEY=${QDRANT_API_KEY}
# Qdrant's container memory limit, also given to the app for the Services page
# warning. Search stays fast only while the index fits; size it from
# docs/deployment/vector-db-sizing.md.
$(optional_env_line QDRANT_MEMORY_LIMIT "$QDRANT_MEMORY_LIMIT" "6G")
$(optional_env_line QDRANT_MEMORY_RESERVATION "$QDRANT_MEMORY_RESERVATION" "2G")

# ── Indexing concurrency ─────────────────────────────────────────────────────
# Do not write MAX_CONCURRENT_* / EMBEDDING_*_CONCURRENCY here. Empty values
# crash Hub slim (int("")); omitting them lets slim use built-in defaults and
# lets new images size from CPU. Set an integer in .env only to cap.
# Governor tuning knobs stay empty: each is derived from the container's
# own CPU/memory limits unless pinned.
INDEXING_SPLIT_LEASE_POOLS=
GOVERNOR_HEAVY_PARSE_SLOTS_PER_CPU=
GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU=
GOVERNOR_LIGHT_PARSE_MAX=
GOVERNOR_INDEX_HEADROOM=
GOVERNOR_INDEX_MAX=
GOVERNOR_INDEX_HEAVY_WORKING_SET_GB=
GOVERNOR_INDEX_LIGHT_WORKING_SET_GB=
GOVERNOR_HEAVY_PARSE_WORKING_SET_GB=
# Uncomment to pin the query worker count. Left unset so WEB_CONCURRENCY,
# which uvicorn honours by default, still applies if you use it.
# QUERY_UVICORN_WORKERS=1

INDEXING_UVICORN_WORKERS=1
PARSING_UVICORN_WORKERS=1
DOCLING_UVICORN_WORKERS=1
LOCAL_DOCLING_PARSE_WORKERS=1
PDF_OCR_DETECTION_WORKERS=1

# Query-service runtime tuning. These defaults reproduce current behaviour.
# PIPESHUB_AGENT_TRANSPORT: langchain (default) | direct
PIPESHUB_AGENT_TRANSPORT=langchain
# Accessible-records cache is ON unless set to a disabled value; leave blank to keep it on.
PIPESHUB_ACCESSIBLE_RECORDS_CACHE=
PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL=300
# 0 disables the signed-URL cache. Keep well under the 3600s signing lifetime.
PIPESHUB_SIGNED_URL_CACHE_SECONDS=0
# Most characters one agent turn may stream (text, reasoning and tool-call
# arguments together) before it is stopped as a runaway. Blank keeps the
# built-in 1000000; 0 removes the limit.
PIPESHUB_AGENT_MAX_TURN_OUTPUT_CHARS=

# ── Agent harness ────────────────────────────────────────────────────────────
# The agent harness (Beta) and its switch (ADR-OPS-06), on by default: true starts the worker and its gateways,
# idle until an org admin turns on Agent harness in Labs (off by default). false: no harness process, route, Labs
# flag or UI, and the install runs as a release without it.
AGENT_HARNESS_WORKER_ENABLED=${AGENT_HARNESS_WORKER_ENABLED}

# ── Security profile ─────────────────────────────────────────────────────────
# ADR-OPS-07. true: the hardened defaults (stores require credentials, a weak SECRET_KEY is refused, the CSP is
# enforced, refresh tokens rotate, ...). false: the behaviour of a release without them, for an upgraded install;
# set true and run ./install.sh --upgrade to adopt them (guides/HARNESS-OFF.md lists each behaviour).
PIPESHUB_HARDENED=${PIPESHUB_HARDENED}

# ── ML performance ───────────────────────────────────────────────────────────
# Caps PyTorch / OpenBLAS / MKL thread fan-out per operation.
OMP_NUM_THREADS=2

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_MAX_SIZE=20m
LOG_MAX_FILE=15

# ── Optional integrations ────────────────────────────────────────────────────
SLACK_SIGNING_SECRET=
BOT_TOKEN=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=
OPIK_API_KEY=
OPIK_WORKSPACE=
ENVFILE

  # Restrict to owner read/write — .env holds database passwords and the app
  # secret key in plain text. Guarantee this regardless of the caller's umask
  # (a permissive umask would otherwise leave it world-readable).
  chmod 600 "$ENV_FILE" 2>/dev/null || true

  if [[ -n "${PIPESHUB_IMAGE:-}" ]]; then
    printf 'PIPESHUB_IMAGE=%s\n' "$PIPESHUB_IMAGE" >> "$ENV_FILE"
  fi
  if [[ -n "${PIPESHUB_COMPOSE_OVERRIDES:-}" ]]; then
    printf 'PIPESHUB_COMPOSE_OVERRIDES=%s\n' "$PIPESHUB_COMPOSE_OVERRIDES" >> "$ENV_FILE"
  fi
  if [[ -n "${PIPESHUB_ENV_EXTRA:-}" ]]; then
    [[ -f "$PIPESHUB_ENV_EXTRA" ]] || die "PIPESHUB_ENV_EXTRA: $PIPESHUB_ENV_EXTRA not found"
    # A key the installer already wrote would appear twice and be read back wrong.
    while IFS= read -r _extra_line; do
      _extra_key="${_extra_line%%=*}"
      [[ "$_extra_line" == *=* && "$_extra_key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
      if grep -qE "^${_extra_key}=" "$ENV_FILE"; then
        die "PIPESHUB_ENV_EXTRA sets ${_extra_key}, which the installer already writes; remove it from $PIPESHUB_ENV_EXTRA"
      fi
    done < "$PIPESHUB_ENV_EXTRA"
    { printf '\n# From %s\n' "$PIPESHUB_ENV_EXTRA"; cat "$PIPESHUB_ENV_EXTRA"; } >> "$ENV_FILE"
  fi

  success ".env written to $ENV_FILE (permissions: owner read/write only)"

fi  # end wizard

# Reused .env files from older installs may still be 644. Always lock them
# before we print secrets in the summary or start containers.
if [[ -f "$ENV_FILE" ]] && ! chmod 600 "$ENV_FILE"; then
  die "Could not restrict permissions on $ENV_FILE"
fi

# Keep .env and -p in sync so later --stop / --uninstall only tear down this copy.
persist_env_var "COMPOSE_PROJECT_NAME" "$PROJECT_NAME"
# A .env written before the metrics login existed (reused, or --upgrade) gets its password once: the exported one, if any, as
# pg-roles.sh may have created the role with it; else a new one. A value already in .env is kept. Single-quoted, as in the writer
# above: bash, which reads .env back, expands a leading ~ (an allowed character), and Compose reads quotes as literal.
[[ -n "$(get_existing_val PG_MONITOR_PASSWORD "")" ]] || persist_env_var PG_MONITOR_PASSWORD "'${PG_MONITOR_EXPORTED:-$(gen_secret 16)}'"

# ==============================================================================
# 14. DEPLOYMENT SUMMARY
# ==============================================================================
header "Deployment summary"

# Source .env so all variables are available for display (and for launch)
set -a; . "$ENV_FILE"; set +a

# Docker requires memswap_limit >= memory (or -1 for unlimited swap); if a user
# raises APP_MEMORY_LIMIT without also raising APP_MEMSWAP_LIMIT, that would
# otherwise only surface as a cryptic Docker error at container start. Only
# checked for the G/M-suffixed forms documented in env.template — anything
# else (e.g. -1, plain byte counts) is left to Docker's own validation.
_parse_mem_mb() {
  [[ "$1" =~ ^([0-9]+)[gG]$ ]] && { echo $(( ${BASH_REMATCH[1]} * 1024 )); return; }
  [[ "$1" =~ ^([0-9]+)[mM]$ ]] && { echo "${BASH_REMATCH[1]}"; return; }
  return 1
}
_app_mem_mb="$(_parse_mem_mb "${APP_MEMORY_LIMIT:-10G}")" || _app_mem_mb=""
_app_memswap_mb="$(_parse_mem_mb "${APP_MEMSWAP_LIMIT:-16G}")" || _app_memswap_mb=""
if [[ -n "$_app_mem_mb" && -n "$_app_memswap_mb" ]] && (( _app_memswap_mb < _app_mem_mb )); then
  die "APP_MEMSWAP_LIMIT (${APP_MEMSWAP_LIMIT:-16G}) must be >= APP_MEMORY_LIMIT (${APP_MEMORY_LIMIT:-10G}). Raise APP_MEMSWAP_LIMIT in .env (or raise both together) before launching."
fi
# Resolve APP_PORT from .env when wizard was skipped (upgrade / reuse)
APP_PORT="${APP_PORT:-3000}"

# An .env from before SEC-D-06 / ADR-KEY-02 may lack a credential the hardened profile requires.
if ${SKIP_WIZARD:-false}; then
  # ADR-OPS-07: decided before the heal below writes the marks security_profile_choice reads, and always written
  # down, so an upgraded install says which profile it runs.
  _profile="$(security_profile_choice true)"
  if [[ "$(get_existing_val PIPESHUB_HARDENED "")" != "$_profile" ]]; then
    persist_env_var PIPESHUB_HARDENED "$_profile"
    info "Security profile: PIPESHUB_HARDENED=${_profile} written to ${ENV_FILE}."
  fi
  PIPESHUB_HARDENED="$_profile"
  explain_security_profile "$PIPESHUB_HARDENED"
  SECRET_KEY_PREVIOUS="$(get_existing_val SECRET_KEY_PREVIOUS "")"
  if secure_secret_key "$PIPESHUB_HARDENED"; then
    persist_env_var SECRET_KEY_PREVIOUS "$SECRET_KEY_PREVIOUS"
    persist_env_var SECRET_KEY "$SECRET_KEY"
  fi
  # Read fresh on every run (an .env from before it has none: the proxy could not open the socket).
  _docker_gid="$(docker_socket_gid)"
  if [[ "$(get_existing_val DOCKER_GID "")" != "$_docker_gid" ]]; then
    DOCKER_GID="$_docker_gid"
    persist_env_var DOCKER_GID "$DOCKER_GID"
  fi
  # A reused .env that names the switch is left as it is unless PIPESHUB_AGENT_HARNESS says otherwise.
  _harness_switch="$(agent_harness_switch)"
  if [[ "$(get_existing_val AGENT_HARNESS_WORKER_ENABLED "")" != "$_harness_switch" ]]; then
    AGENT_HARNESS_WORKER_ENABLED="$_harness_switch"
    persist_env_var AGENT_HARNESS_WORKER_ENABLED "$AGENT_HARNESS_WORKER_ENABLED"
    info "Agent harness: AGENT_HARNESS_WORKER_ENABLED=${AGENT_HARNESS_WORKER_ENABLED} written to ${ENV_FILE}."
  fi
  heal_regenerable_credentials
  # An .env from before SEC-D-08 has no bind address; that install published the port on every interface, and keeps
  # doing so under the compatibility profile (Compose reads unset as 0.0.0.0). A hardened install applies the rule a
  # fresh install follows, writes it down, and says what changed and how to open it again.
  if [[ -z "${APP_BIND_ADDRESS:-}" && "$PIPESHUB_HARDENED" != true ]]; then
    if [[ -n "${PIPESHUB_BIND_ADDRESS:-}" ]]; then
      APP_BIND_ADDRESS="$PIPESHUB_BIND_ADDRESS"
      persist_env_var APP_BIND_ADDRESS "$APP_BIND_ADDRESS"
    fi
    explain_bind_address "${APP_BIND_ADDRESS:-0.0.0.0}" "$APP_PORT" "${FRONTEND_PUBLIC_URL:-}"
  elif [[ -z "${APP_BIND_ADDRESS:-}" ]]; then
    APP_BIND_ADDRESS="$(app_bind_address "${PIPESHUB_BIND_ADDRESS:-}" "${FRONTEND_PUBLIC_URL:-}")"
    persist_env_var APP_BIND_ADDRESS "$APP_BIND_ADDRESS"
    if [[ "$APP_BIND_ADDRESS" == "127.0.0.1" ]]; then
      warn "The app port was published on every interface; it is now published on 127.0.0.1 only, since no public"
      warn "URL is set. If other machines reach this install, set APP_BIND_ADDRESS=0.0.0.0 in ${ENV_FILE} (and put"
      warn "HTTPS in front), then run ./install.sh again."
    fi
    explain_bind_address "$APP_BIND_ADDRESS" "$APP_PORT" "${FRONTEND_PUBLIC_URL:-}"
  fi
fi
HEALTH_URL="http://localhost:${APP_PORT}/api/v1/health/services"

# Self-heal a reused/legacy .env that is missing a valid DATA_STORE (older
# installers did not write it). Resolve it the same way the wizard does — from
# existing data volumes, otherwise the product default (Neo4j) — so users are not
# forced into a manual --reconfigure. We only stop when the choice is genuinely
# unsafe: ambiguous data (both DBs have volumes) or an existing volume whose
# password was lost (a fresh password would fail authentication).
case "${DATA_STORE:-}" in
  arangodb|neo4j) ;;   # already valid — nothing to heal
  *)
    _has_arango=false; _has_neo4j=false
    volume_exists "${PROJECT_NAME}_arango_data" && _has_arango=true
    volume_exists "${PROJECT_NAME}_neo4j_data"  && _has_neo4j=true

    if $_has_arango && $_has_neo4j; then
      die "DATA_STORE is unset in ${ENV_FILE}, but data volumes for BOTH graph
  databases exist (${PROJECT_NAME}_arango_data and ${PROJECT_NAME}_neo4j_data).
  Cannot safely choose one. Pick explicitly with:
    ./install.sh --reconfigure
  or set DATA_STORE=arangodb|neo4j in ${ENV_FILE}."
    elif $_has_arango; then
      DATA_STORE="arangodb"
      warn "DATA_STORE was unset; reusing the existing ArangoDB data volume (DATA_STORE=arangodb)."
    elif $_has_neo4j; then
      DATA_STORE="neo4j"
      warn "DATA_STORE was unset; reusing the existing Neo4j data volume (DATA_STORE=neo4j)."
    else
      DATA_STORE="neo4j"
      warn "DATA_STORE was unset; defaulting to Neo4j (no existing graph data found)."
    fi

    # The chosen DB needs a password. Keep an existing one; otherwise generate a
    # fresh one only when there is no volume yet. If a volume already exists but
    # its password is gone, a new one would fail auth — stop and let the user
    # reconfigure or reset.
    if [[ "$DATA_STORE" == "arangodb" ]]; then
      _graph_pw="$(get_existing_val ARANGO_PASSWORD "")"
      if [[ -z "$_graph_pw" ]]; then
        $_has_arango && die "The ArangoDB data volume (${PROJECT_NAME}_arango_data) exists but ARANGO_PASSWORD
  is missing from ${ENV_FILE}; its original password cannot be recovered, and a
  new one would fail authentication against the existing volume. Either:
    - set the known ARANGO_PASSWORD in ${ENV_FILE}, or
    - discard the data and start fresh:  ./install.sh --uninstall
      (or: docker volume rm ${PROJECT_NAME}_arango_data)"
        _graph_pw="$(gen_secret 16)"
      fi
      ARANGO_PASSWORD="$_graph_pw"; persist_env_var ARANGO_PASSWORD "$ARANGO_PASSWORD"
    else
      _graph_pw="$(get_existing_val NEO4J_PASSWORD "")"
      if [[ -z "$_graph_pw" ]]; then
        $_has_neo4j && die "The Neo4j data volume (${PROJECT_NAME}_neo4j_data) exists but NEO4J_PASSWORD
  is missing from ${ENV_FILE}; its original password cannot be recovered, and a
  new one would fail authentication against the existing volume. Either:
    - set the known NEO4J_PASSWORD in ${ENV_FILE}, or
    - discard the data and start fresh:  ./install.sh --uninstall
      (or: docker volume rm ${PROJECT_NAME}_neo4j_data)"
        _graph_pw="$(gen_secret 16)"
      fi
      NEO4J_PASSWORD="$_graph_pw"; persist_env_var NEO4J_PASSWORD "$NEO4J_PASSWORD"
    fi

    persist_env_var DATA_STORE "$DATA_STORE"
    success "Repaired graph DB configuration in .env (DATA_STORE=${DATA_STORE})."
    ;;
esac

# Code execution (D1): an .env from before the sandbox broker has no
# SANDBOX_MODE, which then meant docker with the socket in the app container.
# The harness switch's default does not move an install: only this run's request does (agent_harness_requested).
APP_IMAGE_CAPABILITIES="$(app_image_capabilities)"
_image_runs_broker=false
image_can sandbox-broker "$APP_IMAGE_CAPABILITIES" && _image_runs_broker=true
SANDBOX_PLAN="$(sandbox_plan "${DEPLOY_TYPE:-}" "${SANDBOX_MODE-__unset__}" "${PIPESHUB_HARDENED:-false}" \
  "$(agent_harness_requested)" "$_image_runs_broker")"
if [[ "$_image_runs_broker" != true ]]; then
  case "${SANDBOX_MODE-__unset__}" in
    broker) warn "${APP_IMAGE_REPO}:${IMAGE_TAG:-latest} cannot run the sandbox broker that SANDBOX_MODE=broker names: code execution will not work until the image is upgraded." ;;
    *) [[ "$SANDBOX_PLAN" == docker ]] && info "${APP_IMAGE_REPO}:${IMAGE_TAG:-latest} predates the sandbox broker: code execution stays on SANDBOX_MODE=docker." ;;
  esac
fi
case "$SANDBOX_PLAN" in
  disabled)
    if [[ -n "${SANDBOX_MODE-__unset__}" ]]; then
      SANDBOX_MODE=""
      persist_env_var SANDBOX_MODE ""
    fi
    ;;
  # Not persisted until the broker is healthy (migrate_sandbox_to_broker).
  migrate) SANDBOX_MODE=docker ;;
  # Compatibility: the app keeps creating sandboxes through the Docker API proxy, written down so it is visible.
  docker)
    SANDBOX_MODE=docker
    [[ "$(get_existing_val SANDBOX_MODE "")" == docker ]] || persist_env_var SANDBOX_MODE docker
    if [[ "${AGENT_HARNESS_WORKER_ENABLED:-true}" == true && "$_image_runs_broker" == true ]]; then
      info "Coding engines need the sandbox broker; this install keeps SANDBOX_MODE=docker. To move to the broker, run"
      info "  PIPESHUB_AGENT_HARNESS=on ./install.sh --upgrade   (or PIPESHUB_HARDENED=true ./install.sh --upgrade)"
    fi
    ;;
esac
case "$SANDBOX_PLAN" in
  docker|migrate) sync_docker_proxy_app_token docker ;;
  broker|disabled) sync_docker_proxy_app_token "${SANDBOX_MODE:-}" ;;
esac

# Byte size in Neo4j's setting syntax (plain bytes, or k/m/g/t with an optional
# B or iB suffix, any case; all binary) -> bytes. Returns 1 if not recognised.
_parse_neo4j_bytes() {
  local v n unit
  # tr, not ${1,,}: the installer supports bash 3.2 (macOS), which lacks it.
  v="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  [[ "$v" =~ ^([0-9]+)([kmgt]?)(ib|b)?$ ]] || return 1
  # 10#: a leading zero (010G) would otherwise be read as octal.
  n=$(( 10#${BASH_REMATCH[1]} )); unit="${BASH_REMATCH[2]}"
  case "$unit" in
    k) n=$(( n * 1024 )) ;;
    m) n=$(( n * 1024 ** 2 )) ;;
    g) n=$(( n * 1024 ** 3 )) ;;
    t) n=$(( n * 1024 ** 4 )) ;;
  esac
  echo "$n"
}

# The JVM refuses to start when the initial heap exceeds the maximum, and Neo4j
# would then restart in a loop with only a JVM error in its log. Only Neo4j
# reads these settings, so an ArangoDB deployment is not held to them.
if [[ "$DATA_STORE" == "neo4j" ]]; then
  _neo4j_heap_init="$(_parse_neo4j_bytes "${NEO4J_HEAP_INITIAL:-1G}")" || _neo4j_heap_init=""
  _neo4j_heap_max="$(_parse_neo4j_bytes "${NEO4J_HEAP_MAX:-2G}")" || _neo4j_heap_max=""
  if [[ -z "$_neo4j_heap_init" || -z "$_neo4j_heap_max" ]]; then
    warn "Could not read NEO4J_HEAP_INITIAL (${NEO4J_HEAP_INITIAL:-1G}) or NEO4J_HEAP_MAX (${NEO4J_HEAP_MAX:-2G}) as a size; not checking that the initial heap fits the maximum."
  elif (( _neo4j_heap_init > _neo4j_heap_max )); then
    die "NEO4J_HEAP_INITIAL (${NEO4J_HEAP_INITIAL:-1G}) must be <= NEO4J_HEAP_MAX (${NEO4J_HEAP_MAX:-2G}). Lower NEO4J_HEAP_INITIAL in .env before launching."
  fi
fi

# Repair COMPOSE_PROFILES if it disagrees with the configured services. Without
# this, reusing an .env written by an older installer (or hand-edited) can start
# the app while leaving its graph DB / broker container down, so the health
# check can never pass. Derive the correct set, fix it in memory, and persist it.
_EXPECTED_PROFILES="$(derive_compose_profiles)"
if [[ "${COMPOSE_PROFILES:-}" != "$_EXPECTED_PROFILES" ]]; then
  warn "COMPOSE_PROFILES in .env ('${COMPOSE_PROFILES:-}') does not match the configured services"
  warn "  (DATA_STORE=${DATA_STORE:-unset}, MESSAGE_BROKER=${MESSAGE_BROKER:-unset}, KV_STORE_TYPE=${KV_STORE_TYPE:-unset})."
  warn "Repairing to '${_EXPECTED_PROFILES:-(none)}' so the required containers start."
  COMPOSE_PROFILES="$_EXPECTED_PROFILES"
  persist_env_var "COMPOSE_PROFILES" "$COMPOSE_PROFILES"
fi

# The migration job is built from backend/db/Dockerfile, which only a repository checkout has (a curl-installed directory does not).
if [[ ",${COMPOSE_PROFILES:-}," == *",postgres-migrate,"* && ! -f "${SCRIPT_DIR}/../../backend/db/Dockerfile" ]]; then
  warn "The postgres-migrate profile builds its job image from backend/db/Dockerfile, which is missing here (run from a repository checkout)."
fi

# On reuse/upgrade the wizard's interactive port scan was skipped. Confirm the
# app port is free — or already held by our own stack (a restart) — and otherwise
# fail clearly instead of letting docker emit a cryptic bind error mid-launch.
# Skipped under --print-env-only: that mode resolves and prints the config
# without launching, so whether a port is free is not yet relevant and probing
# it would make a non-launching command fail on the host's unrelated services.
if ${SKIP_WIZARD:-false} && ! $FLAG_PRINT_ENV_ONLY; then
  if port_in_use "$APP_PORT" 2>/dev/null && ! port_owned_by_project "$APP_PORT"; then
    die "Port ${APP_PORT} is already in use by another process.
  Free it, stop the conflicting service, or change APP_PORT in:
    ${ENV_FILE}
  then re-run ./install.sh."
  fi
fi

printf "\n"
printf "  %-22s %s\n" "Compose project:" "$PROJECT_NAME"
printf "  %-22s %s\n" "Image source:"  "${IMAGE_SOURCE:-prebuilt}"
printf "  %-22s %s\n" "Image tag:"     "${IMAGE_TAG:-latest}"
# This override puts every store on PostgreSQL, whatever the selections in .env say.
_pg_only=""
[[ "${PIPESHUB_COMPOSE_OVERRIDES:-}" == *docker-compose.postgres-only.yml* ]] && _pg_only="postgres (docker-compose.postgres-only.yml)"
printf "  %-22s %s\n" "Graph DB:"      "${_pg_only:-${DATA_STORE:-(unset)}}"
printf "  %-22s %s\n" "KV store:"      "${_pg_only:-${KV_STORE_TYPE:-redis}}"
printf "  %-22s %s\n" "Broker:"        "${_pg_only:-${MESSAGE_BROKER:-redis}}"
printf "  %-22s %s\n" "Profiles:"      "${COMPOSE_PROFILES:-(none)}"
printf "  %-22s %s\n" "Code execution:" "$(case "$SANDBOX_PLAN" in migrate) echo "moving to the sandbox broker (from SANDBOX_MODE=docker)" ;; broker) echo "sandbox broker" ;; docker) echo "SANDBOX_MODE=docker (the app, through the Docker API proxy)" ;; *) sandbox_isolation_label "$SANDBOX_PLAN" "" ;; esac)"
printf "  %-22s %s\n" "Security profile:" "$([[ "${PIPESHUB_HARDENED:-false}" == true ]] && echo "hardened (PIPESHUB_HARDENED=true)" || echo "compatibility (PIPESHUB_HARDENED=false; PIPESHUB_HARDENED=true ./install.sh --upgrade to adopt)")"
printf "  %-22s %s\n" "Local URL:"     "http://localhost:${APP_PORT}"
printf "  %-22s %s\n" "Listens on:"    "${APP_BIND_ADDRESS:-0.0.0.0}:${APP_PORT}$(case "${APP_BIND_ADDRESS:-0.0.0.0}" in 127.0.0.1|localhost|::1) echo " (this machine only)" ;; *) echo " (every interface, plain HTTP)" ;; esac)"
if [[ -n "${FRONTEND_PUBLIC_URL:-}" ]]; then
  printf "  %-22s %s\n" "Public URL:"  "${FRONTEND_PUBLIC_URL}"
fi
printf "\n"

# ==============================================================================
# 14b. SIGNING-SECRET ROTATION (--rotate-signing-secrets)
# Writes a new one-shot id into .env. The app replaces jwtSecret / scopedJwtSecret
# / cookieSecret only when this id differs from the value stored in the KV store.
# ==============================================================================
if $FLAG_ROTATE_SIGNING_SECRETS; then
  header "Rotate signing secrets"
  warn "This replaces JWT, scoped JWT, and cookie signing secrets."
  warn "Every signed-in user will be logged out. Refresh tokens, password-reset"
  warn "links, and in-flight service-to-service tokens will stop working."
  if ! $FLAG_YES; then
    printf "\n  ${BOLD}Type ROTATE to confirm:${RESET} "
    read -r _rotate_confirm
    [[ "${_rotate_confirm}" == "ROTATE" ]] || { info "Aborted — signing secrets were not changed."; exit 0; }
  fi
  ROTATE_SIGNING_SECRETS="$(gen_secret 16)"
  persist_env_var ROTATE_SIGNING_SECRETS "$ROTATE_SIGNING_SECRETS"
  set -a; . "$ENV_FILE"; set +a
  success "Wrote a new ROTATE_SIGNING_SECRETS id. The app will rotate secrets on next start."
fi

# --print-env-only: show the compose command and exit
if $FLAG_PRINT_ENV_ONLY; then
  _build_flag=""
  [[ "${IMAGE_SOURCE:-prebuilt}" == "local" ]] && _build_flag=" --build"
  printf "\n"
  info "Run the following to start PipesHub:"
  printf "\n  ${BOLD}COMPOSE_PROFILES=%s \\\\\n    docker compose %s -p %s up -d%s${RESET}\n\n" \
    "${COMPOSE_PROFILES:-}" "$COMPOSE_FILES_SHOWN" "$PROJECT_NAME" "$_build_flag"
  success "Done (--print-env-only mode; not launching)."
  if $FLAG_ROTATE_SIGNING_SECRETS; then
    warn "ROTATE_SIGNING_SECRETS is set. Recreate the app container so secrets rotate:"
    warn "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} --env-file ${ENV_FILE} up -d --force-recreate --no-deps pipeshub-ai"
  fi
  exit 0
fi

# Confirm before launching (skip for --upgrade / --rotate-signing-secrets which
# already confirmed intent)
if ! $FLAG_YES && ! $FLAG_UPGRADE && ! $FLAG_ROTATE_SIGNING_SECRETS; then
  printf "  ${BOLD}Launch PipesHub with the above configuration? [Y/n]: ${RESET}"
  read -r _launch_reply
  case "${_launch_reply:-Y}" in
    [Yy]*|"") ;;
    *) info "Aborted. Edit .env if needed, then re-run install.sh."; exit 0 ;;
  esac
fi

# MongoDB 8.x segfaults (exit 139) on some newer host kernels because of a glibc
# rseq/TCMalloc interaction. env.template documents MONGO_GLIBC_TUNABLES for
# exactly this case, but an operator only discovers it after the install has
# already failed -- and the generic exit-139 advice points at recreating the data
# volume, which destroys data without addressing this cause.
#
# Reacting to the observed crash rather than pre-screening the host kernel keeps
# rseq=0 -- the faster TCMalloc default that #2677 deliberately preserved -- on
# every machine that does not need the workaround, and needs no list of affected
# kernel versions to stay accurate.
MONGO_RSEQ_HEAL_TRIED=false
MONGO_HEAL_GRACE_SECS=180
MONGO_RSEQ_PROBE_SECS=12

# The project's mongodb container ids, one per line.
mongo_container_ids() {
  docker ps -aq \
    --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
    --filter "label=com.docker.compose.service=mongodb" 2>/dev/null
}

# The last exit code Docker recorded for this mongodb container, from the
# daemon's own event log.
#
# `docker inspect .State.ExitCode` cannot answer this on its own: it reports 0
# whenever the container is in one of its up windows, so the true code is only
# visible while it happens to be `restarting`. A slow flap -- up for ~25s, crash,
# restart -- hides it from any single sample and from a short probe. The event
# log records every `die` with its code, and filtering by the current container
# id means a recreated container starts with a clean history.
mongo_last_die_code() {
  local id
  id="$(mongo_container_ids | head -1)"
  [[ -n "$id" ]] || return 0
  docker events --since 1h --until "$(date +%s)" \
    --filter "container=$id" --filter "event=die" \
    --format '{{.Actor.Attributes.exitCode}}' 2>/dev/null | tail -1
}

# True when this project's mongodb is crash-looping on exit 139.
mongo_rseq_crashed() {
  local id exit_code restarts elapsed=0
  # No mongodb container means the failure was something else entirely -- a build
  # error, a missing image, another service. Do not spend any time on it.
  if [[ -z "$(mongo_container_ids)" ]]; then return 1; fi

  # Authoritative and race-free: what the daemon recorded when it last died.
  if [[ "$(mongo_last_die_code)" == "139" ]]; then return 0; fi

  # Fallback for a daemon whose event history is unavailable or trimmed. Only
  # catches a fast loop, where the container is restarting most of the time.
  while (( elapsed < MONGO_RSEQ_PROBE_SECS )); do
    for id in $(mongo_container_ids); do
      restarts="$(docker inspect "$id" --format '{{.RestartCount}}' 2>/dev/null || echo 0)"
      exit_code="$(docker inspect "$id" --format '{{.State.ExitCode}}' 2>/dev/null || echo '')"
      if [[ "$exit_code" == "139" ]] && (( ${restarts:-0} >= 1 )); then return 0; fi
    done
    sleep 1
    elapsed=$(( elapsed + 1 ))
  done
  return 1
}

# Docker's RestartCount is a lifetime counter on the container, so a stack that
# crash-looped weeks ago and was fixed still reports those restarts on every
# later run. Snapshot before starting and compare afterwards, so the checks below
# measure only what happened during this install.
_MONGO_RESTART_BASELINE=""

snapshot_mongo_restarts() {
  local id n
  _MONGO_RESTART_BASELINE=""
  for id in $(mongo_container_ids); do
    n="$(docker inspect "$id" --format '{{.RestartCount}}' 2>/dev/null || echo 0)"
    _MONGO_RESTART_BASELINE="${_MONGO_RESTART_BASELINE}${id} ${n:-0}
"
  done
}

# Restarts accumulated since the snapshot. An id absent from the baseline is a
# container this run created or recreated, so all of its restarts are ours.
# Cheap -- one inspect per container, no probing -- so the healthy path can call
# it on every install.
mongo_restart_delta() {
  local id n base total=0
  for id in $(mongo_container_ids); do
    n="$(docker inspect "$id" --format '{{.RestartCount}}' 2>/dev/null || echo 0)"
    base="$(printf '%s' "${_MONGO_RESTART_BASELINE:-}" | awk -v i="$id" '$1==i{print $2; exit}')"
    total=$(( total + ${n:-0} - ${base:-0} ))
  done
  if (( total < 0 )); then total=0; fi
  printf '%s' "$total"
}

# Write the documented tunable, once per run, and never over a value the operator
# chose. Returns 0 when it changed something, so the caller can retry the start.
apply_mongo_rseq_tunable() {
  if $MONGO_RSEQ_HEAL_TRIED; then return 1; fi
  if [[ -n "$(get_existing_val MONGO_GLIBC_TUNABLES "")" ]]; then return 1; fi
  if ! mongo_rseq_crashed; then return 1; fi

  MONGO_RSEQ_HEAL_TRIED=true
  warn "MongoDB is crash-looping on exit 139 (segfault)."
  warn "That is the known glibc rseq/TCMalloc crash on newer host kernels."
  info "Setting MONGO_GLIBC_TUNABLES=glibc.pthread.rseq=1 in .env and retrying..."
  persist_env_var MONGO_GLIBC_TUNABLES "glibc.pthread.rseq=1"
  return 0
}

# docker-compose.yml runs MongoDB as a single-node replica set (rs0). A volume
# written by a standalone mongod converts in place: mongod restarts with
# --replSet and the service healthcheck runs rs.initiate. Before that first
# start the installer offers a backup and records per-collection counts, then
# checks them against the converted member.
MONGO_BACKUP_DIR="${SCRIPT_DIR}/backups"
MONGO_PROBE_WAIT_SECS=60

# "db.collection count" per line. Authenticates only when the container carries
# the root credentials (the compose service does; the throwaway probe does not).
mongo_counts_js() {
  cat <<'JS'
  const pass = process.env.MONGO_INITDB_ROOT_PASSWORD;
  if (pass) db.getSiblingDB("admin").auth(process.env.MONGO_INITDB_ROOT_USERNAME, pass);
  const lines = [];
  for (const info of db.adminCommand({listDatabases: 1, nameOnly: true}).databases) {
    if (info.name === "local" || info.name === "config") continue;
    const target = db.getSiblingDB(info.name);
    for (const coll of target.getCollectionInfos({type: "collection"})) {
      if (coll.name.startsWith("system.") && coll.name !== "system.users") continue;
      lines.push(info.name + "." + coll.name + " " + target.getCollection(coll.name).countDocuments({}));
    }
  }
  print(lines.sort().join("\n"));
JS
}

mongo_probe_name() { printf '%s-mongodb-rs-migrate' "$PROJECT_NAME"; }

stop_mongo_probe() {
  docker stop -t 60 "$(mongo_probe_name)" >/dev/null 2>&1 || true
  docker rm -f "$(mongo_probe_name)" >/dev/null 2>&1 || true
}

# Opens the data volume with a throwaway mongod that has no --auth and listens
# only on its own loopback, so reading it needs neither credentials nor a port.
start_mongo_probe() {
  local volume="$1" image="$2" probe i
  probe="$(mongo_probe_name)"
  stop_mongo_probe
  docker run -d --name "$probe" -v "${volume}:/data/db" \
    -e "GLIBC_TUNABLES=${MONGO_GLIBC_TUNABLES:-glibc.pthread.rseq=0}" \
    "$image" mongod --bind_ip 127.0.0.1 --wiredTigerCacheSizeGB 0.25 >/dev/null || return 1
  for (( i = 0; i < MONGO_PROBE_WAIT_SECS; i++ )); do
    if docker exec "$probe" mongosh --quiet --eval 'quit(db.adminCommand({ping: 1}).ok ? 0 : 1)' >/dev/null 2>&1; then
      return 0
    fi
    [[ "$(docker inspect -f '{{.State.Running}}' "$probe" 2>/dev/null)" == "true" ]] || return 1
    sleep 1
  done
  return 1
}

# R50OFF-FU-2: an install whose MONGO_PASSWORD was empty has no root user, and MongoDB ran without authentication.
# The replica-set key turns authentication on, which would lock everyone out, so before anything starts MongoDB with a
# password this creates the root user on the existing volume (through the probe, which runs without --auth) and writes
# the new password to .env. The app reads its URI from that password, so nothing else changes. A volume that already
# has the user but no password in .env is left without authentication (Compose's compatibility fallback) and named.
ensure_mongo_root_user() {
  local volume="${PROJECT_NAME}_mongodb_data" image probe user password count
  local -a compose_cmd=(docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE")
  [[ -z "$(get_existing_val MONGO_PASSWORD "")" ]] || return 0
  [[ -z "${MONGO_URI:-}" ]] || return 0
  volume_exists "$volume" || return 0
  user="${MONGO_USERNAME:-admin}"
  info "MONGO_PASSWORD is empty and MongoDB holds data: creating its root user '${user}' so it can require a login"
  info "(R50OFF-FU-2). The data is not changed."
  if [[ -n "$(mongo_container_ids)" ]]; then
    "${compose_cmd[@]}" stop pipeshub-ai mongodb >/dev/null 2>&1 || true
  fi
  image="$("${compose_cmd[@]}" config --images mongodb 2>/dev/null | head -1)"
  [[ -n "$image" ]] || die "Could not resolve the MongoDB image from ${COMPOSE_FILE}."
  probe="$(mongo_probe_name)"
  if ! start_mongo_probe "$volume" "$image"; then
    docker logs --tail 30 "$probe" 2>&1 || true
    stop_mongo_probe
    die "Could not open the MongoDB volume ${volume} to add its root user. Nothing was changed."
  fi
  count="$(docker exec -e PXH_MONGO_USER="$user" "$probe" mongosh --quiet --eval     'print(db.getSiblingDB("admin").system.users.countDocuments({user: process.env.PXH_MONGO_USER, db: "admin"}))'     2>/dev/null)" || count=""
  if [[ "$count" != "0" ]]; then
    stop_mongo_probe
    warn "MongoDB already has a user '${user}' but MONGO_PASSWORD in ${ENV_FILE} is empty: it keeps running without"
    warn "authentication (compatibility). Set its password as MONGO_PASSWORD and run ./install.sh --upgrade again."
    return 0
  fi
  password="$(gen_secret 24)"
  if ! docker exec -e PXH_MONGO_USER="$user" -e PXH_MONGO_PASSWORD="$password" "$probe" mongosh --quiet --eval       'db.getSiblingDB("admin").createUser({user: process.env.PXH_MONGO_USER, pwd: process.env.PXH_MONGO_PASSWORD, roles: [{role: "root", db: "admin"}]})'       >/dev/null 2>&1; then
    stop_mongo_probe
    die "Could not create MongoDB's root user. Nothing was changed; MongoDB still runs without authentication."
  fi
  stop_mongo_probe
  MONGO_PASSWORD="$password"
  persist_env_var MONGO_PASSWORD "$MONGO_PASSWORD"
  success "Created MongoDB's root user '${user}'; its password is MONGO_PASSWORD in ${ENV_FILE}."
}

# Converts this project's standalone MongoDB volume to replica set rs0 in place.
# No-op for a fresh install (no volume) and for a volume that is already a
# replica-set member, so re-running the installer is safe.
migrate_mongo_to_replset() {
  local volume="${PROJECT_NAME}_mongodb_data" id image probe before after missing
  local backup="" do_backup=true reply
  local -a compose_cmd=(docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE")
  volume_exists "$volume" || return 0

  id="$(mongo_container_ids | head -1)"
  if [[ -n "$id" ]]; then
    if docker inspect -f '{{join .Config.Cmd " "}}' "$id" 2>/dev/null | grep -q -- '--replSet'; then
      return 0
    fi
    # The standalone mongod holds the data directory lock; the app must not
    # write while the counts are taken.
    info "Stopping the app and the standalone MongoDB to check its data volume..."
    "${compose_cmd[@]}" stop pipeshub-ai mongodb >/dev/null 2>&1 || true
  fi

  image="$("${compose_cmd[@]}" config --images mongodb 2>/dev/null | head -1)"
  [[ -n "$image" ]] || die "Could not resolve the MongoDB image from ${COMPOSE_FILE}."
  probe="$(mongo_probe_name)"
  if ! start_mongo_probe "$volume" "$image"; then
    docker logs --tail 30 "$probe" 2>&1 || true
    stop_mongo_probe
    die "Could not open the MongoDB volume ${volume} to check it. Nothing was changed."
  fi
  if [[ "$(docker exec "$probe" mongosh --quiet --eval 'print(db.getSiblingDB("local").system.replset.countDocuments())' 2>/dev/null)" != "0" ]]; then
    stop_mongo_probe
    return 0
  fi

  info "MongoDB now runs as a single-node replica set (rs0) so the app can use"
  info "multi-document transactions. The data in volume ${volume} is converted in place."
  backup="${MONGO_BACKUP_DIR}/mongodb-${PROJECT_NAME}-$(date +%Y%m%d%H%M%S).archive.gz"
  info "Backup command: docker exec ${probe} mongodump --archive --gzip > ${backup}"
  if ! $FLAG_YES; then
    printf "\n  ${BOLD}Back up MongoDB before converting? [Y/n]: ${RESET}"
    read -r reply
    [[ "${reply:-Y}" =~ ^[Nn] ]] && do_backup=false
  fi
  if $do_backup; then
    mkdir -p "$MONGO_BACKUP_DIR"
    if ! (umask 077; docker exec "$probe" mongodump --archive --gzip --quiet >"$backup"); then
      rm -f "$backup"
      stop_mongo_probe
      die "mongodump failed; MongoDB was not converted. Free disk space in ${MONGO_BACKUP_DIR} and re-run install.sh."
    fi
    success "Backup written to ${backup}"
    info "Restore it, if ever needed, into the running mongodb service with:"
    info "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} exec -T mongodb mongorestore --archive --gzip --drop --username ${MONGO_USERNAME:-admin} --password <MONGO_PASSWORD from .env> --authenticationDatabase admin < ${backup}"
  else
    warn "Converting without a backup."
  fi

  before="$(docker exec "$probe" mongosh --quiet --eval "$(mongo_counts_js)")" || {
    stop_mongo_probe
    die "Could not count MongoDB documents before converting. Nothing was changed."
  }
  stop_mongo_probe

  info "Starting MongoDB as replica set rs0..."
  if ! "${compose_cmd[@]}" up -d --wait mongodb; then
    "${compose_cmd[@]}" logs --tail 40 mongodb 2>&1 || true
    die "MongoDB did not become a writable replica-set primary. The data volume is unchanged apart from the replica-set config; the backup (if taken) is ${backup}."
  fi
  after="$("${compose_cmd[@]}" exec -T mongodb mongosh --quiet --eval "$(mongo_counts_js)")" \
    || die "Could not count MongoDB documents after converting."
  missing="$(printf '%s\n' "$before" | grep -Fxv -f <(printf '%s\n' "$after") || true)"
  if [[ -n "$missing" ]]; then
    error "These collections differ after the conversion (db.collection count before):"
    printf '%s\n' "$missing" >&2
    die "Stop here and restore ${backup:-from your own backup} before using PipesHub."
  fi
  success "MongoDB converted to replica set rs0; $(printf '%s\n' "$before" | grep -c . || true) collections verified."
}

# ==============================================================================
# 15. LAUNCH
# ==============================================================================
header "$(
  if $FLAG_ROTATE_SIGNING_SECRETS && $FLAG_UPGRADE; then echo 'Upgrading PipesHub and rotating signing secrets'
  elif $FLAG_ROTATE_SIGNING_SECRETS; then echo 'Rotating signing secrets'
  elif $FLAG_UPGRADE; then echo 'Upgrading PipesHub'
  else echo 'Launching PipesHub'
  fi
)"

export COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"

# Determine whether to pass --build to compose up.
# IMAGE_SOURCE is read from the sourced .env (covers reuse / upgrade paths too).
_USE_BUILD=false
[[ "${IMAGE_SOURCE:-prebuilt}" == "local" ]] && _USE_BUILD=true

# One TTY check for Compose pull/up progress and the health-wait spinner below.
# curl | bash pipes stdin only; stdout stays on the terminal, so in-place
# progress is the majority install path. Forced `--progress plain` on a TTY
# prints a new line per layer tick and floods the log. Captured stdout (CI,
# tee, redirect) still gets plain so cursor-escape frames do not explode.
_is_tty=false; [[ -t 1 ]] && _is_tty=true
_PROGRESS=(--progress "$(resolve_compose_progress "$_is_tty")")

compose_up()        { docker compose "${_PROGRESS[@]}" "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE" up -d "$@"; }
compose_logs_tail() { docker compose "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE" logs --tail 30 2>&1 || true; }

# Start the stack, healing a MongoDB rseq segfault once if that is what stopped
# it. A segfault fails `up` within seconds rather than later: the app's
# `depends_on: mongodb: condition: service_healthy` is never satisfied, so
# compose reports "dependency failed to start" and exits non-zero long before the
# health poll could react.
#
# The retry is a full `up -d` so dependents that were created but never started
# come up too, and it never repeats --build: if the image compiled the first
# time, it is already built. Callers `die` on a non-zero return; every failure
# message is emitted here so the prebuilt and build paths cannot drift apart.
compose_up_with_mongo_heal() {
  local label="$1"; shift
  if compose_up "$@"; then return 0; fi

  if apply_mongo_rseq_tunable; then
    if compose_up; then
      success "Startup recovered after applying the MongoDB rseq tunable."
      return 0
    fi
    error "docker compose up failed again after applying the MongoDB tunable."
    compose_logs_tail
    warn "MongoDB is still not starting, so something else is wrong. Read the logs"
    warn "above first. Do not recreate the mongo data volume yet — that destroys"
    warn "your data and will not fix a crash the tunable did not resolve."
    return 1
  fi

  error "docker compose ${label} failed. Last 30 lines of container logs:"
  compose_logs_tail
  return 1
}

# Decide whether to refresh the prebuilt image from the registry before starting.
# Pure decision (no side effects) so it is unit-testable in isolation.
# `docker compose up -d` only pulls an image that is ABSENT locally, so a cached
# :latest is reused forever — that is how a host ends up on a weeks-old build.
# Refreshing by default fixes that, with deliberate opt-outs for the cases where
# someone wants their current/specific image instead.
should_pull_image() { # args: use_build flag_no_pull env_no_pull -> "true"|"false"
  local use_build="$1" flag_no_pull="$2" env_no_pull="$3"
  # Local build owns the image; nothing to pull.
  [[ "$use_build" == true ]] && { echo false; return; }
  # Explicit opt-out: keep a known-good/old cached image, or air-gapped host.
  [[ "$flag_no_pull" == true ]] && { echo false; return; }
  case "$env_no_pull" in 1|true|yes) echo false; return ;; esac
  echo true
}

# Version and commit of the checkout, as build args for the image. The running
# container has no git history, so they are read here, on the host. A value
# already in the environment wins; a checkout without git leaves them empty.
export_build_info() { # args: repo_root
  local repo_root="$1" tag
  tag="$(git -C "$repo_root" describe --tags --abbrev=0 --match 'v*' 2>/dev/null || true)"
  export APP_VERSION="${APP_VERSION:-${tag#v}}"
  export GIT_COMMIT="${GIT_COMMIT:-$(git -C "$repo_root" rev-parse HEAD 2>/dev/null || true)}"
}

# ── Image signatures and digest pins (SEC-D-04, ADR-SEC-05) ──────────────────
# Each release image is signed in CI by release-publish.yml (keyless Sigstore:
# the certificate names that workflow at a v* tag). The installer runs cosign verify on the
# exact digest it pulled and writes that digest to .env, so the
# stack runs the image it checked; a tag re-pushed on the registry changes
# nothing until the next --upgrade checks the new one.
PIPESHUB_COSIGN_IMAGE="ghcr.io/sigstore/cosign/cosign:v2.4.1@sha256:b03690aa52bfe94054187142fba24dc54137650682810633901767d8a3e15b31"
PIPESHUB_SIGNER_IDENTITY='^https://github\.com/pipeshub-ai/pipeshub-ai/\.github/workflows/release-publish\.yml@refs/tags/v[0-9]'
PIPESHUB_SIGNER_ISSUER="https://token.actions.githubusercontent.com"
# A distribution that publishes its own images names its release workflow here:
# PIPESHUB_SIGNER_REPOSITORY=owner/repo and, if it is not release-publish.yml,
# PIPESHUB_SIGNER_WORKFLOW=file.yml. They are names, not a pattern: the identity
# stays that one workflow at a v* tag under the same issuer, so no value widens it.
# warn until the first signed release is published, then require: releases
# before signing have no signature to check (RELEASE-CHECKLIST.md, SEC-D-04).
IMAGE_VERIFY_DEFAULT="warn"

image_verify_mode() { # args: requested -> require|warn|off; anything else fails closed
  local mode="${1:-$IMAGE_VERIFY_DEFAULT}"
  case "$mode" in
    require|warn|off) printf '%s\n' "$mode" ;;
    *) printf 'require\n' ;;
  esac
}

# The registry digest of a pulled image, kept with its tag for people reading
# .env: repo:tag@sha256:... Nothing when the image was built or loaded locally.
image_pinned_ref() { # args: image ref
  local ref="$1" repo line
  if [[ "$ref" == *@sha256:* ]]; then printf '%s\n' "$ref"; return 0; fi
  repo="$ref"
  [[ "${repo##*/}" == *:* ]] && repo="${repo%:*}"
  while IFS= read -r line; do
    case "${line%@*}" in
      "$repo"|"docker.io/$repo"|"docker.io/library/$repo")
        printf '%s@%s\n' "$ref" "${line#*@}"; return 0 ;;
    esac
  done < <(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" 2>/dev/null)
  return 0
}

cosign_run() { # args: cosign arguments; a host cosign if there is one, else the pinned cosign image
  if command -v cosign >/dev/null 2>&1; then cosign "$@"; return; fi
  local -a mounts=()
  [[ -n "${PIPESHUB_IMAGE_PUBKEY:-}" ]] && mounts=(-v "${PIPESHUB_IMAGE_PUBKEY}:/pipeshub-release.pub:ro")
  docker run --rm "${mounts[@]}" "$PIPESHUB_COSIGN_IMAGE" "$@"
}

signer_identity() { # -> the certificate identity regexp; fails on a malformed distribution signer
  local repo="${PIPESHUB_SIGNER_REPOSITORY:-}" workflow="${PIPESHUB_SIGNER_WORKFLOW:-release-publish.yml}"
  if [[ -z "$repo" && -z "${PIPESHUB_SIGNER_WORKFLOW:-}" ]]; then printf '%s\n' "$PIPESHUB_SIGNER_IDENTITY"; return 0; fi
  repo="${repo:-pipeshub-ai/pipeshub-ai}"
  if [[ ! "$repo" =~ ^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$ || "$repo" == */.* || ! "$workflow" =~ ^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.ya?ml$ ]]; then
    error "PIPESHUB_SIGNER_REPOSITORY must be owner/repo and PIPESHUB_SIGNER_WORKFLOW a workflow file name (got '$repo', '$workflow')."
    return 1
  fi
  printf '^https://github\\.com/%s/\\.github/workflows/%s@refs/tags/v[0-9]\n' "${repo//./\\.}" "${workflow//./\\.}"
}

verify_image_signature() { # args: repo:tag@sha256:... -> 0 when a PipesHub release signature verifies
  local ref="$1" key="${PIPESHUB_IMAGE_PUBKEY:-}" identity
  local -a args=(verify)
  if [[ -n "$key" ]]; then
    if command -v cosign >/dev/null 2>&1; then args+=(--key "$key"); else args+=(--key /pipeshub-release.pub); fi
    # A key-signed mirror or air-gapped registry may keep no transparency log.
    [[ "${PIPESHUB_IMAGE_VERIFY_NO_TLOG:-}" == 1 ]] && args+=(--insecure-ignore-tlog=true)
  else
    identity="$(signer_identity)" || return 1
    args+=(--certificate-identity-regexp "$identity" --certificate-oidc-issuer "$PIPESHUB_SIGNER_ISSUER")
  fi
  cosign_run "${args[@]}" "$ref" >/dev/null 2>"${_COSIGN_ERR:-/dev/null}"
}

refuse_image() { # args: label ref reason
  error "Refusing to start PipesHub: the $1 image $2 $3."
  error "This run started nothing; a stack that is already running keeps its current images and your data is untouched."
  error "A failed signature check can mean the image on the registry was replaced. Do not run it."
  error "What to do: re-run install.sh in a few minutes (a release may still be publishing). If it fails again, send this output to contact@pipeshub.com."
  error "Your own build or mirror: set PIPESHUB_IMAGE_PUBKEY to the public key you signed it with, or PIPESHUB_IMAGE_VERIFY=off to skip the check."
  exit 1
}

# Prints the reference .env should pin, or nothing. Exits (refuse_image) when
# the check fails and the mode is require.
pin_release_image() { # args: label image_ref mode check(true|false)
  local label="$1" ref="$2" mode="$3" check="$4" pinned
  pinned="$(image_pinned_ref "$ref")"
  if [[ -z "$pinned" ]]; then
    [[ "$mode" == require && "$check" == true ]] && refuse_image "$label" "$ref" "has no registry digest (it was built or loaded locally), so its signature cannot be checked"
    warn "The $label image $ref has no registry digest; it runs by tag." >&2
    return 0
  fi
  if [[ "$check" != true || "$mode" == off ]]; then
    printf '%s\n' "$pinned"; return 0
  fi
  if verify_image_signature "$pinned"; then
    success "Signature verified: $label image ${pinned##*@}" >&2
  elif [[ "$mode" == require ]]; then
    [[ -s "${_COSIGN_ERR:-}" ]] && tail -n 3 "$_COSIGN_ERR" >&2
    refuse_image "$label" "$pinned" "has no valid PipesHub release signature"
  else
    warn "The $label image ${pinned} has no valid PipesHub release signature; running it because PIPESHUB_IMAGE_VERIFY=warn." >&2
  fi
  printf '%s\n' "$pinned"
}

env_file_set() { # args: key value; replaces the key's line in .env or appends it, mode 600
  local key="$1" val="$2" tmp
  tmp="$(mktemp "${ENV_FILE}.XXXXXX")" || return 1
  { grep -vE "^${key}=" "$ENV_FILE" 2>/dev/null || true; printf '%s=%s\n' "$key" "$val"; } >"$tmp"
  chmod 600 "$tmp" && mv -f "$tmp" "$ENV_FILE"
}

# Check the pulled images and pin their digests in .env. A run that did not
# pull (--no-pull, air-gapped) pins what is cached; with --offline-bundle the
# bundle's signature and image IDs were checked before the load. A local build
# pins nothing.
pin_release_images() { # args: pulled(true|false) with_sandbox(true|false)
  local mode check="$1" app sandbox=""
  mode="$(image_verify_mode "${PIPESHUB_IMAGE_VERIFY:-}")"
  [[ "$mode" == off ]] && warn "Image signature checks are off (PIPESHUB_IMAGE_VERIFY=off)."
  [[ "$check" != true && "$mode" != off && -z "${OFFLINE_BUNDLE:-}" ]] && warn "Images were not pulled, so their signatures were not checked here."
  _COSIGN_ERR="$(mktemp)"
  app="$(pin_release_image app "$_APP_IMAGE" "$mode" "$check")" || exit 1
  if [[ "$2" == true ]]; then
    sandbox="$(pin_release_image sandbox "$_SANDBOX_IMAGE" "$mode" "$check")" || exit 1
  fi
  rm -f "$_COSIGN_ERR"
  env_file_set PIPESHUB_APP_PINNED "$app"
  env_file_set PIPESHUB_SANDBOX_PINNED "$sandbox"
}

# ── Offline bundle (R41SUPPLY-FU-7, ADR-SEC-06 D2) ───────────────────────────
# An air-gapped host cannot ask a registry for signatures, so the release signs
# the bundle instead: a manifest naming each archive part by SHA-256 and the
# image tags it holds, signed keyless by release-publish.yml at the tag. Its Sigstore
# bundle carries the transparency-log proof, so cosign checks it with no network
# against the Sigstore trusted root this installer pins (sigstore-trusted-root.json,
# beside this script or the manifest). Nothing is loaded before every check passes.
PIPESHUB_SIGSTORE_ROOT_SHA256="6494e21ea73fa7ee769f85f57d5a3e6a08725eae1e38c755fc3517c9e6bc0b66"
OFFLINE_BUNDLE_SCHEMA="pipeshub-offline-bundle 1"

sha256_file() { # args: file -> its SHA-256
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

offline_bundle_trusted_root() { # args: manifest -> the pinned Sigstore trusted root, or nothing
  local f
  for f in "${SCRIPT_DIR}/sigstore-trusted-root.json" "$(dirname "$1")/sigstore-trusted-root.json"; do
    if [[ -f "$f" && "$(sha256_file "$f")" == "$PIPESHUB_SIGSTORE_ROOT_SHA256" ]]; then printf '%s\n' "$f"; return 0; fi
  done
  return 0
}

verify_offline_bundle_signature() { # args: manifest trusted_root -> 0 when the release's signature verifies
  local manifest="$1" root="$2" key="${PIPESHUB_IMAGE_PUBKEY:-}"
  local -a args=(verify-blob --bundle "${manifest}.sigstore.json" --new-bundle-format --offline)
  if [[ -n "$key" ]]; then
    args+=(--key "$key")
  else
    args+=(--trusted-root "$root" --certificate-identity-regexp "$PIPESHUB_SIGNER_IDENTITY"
      --certificate-oidc-issuer "$PIPESHUB_SIGNER_ISSUER")
  fi
  cosign "${args[@]}" "$manifest" >/dev/null 2>"${_COSIGN_ERR:-/dev/null}"
}

refuse_bundle() { # args: manifest reason
  error "Refusing the offline bundle $1: $2."
  error "Nothing was loaded and this run started nothing; a stack that is already running keeps its images and your data is untouched."
  error "What to do: copy the bundle again from the release page (the .manifest, its .sigstore.json and every part it names, into one folder) and re-run."
  error "A bundle you built and signed yourself: set PIPESHUB_IMAGE_PUBKEY to your cosign public key. PIPESHUB_IMAGE_VERIFY=off skips the signature (the checksums are still checked)."
  exit 1
}

# Verifies, then loads. Under PIPESHUB_IMAGE_VERIFY=warn a bundle is still
# required to be signed: the release signs every bundle it publishes, so an
# unsigned one did not come from it. Only off skips the signature.
load_offline_bundle() { # args: manifest mode
  local manifest="$1" mode="$2" dir root="" line name sum ref version
  [[ -f "$manifest" ]] || refuse_bundle "$manifest" "the manifest file does not exist"
  [[ "$(head -1 "$manifest")" == "$OFFLINE_BUNDLE_SCHEMA" ]] || refuse_bundle "$manifest" "it is not a PipesHub offline bundle manifest"
  dir="$(cd "$(dirname "$manifest")" && pwd)"
  if [[ "$mode" == off ]]; then
    warn "The offline bundle's signature is not checked (PIPESHUB_IMAGE_VERIFY=off); its checksums are."
  else
    [[ -f "${manifest}.sigstore.json" ]] || refuse_bundle "$manifest" "it is not signed (${manifest##*/}.sigstore.json is missing)"
    command -v cosign >/dev/null 2>&1 \
      || refuse_bundle "$manifest" "checking its signature needs cosign on this host (cosign v2.4 or newer from github.com/sigstore/cosign/releases, checked against its published checksum)"
    root="$(offline_bundle_trusted_root "$manifest")"
    [[ -n "$root" || -n "${PIPESHUB_IMAGE_PUBKEY:-}" ]] \
      || refuse_bundle "$manifest" "sigstore-trusted-root.json is missing or is not the one this installer pins (sha256 ${PIPESHUB_SIGSTORE_ROOT_SHA256})"
    _COSIGN_ERR="$(mktemp)"
    if ! verify_offline_bundle_signature "$manifest" "$root"; then
      [[ -s "$_COSIGN_ERR" ]] && tail -n 3 "$_COSIGN_ERR" >&2
      rm -f "$_COSIGN_ERR"
      refuse_bundle "$manifest" "its signature is not a PipesHub release signature"
    fi
    rm -f "$_COSIGN_ERR"
    success "Signature verified: offline bundle ${manifest##*/}"
  fi
  version="$(sed -n 's/^version \([A-Za-z0-9._-]*\)$/\1/p' "$manifest" | head -1)"
  [[ -n "$version" ]] || refuse_bundle "$manifest" "it names no version"
  [[ -z "${IMAGE_TAG:-}" || "$IMAGE_TAG" == "$version" ]] \
    || refuse_bundle "$manifest" "it holds version ${version}, but this install asks for ${IMAGE_TAG}"
  local -a parts=()
  while read -r line name sum; do
    [[ "$line" == part ]] || continue
    [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]] || refuse_bundle "$manifest" "it names a part outside its folder ($name)"
    [[ -f "$dir/$name" ]] || refuse_bundle "$manifest" "the part $name is missing"
    [[ "$(sha256_file "$dir/$name")" == "$sum" ]] || refuse_bundle "$manifest" "the part $name does not match its checksum"
    parts+=("$dir/$name")
  done <"$manifest"
  [[ ${#parts[@]} -gt 0 ]] || refuse_bundle "$manifest" "it names no archive"
  info "Loading the offline bundle (${#parts[@]} part(s), version ${version})..."
  cat "${parts[@]}" | gzip -dc | docker load >/dev/null || refuse_bundle "$manifest" "docker load failed"
  # The parts are the signed bytes, so what docker loaded is what the release saved; an image ID is not compared
  # because it depends on the host's image store (classic or containerd).
  while read -r line ref; do
    [[ "$line" == image ]] || continue
    docker image inspect "$ref" >/dev/null 2>&1 || refuse_bundle "$manifest" "the archive did not hold $ref"
    success "Loaded from the signed bundle: $ref"
  done <"$manifest"
}

_DO_PULL="$(should_pull_image "$_USE_BUILD" "$FLAG_NO_PULL" "${PIPESHUB_NO_PULL:-}")"
# Rotation without --upgrade must not refresh images; the operator asked only
# to replace signing secrets.
if $FLAG_ROTATE_SIGNING_SECRETS && ! $FLAG_UPGRADE; then
  _DO_PULL=false
fi
# Pinning a specific tag (--version / PIPESHUB_VERSION) still benefits from the
# pull: it fetches exactly that immutable tag rather than a moving :latest, so
# reproducibility is preserved while a stale local copy is corrected.
_APP_IMAGE="${APP_IMAGE_REPO}:${IMAGE_TAG:-latest}"
_SANDBOX_IMAGE="${SANDBOX_DOCKER_IMAGE:-pipeshubai/pipeshub-sandbox:${IMAGE_TAG:-latest}}"

if project_has_pinned_container_names; then
  warn_pinned_container_rename
fi

ensure_mongo_root_user
migrate_mongo_to_replset

# Baseline before anything starts, so the post-start checks below can tell a
# crash loop from restarts this run had nothing to do with.
snapshot_mongo_restarts

if $_USE_BUILD; then
  $FLAG_UPGRADE && info "Rebuilding image from source for tag: ${IMAGE_TAG:-local}..."
  info "Building image from source and starting containers..."
  info "(This may take 10–30+ minutes on first run)"
  export_build_info "$(cd "${SCRIPT_DIR}/../../" && pwd)"
  if [[ "$SANDBOX_PLAN" == "migrate" ]]; then
    # The broker runs from the app image, so it is built before the switch.
    docker compose "${_PROGRESS[@]}" "${COMPOSE_FILES[@]}" -p "$PROJECT_NAME" --env-file "$ENV_FILE" build pipeshub-ai \
      || die "Fix the build error above and re-run install.sh."
  fi
  # A local build has no registry digest or release signature to pin.
  env_file_set PIPESHUB_APP_PINNED ""
  env_file_set PIPESHUB_SANDBOX_PINNED ""
  prepare_sandbox_plane
  if ! compose_up_with_mongo_heal "up --build" --build; then
    die "Fix the error above and re-run install.sh."
  fi
else
  # The pull must fetch the tag, not the digest an earlier run pinned.
  env_file_set PIPESHUB_APP_PINNED ""
  env_file_set PIPESHUB_SANDBOX_PINNED ""
  if [[ "$_DO_PULL" == true ]]; then
    info "Refreshing the PipesHub images ($_APP_IMAGE$([ "${DEPLOY_TYPE:-}" = "eval" ] || echo ", $_SANDBOX_IMAGE"))... (pass --no-pull to keep cached images)"
    # App and sandbox images share the moving IMAGE_TAG (often :latest). Infra
    # images use pinned tags and are fetched by `up -d` when absent.
    # A pull failure is non-fatal when an image is already cached, so a flaky
    # network or a temporary registry outage does not block a working install.
    # Eval does not start the sandbox profile, so skip that pull.
    _PULL_SERVICES=(pipeshub-ai)
    [[ ",${COMPOSE_PROFILES:-}," == *",sandbox,"* ]] && _PULL_SERVICES+=(sandbox-image)
    if ! docker compose "${_PROGRESS[@]}" \
        "${COMPOSE_FILES[@]}" \
        -p "$PROJECT_NAME" \
        --env-file "$ENV_FILE" \
        pull "${_PULL_SERVICES[@]}" 2>&1; then
      _cached=true
      docker image inspect "$_APP_IMAGE" >/dev/null 2>&1 || _cached=false
      if [[ ",${COMPOSE_PROFILES:-}," == *",sandbox,"* ]]; then
        docker image inspect "$_SANDBOX_IMAGE" >/dev/null 2>&1 || _cached=false
      fi
      if $_cached; then
        warn "Could not refresh images; continuing with cached copies if present."
      else
        warn "Could not pull a required image and it is not cached locally — the next step may fail."
        warn "On an air-gapped host, preload the image (docker load) and re-run with --no-pull."
      fi
    fi
  else
    if [[ -n "$OFFLINE_BUNDLE" ]]; then
      load_offline_bundle "$OFFLINE_BUNDLE" "$(image_verify_mode "${PIPESHUB_IMAGE_VERIFY:-}")"
    elif $FLAG_ROTATE_SIGNING_SECRETS && ! $FLAG_UPGRADE; then
      info "Skipping image refresh; rotating signing secrets only."
    else
      info "Skipping image refresh; using locally cached images (--no-pull)."
    fi
  fi
  pin_release_images "$_DO_PULL" "$([[ ",${COMPOSE_PROFILES:-}," == *",sandbox,"* ]] && echo true || echo false)"
  prepare_sandbox_plane
  info "Starting containers..."
  if ! compose_up_with_mongo_heal "up"; then
    die "Fix the error above and re-run install.sh."
  fi
fi

# Node loads signing secrets at process start. Force-recreate the app container
# so a running stack cannot keep the old JWT/cookie keys in memory.
if $FLAG_ROTATE_SIGNING_SECRETS; then
  info "Recreating the app container so it applies the new signing secrets..."
  if ! docker compose "${_PROGRESS[@]}" \
      "${COMPOSE_FILES[@]}" \
      -p "$PROJECT_NAME" \
      --env-file "$ENV_FILE" \
      up -d --force-recreate --no-deps pipeshub-ai; then
    die "Failed to recreate pipeshub-ai after writing ROTATE_SIGNING_SECRETS. Signing secrets may not have rotated."
  fi
fi

# ==============================================================================
# 16. HEALTH WAIT
# Uses compose exec so curl and python3 run inside the app service — no host deps.
# ==============================================================================
header "Waiting for PipesHub to become healthy"

printf "  (May take up to %ds on first start — embedding model may need to download)\n\n" "$HEALTH_WAIT_SECS"

CONTAINER_HEALTHY=false
HOST_REACHABLE=false

# Confirm the app port is reachable from the host — not just healthy inside the
# container. This catches port-publish, firewall, and reverse-proxy problems
# that leave the UI unreachable even though every service reports healthy.
# If neither curl nor wget is available we cannot verify, so we do not block.
check_host_reachable() {
  if command -v curl >/dev/null 2>&1; then
    curl -sf "http://localhost:${APP_PORT}/api/v1/health/services" -o /dev/null 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null "http://localhost:${APP_PORT}/api/v1/health/services" 2>/dev/null
  else
    return 0
  fi
}

# One readiness probe: the core services must all report healthy. Runs inside the
# container so the host needs no curl/python. embedding is intentionally excluded
# — on first run it downloads its model and can sit 'unhealthy' for minutes
# without blocking core usability (mirrors the compose healthcheck).
app_is_healthy() {
  compose_app_exec \
    curl -sf http://localhost:3000/api/v1/health/services \
    -o /tmp/pipeshub_hc.json 2>/dev/null || return 1
  compose_app_exec python3 -c "
import json, sys
d = json.load(open('/tmp/pipeshub_hc.json'))
s = d.get('services', {}) or {}
required = ('query', 'connector', 'indexing', 'docling')
sys.exit(0 if all(s.get(k) == 'healthy' for k in required) else 1)
" 2>/dev/null
}

# A container that has restarted several times is broken in a way the stack can't
# recover from on its own — it is crashing (e.g. SIGSEGV) or being killed (e.g.
# OOM). Report any such container (by the compose project label, so profile-gated
# services are included) with its restart count and last exit code so the failure
# names the actual symptom instead of guessing a cause. exit 137 = killed (often
# OOM), 139 = segfault. Output is one indented line per offending container.
CRASH_LOOP_THRESHOLD=4
crash_looping_containers() {
  local id name count exit_code
  for id in $(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT_NAME}" 2>/dev/null); do
    count="$(docker inspect "$id" --format '{{.RestartCount}}' 2>/dev/null || echo 0)"
    if [[ "${count:-0}" -ge "$CRASH_LOOP_THRESHOLD" ]]; then
      name="$(docker inspect "$id" --format '{{.Name}}' 2>/dev/null | sed 's#^/##')"
      exit_code="$(docker inspect "$id" --format '{{.State.ExitCode}}' 2>/dev/null || echo '?')"
      printf '    - %s (%s restarts, last exit %s)\n' "$name" "$count" "${exit_code:-?}"
    fi
  done
}

# Poll until healthy or the deadline passes. Uses _is_tty from launch (same
# flag as Compose progress). On a TTY, redraw a single spinner line in place;
# when stdout is captured (CI, tee, redirect) emit a sparse heartbeat instead.
ELAPSED=0
CHECK_EVERY=5
HEARTBEAT_EVERY=30
START_TS=$SECONDS
_spinner=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
_spin=0
_CRASH_REPORT=""

while (( ELAPSED < HEALTH_WAIT_SECS )); do
  if (( ELAPSED % CHECK_EVERY == 0 )) && app_is_healthy; then
    CONTAINER_HEALTHY=true
    break
  fi
  # After a grace period for normal startup churn (e.g. Kafka waiting on
  # Zookeeper), give up early if a container is clearly restart-looping — it will
  # not recover on its own, so there is no point waiting out the full timeout.
  if (( ELAPSED >= 90 && ELAPSED % 15 == 0 )); then
    _CRASH_REPORT="$(crash_looping_containers)"
    # A MongoDB rseq segfault has a known one-line fix. Apply it and keep
    # waiting instead of failing an install that is one restart from working.
    if [[ -n "$_CRASH_REPORT" ]] && apply_mongo_rseq_tunable; then
      # Full `up -d`, not `--force-recreate mongodb`: dependents that never
      # started will not appear just because mongodb was recreated.
      if compose_up >/dev/null 2>&1; then
        _CRASH_REPORT=""
        # Extend the deadline, never shorten it: at t=90 a bare ELAPSED+180
        # would cut the default 420s wait down to 270.
        _healed_deadline=$(( ELAPSED + MONGO_HEAL_GRACE_SECS ))
        if (( _healed_deadline > HEALTH_WAIT_SECS )); then
          HEALTH_WAIT_SECS=$_healed_deadline
        fi
        success "MongoDB restarted with the rseq tunable; waiting for it to settle."
      fi
    fi
    [[ -n "$_CRASH_REPORT" ]] && break
  fi
  if $_is_tty; then
    printf "\r  ${CYAN}%s${RESET} Starting services… ${BOLD}%ds${RESET} elapsed (timeout %ds)  " \
      "${_spinner[_spin]}" "$ELAPSED" "$HEALTH_WAIT_SECS"
    _spin=$(( (_spin + 1) % ${#_spinner[@]} ))
    sleep 1
    ELAPSED=$(( ELAPSED + 1 ))
  else
    (( ELAPSED % HEARTBEAT_EVERY == 0 )) && \
      printf "  … still starting (%ds / %ds)\n" "$ELAPSED" "$HEALTH_WAIT_SECS"
    sleep "$CHECK_EVERY"
    ELAPSED=$(( ELAPSED + CHECK_EVERY ))
  fi
done

# Final probe: the app may have crossed the line within the last interval; do not
# report a false "not ready" verdict if it is in fact serving now.
if ! $CONTAINER_HEALTHY && app_is_healthy; then CONTAINER_HEALTHY=true; fi

# Erase the spinner line so the result prints cleanly.
$_is_tty && printf "\r\033[K"

if $CONTAINER_HEALTHY; then
  success "PipesHub services are healthy (ready in $(( SECONDS - START_TS ))s)."
  if check_host_reachable; then
    HOST_REACHABLE=true
  else
    warn "Services are healthy inside the container, but http://localhost:${APP_PORT} is not reachable from this host."
    warn "This is usually a port-publish, firewall, or reverse-proxy issue."
    warn "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} logs -f pipeshub-ai"
  fi

  # "Healthy" is not the same as "stable". A segfaulting MongoDB is healthy in
  # the gaps between crashes, and those gaps are long enough to satisfy the app's
  # depends_on and to pass the poll above -- so the install reports success and
  # walks away from a database that restarts every ~25s, dropping every
  # connection each time. Restarts are the signal the health check cannot see.
  _mongo_restarts="$(mongo_restart_delta)"
  if (( _mongo_restarts > 0 )); then
    if apply_mongo_rseq_tunable; then
      if compose_up >/dev/null 2>&1; then
        # The restart drops the stack briefly. Do not let the banner claim ready
        # while it is still coming back.
        _heal_wait=0
        while (( _heal_wait < 120 )) && ! app_is_healthy; do
          sleep 5
          _heal_wait=$(( _heal_wait + 5 ))
        done
        if app_is_healthy; then
          success "MongoDB restarted ${_mongo_restarts}x on exit 139; applied the tunable and restarted the stack."
        else
          CONTAINER_HEALTHY=false
          warn "Applied the MongoDB tunable and restarted, but the stack has not come back healthy."
          warn "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} logs -f pipeshub-ai"
        fi
      else
        CONTAINER_HEALTHY=false
        warn "Applied the MongoDB tunable, but restarting the stack failed."
        warn "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} logs mongodb"
      fi
    else
      warn "MongoDB restarted ${_mongo_restarts} time(s) during this install."
      warn "The stack is healthy right now, but a database that keeps restarting"
      warn "drops every connection each time it goes. Check why:"
      warn "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} logs --tail 40 mongodb"
      warn "  exit 139 → set MONGO_GLIBC_TUNABLES=glibc.pthread.rseq=1 in .env"
      warn "  exit 137 → out of memory; raise MONGO_CACHE_GB and MONGO_MEMORY_LIMIT together"
    fi
  fi
elif [[ -n "${_CRASH_REPORT:-}" ]]; then
  error "A container keeps restarting, so the stack cannot become healthy:"
  printf "%s\n" "$_CRASH_REPORT"
  _c1="$(printf '%s' "$_CRASH_REPORT" | sed -n '1s/^[[:space:]]*-[[:space:]]*\([^ ]*\).*/\1/p')"
  _c1="${_c1:-<name>}"
  warn "A service that restarts repeatedly is crashing or being killed. Find out why:"
  warn "  docker logs --tail 50 ${_c1}"
  warn "  docker inspect ${_c1} --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}}'"
  warn "Read the last exit code above, then:"
  # Memory hint: on Linux/WSL the host figure (free) is what matters; on
  # macOS/Windows containers run in the Docker Desktop VM, so report its
  # allocation instead. free(1)/awk do not exist on macOS in the host sense.
  if $IS_LINUX || $IS_WSL; then
    _free_mb="$(free -m 2>/dev/null | awk '/^Mem:/{print $7}')"
    [[ -n "${_free_mb:-}" ]] && warn "  (available memory right now: ${_free_mb} MB; the full stack wants ~16 GB)"
  else
    _vm_mb="$(docker_vm_mem_mb)"
    (( _vm_mb > 0 )) && warn "  (Docker Desktop VM memory: ${_vm_mb} MB; the full stack wants ~16 GB — raise it in Settings → Resources)"
  fi
  warn "  • exit 137 / oom=true → out of memory. Free RAM, or switch to the lighter"
  warn "      'slim' profile (Redis broker + KV; drops Kafka/Zookeeper): ./install.sh --reconfigure"
  warn "  • exit 139 on mongodb → set MONGO_GLIBC_TUNABLES=glibc.pthread.rseq=1 in .env and"
  warn "      re-run ./install.sh --upgrade. The installer normally applies this for you; if"
  warn "      you are seeing this, it was already set or the restart did not take. Try this"
  warn "      before touching the data volume — recreating the volume destroys your data and"
  warn "      does not fix this cause."
  warn "  • exit 139 elsewhere  → the service crashed (segfault). Usually a corrupted data"
  warn "      volume from an earlier hard kill — recreate it and re-run ./install.sh. If it"
  warn "      recurs on a fresh volume, it is an incompatible host kernel/CPU (see docker logs)."
  warn "  • anything else       → read 'docker logs' above for the specific error"
else
  warn "Health check did not pass within ${HEALTH_WAIT_SECS}s."
  warn "Services may still be starting (first start can be slow while the embedding model downloads). Check logs:"
  warn "  docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} logs -f pipeshub-ai"
fi

# ==============================================================================
# 16b. OUTBOUND CONNECTIVITY (warn-only — air-gapped installs are valid)
# Cloud LLMs and external connectors need container egress; local models do not.
# ==============================================================================
docker_iptables_disabled() {
  local f="/etc/docker/daemon.json"
  [[ -r "$f" ]] || return 1
  grep -qE '"iptables"[[:space:]]*:[[:space:]]*false' "$f" 2>/dev/null
}

container_has_outbound_internet() {
  app_service_is_running || return 1
  if compose_app_exec sh -c \
      'command -v curl >/dev/null 2>&1 && curl -sf -m 8 -4 -o /dev/null https://1.1.1.1/ 2>/dev/null'; then
    return 0
  fi
  compose_app_exec sh -c \
    'command -v wget >/dev/null 2>&1 && wget -q -T 8 -O /dev/null https://1.1.1.1/ 2>/dev/null'
}

warn_container_outbound_connectivity() {
  if container_has_outbound_internet; then
    return 0
  fi
  warn "PipesHub container cannot reach the public internet."
  warn "  Cloud LLMs (Gemini, OpenAI, …) and external connectors will not work until container egress is fixed."
  warn "  Local models (Ollama, LM Studio, built-in embeddings) still work — air-gapped installs are supported."
  if docker_iptables_disabled; then
    warn "  Detected: /etc/docker/daemon.json has \"iptables\": false (Docker is not managing NAT for containers)."
    warn "    Fix: remove that setting or set \"iptables\": true, then: sudo systemctl restart docker"
  fi
  warn "  Diagnose: docker compose ${COMPOSE_FILES_SHOWN} -p ${PROJECT_NAME} exec -T pipeshub-ai curl -s -o /dev/null -m 6 -w '%{http_code}\\n' https://1.1.1.1/"
  warn "  Docs: deployment/docker-compose/ADVANCED_DEPLOYMENT.md#container-outbound-connectivity"
}

if app_service_is_running; then
  warn_container_outbound_connectivity
fi

# ==============================================================================
# 17. FINAL STATUS BANNER
# Ready only when services are healthy AND the app answers from the host.
# ==============================================================================
READY=false
if $CONTAINER_HEALTHY && $HOST_REACHABLE; then READY=true; fi

if $READY; then
  printf "\n${BOLD}${GREEN}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"
  printf "  ${BOLD}${GREEN}PipesHub AI is ready!${RESET}\n\n"
else
  printf "\n${BOLD}${YELLOW}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"
  printf "  ${BOLD}${YELLOW}PipesHub containers are running, but not confirmed ready yet.${RESET}\n"
  printf "  First start can take several minutes. Open the URL below in a few minutes;\n"
  printf "  if it stays down, check the logs at the bottom of this output.\n\n"
fi
printf "  ${BOLD}URLs${RESET}\n"
printf "  ${DIM}%s${RESET}\n" "$(printf '─%.0s' {1..53})"
printf "  ${CYAN}Local:${RESET}   http://localhost:${APP_PORT}\n"
if [[ -n "${FRONTEND_PUBLIC_URL:-}" ]]; then
  printf "  ${CYAN}Public:${RESET}  %s\n\n" "${FRONTEND_PUBLIC_URL}"
  printf "  ${YELLOW}Note:${RESET} Ensure DNS for %s points to this machine\n" "${FRONTEND_PUBLIC_URL}"
  printf "  and that your reverse proxy (Nginx, Caddy, Cloudflare) is configured.\n"
fi
# curl | bash leaves the user's shell wherever they started. Clone users can
# re-run the repo-root wrapper; standalone users must use this directory.
resolve_banner_dirs

printf "\n  ${BOLD}This install${RESET}\n"
printf "  ${DIM}%s${RESET}\n" "$(printf '─%.0s' {1..53})"
if [[ "$SCRIPT_DIR" == "$BANNER_CLI_DIR" ]]; then
  printf "  ${CYAN}Directory:${RESET}  %s\n" "$SCRIPT_DIR"
else
  printf "  ${CYAN}Files:${RESET}     %s\n" "$SCRIPT_DIR"
  printf "  ${CYAN}Commands:${RESET}  %s\n" "$BANNER_CLI_DIR"
fi

printf "\n  ${BOLD}Useful commands${RESET}\n"
printf "  ${DIM}%s${RESET}\n\n" "$(printf '─%.0s' {1..53})"
printf "  ${DIM}# Check health from this host${RESET}\n"
printf "  curl -fsS http://localhost:%s/api/v1/health/services\n\n" "$APP_PORT"
printf "  ${DIM}# View logs${RESET}\n"
printf "  docker compose %s -p %s logs -f pipeshub-ai\n\n" "$COMPOSE_FILES_SHOWN" "$PROJECT_NAME"
if $BANNER_IN_CLONE; then
  printf "  ${DIM}# From the repository root${RESET}\n"
else
  printf "  ${DIM}# From the install directory (curl | bash does not cd your shell)${RESET}\n"
fi
printf "  cd %q\n\n" "$BANNER_CLI_DIR"
printf "  ${DIM}# Stop (data preserved)${RESET}\n"
printf "  ./install.sh --stop\n\n"
printf "  ${DIM}# Upgrade to latest images (or rebuild from source if IMAGE_SOURCE=local)${RESET}\n"
printf "  ./install.sh --upgrade\n\n"
printf "  ${DIM}# Rotate JWT/cookie signing secrets (logs everyone out)${RESET}\n"
printf "  ./install.sh --rotate-signing-secrets\n\n"
printf "  ${DIM}# Reconfigure (re-run wizard)${RESET}\n"
printf "  ./install.sh --reconfigure\n\n"
printf "  ${DIM}# Uninstall and remove all data (irreversible)${RESET}\n"
printf "  ./install.sh --uninstall\n\n"
if $READY; then
  printf "${BOLD}${GREEN}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"
else
  printf "${BOLD}${YELLOW}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"
fi

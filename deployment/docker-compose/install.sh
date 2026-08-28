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
#   ./install.sh --print-env-only  # write .env and print compose command, don't launch
#   ./install.sh --reconfigure   # overwrite an existing .env (re-run wizard)
#   ./install.sh --upgrade       # pull/rebuild images and recreate containers
#   ./install.sh --stop          # stop the running stack (data preserved)
#   ./install.sh --uninstall     # stop the stack and remove all data volumes
#   ./install.sh --help
#
# Environment overrides for CI / scripted installs (all optional):
#   PIPESHUB_DEPLOY_TYPE     full | slim
#   PIPESHUB_GRAPH_DB        arango | neo4j
#   PIPESHUB_BROKER          kafka | redis
#   PIPESHUB_KV_STORE        etcd | redis
#   PIPESHUB_VERSION         image tag (e.g. latest, slim, 0.7.0); for local builds the tag
#                            applied to the locally built image (default: local)
#   PIPESHUB_IMAGE_SOURCE    prebuilt | local (default: prebuilt)
#   PIPESHUB_PORT            host port to expose on (default 3000; 3200 for a separate instance)
#   PIPESHUB_PROJECT         Compose project name (default pipeshub-ai; use to run a second copy)
#   PIPESHUB_PUBLIC_URL      public HTTPS URL for external access (optional)
# ==============================================================================
set -euo pipefail

INSTALLER_VERSION="1.0.0"

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
FLAG_BUILD=false
FLAG_NO_PULL=false
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
      --print-env-only Write .env and print the compose command; do not launch
      --reconfigure    Overwrite an existing .env (re-run the wizard)
      --upgrade        Pull or rebuild images and recreate containers (data preserved)
      --stop           Stop the running stack (data preserved)
      --uninstall      Stop and remove ALL data volumes (irreversible)
  -h, --help           Show this help

Environment overrides (bypass prompts in CI):
  PIPESHUB_DEPLOY_TYPE   full | slim
  PIPESHUB_GRAPH_DB      arango | neo4j
  PIPESHUB_BROKER        kafka | redis
  PIPESHUB_KV_STORE      etcd | redis
  PIPESHUB_IMAGE_SOURCE  prebuilt | local  (default: prebuilt)
  PIPESHUB_NO_PULL       1 | true to skip the image refresh (same as --no-pull)
  PIPESHUB_VERSION       image tag (prebuilt) or local build tag (default: local)
  PIPESHUB_PORT          host port (default: 3000; 3200 when installing a second copy)
  PIPESHUB_PROJECT       Compose project name (default: pipeshub-ai)
  PIPESHUB_PUBLIC_URL    public HTTPS URL (e.g. https://pipeshub.yourdomain.com)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)            FLAG_YES=true ;;
    --version)           [[ $# -lt 2 ]] && die "--version requires a TAG argument (e.g. --version 0.7.0)"; CLI_VERSION="$2"; shift ;;
    --build)             FLAG_BUILD=true ;;
    --no-pull)           FLAG_NO_PULL=true ;;
    --print-env-only)    FLAG_PRINT_ENV_ONLY=true ;;
    --reconfigure)       FLAG_RECONFIGURE=true ;;
    --upgrade)           FLAG_UPGRADE=true ;;
    --stop)              FLAG_STOP=true ;;
    --uninstall)         FLAG_UNINSTALL=true ;;
    -h|--help)           usage; exit 0 ;;
    *) die "Unknown option: $1. Use --help for usage." ;;
  esac
  shift
done

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

# Retrieve an existing value from .env (if the file exists), falling back to
# the supplied default when the key is absent or empty.  Used during
# --reconfigure to preserve secrets that were already used to initialise
# database volumes — regenerating them would break authentication.
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
optional_env_line() {
  local key="$1" val="$2" hint="$3"
  if [[ -n "$val" ]]; then
    printf '%s=%s' "$key" "$val"
  else
    printf '# %s=%s' "$key" "$hint"
  fi
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
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" --env-file "$ENV_FILE" exec -T pipeshub-ai "$@"
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
  docker volume ls --format '{{.Name}}' 2>/dev/null | grep -qx "$1"
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
  export COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down --remove-orphans
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
  export COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down -v --remove-orphans
  success "PipesHub stopped and all data volumes removed (project ${PROJECT_NAME})."
  exit 0
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
# 3. RESOURCE CHECKS (skip for --upgrade; resources are already allocated)
# ==============================================================================
if ! $FLAG_UPGRADE; then

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
  if $IS_WSL; then
    _RAM_MIN_MB=10240
    _RAM_MIN_LABEL="10 GB"
  else
    _RAM_MIN_MB=15000
    _RAM_MIN_LABEL="16 GB"
  fi

  if (( TOTAL_RAM_MB > 0 && TOTAL_RAM_MB < _RAM_MIN_MB )); then
    warn "Low RAM: ${TOTAL_RAM_MB} MB detected. PipesHub recommends a ${_RAM_MIN_LABEL}-class machine."
    warn "The 'slim' deployment may still work on lower-memory machines, but performance may suffer."
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

# --upgrade always reuses the existing .env
if $FLAG_UPGRADE; then
  $ENV_EXISTS || die ".env not found. Run ./install.sh (without --upgrade) to set up first."
  info "Upgrade mode — reusing existing .env."
  set -a; . "$ENV_FILE"; set +a
  SKIP_WIZARD=true
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

  DETECTED_ARANGO=false; DETECTED_NEO4J=false; DETECTED_ETCD=false

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
  if ! $DETECTED_ARANGO && ! $DETECTED_NEO4J; then
    info "No existing graph database volumes found — starting fresh."
  fi

  # ── 6. DEPLOYMENT TYPE ──────────────────────────────────────────────────────
  header "Deployment type"

  printf "\n  ${BOLD}Choose a deployment type:${RESET}\n\n"
  printf "  ${GREEN}[1] Slim${RESET}  — Smaller image (model downloads on first use), fewer containers.\n"
  printf "         Broker: Redis Streams  |  KV store: Redis  |  Graph: Neo4j\n"
  printf "         Recommended for: laptops, low-resource servers, quick evaluations.\n\n"
  printf "  [2] Full  — Larger image with the embedding model bundled; uses Kafka.\n"
  printf "         Broker: Kafka  |  KV store: Redis  |  Graph: Neo4j\n"
  printf "         Recommended for: production servers, air-gapped deployments.\n\n"

  if [[ -n "${PIPESHUB_DEPLOY_TYPE:-}" ]]; then
    DEPLOY_TYPE="$PIPESHUB_DEPLOY_TYPE"
    info "Using PIPESHUB_DEPLOY_TYPE=$DEPLOY_TYPE"
  else
    prompt_choice DEPLOY_TYPE "Deployment type?" "slim" "slim" "full"
  fi

  case "$DEPLOY_TYPE" in
    full) DEFAULT_IMAGE_TAG="latest"; DEFAULT_GRAPH="neo4j";  DEFAULT_BROKER="kafka"; DEFAULT_KV="redis" ;;
    *)    DEPLOY_TYPE="slim"
          DEFAULT_IMAGE_TAG="slim";   DEFAULT_GRAPH="neo4j";  DEFAULT_BROKER="redis"; DEFAULT_KV="redis" ;;
  esac

  # Volume detection overrides graph/kv defaults
  if $DETECTED_ARANGO && ! $DETECTED_NEO4J; then
    DEFAULT_GRAPH="arango"; info "Defaulting graph DB to ArangoDB to reuse existing data volume."
  elif $DETECTED_NEO4J && ! $DETECTED_ARANGO; then
    DEFAULT_GRAPH="neo4j";  info "Defaulting graph DB to Neo4j to reuse existing data volume."
  fi
  if $DETECTED_ETCD; then
    DEFAULT_KV="etcd"; info "Defaulting KV store to etcd to reuse existing data volume."
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
      info "Building from source, image will be tagged: pipeshubai/pipeshub-ai:${IMAGE_TAG}"
    elif [[ -n "${PIPESHUB_VERSION:-}" ]]; then
      IMAGE_TAG="$PIPESHUB_VERSION"
    else
      IMAGE_TAG="local"
      info "Building from source — image will be tagged: pipeshubai/pipeshub-ai:local"
    fi
    SANDBOX_DOCKER_IMAGE="pipeshubai/pipeshub-sandbox:${IMAGE_TAG}"
    warn "First local build can take 10–30+ minutes depending on your machine."

  else
    IMAGE_SOURCE="prebuilt"
    SANDBOX_DOCKER_IMAGE=""  # let compose.yml default apply: pipeshubai/pipeshub-sandbox:${IMAGE_TAG}
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

  # ── 9. RESOLVE COMPOSE_PROFILES ─────────────────────────────────────────────
  PROFILES=()
  case "$GRAPH_DB" in
    arango*) PROFILES+=("graph-arango") ;;
    neo4j*)  PROFILES+=("graph-neo4j") ;;
  esac
  [[ "$KV_STORE"  == "etcd"  ]] && PROFILES+=("kv-etcd")
  [[ "$BROKER"    == "kafka" ]] && PROFILES+=("broker-kafka")
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
  MONGO_USERNAME="$(get_existing_val MONGO_USERNAME "admin")"
  MONGO_PASSWORD="$(get_existing_val MONGO_PASSWORD "$(gen_secret 16)")"
  REDIS_PASSWORD="$(get_existing_val REDIS_PASSWORD "$(gen_secret 16)")"
  QDRANT_API_KEY="$(get_existing_val QDRANT_API_KEY "$(gen_secret 20)")"

  # Optional MongoDB knobs from env.template. The wizard never asks about these,
  # but .env is rewritten in full below, so read back anything the operator set
  # by hand -- otherwise --reconfigure silently reverts their tuning and MongoDB
  # goes back to the defaults that made them set it in the first place.
  MONGO_GLIBC_TUNABLES="$(get_existing_val MONGO_GLIBC_TUNABLES "")"
  MONGO_IMAGE_TAG="$(get_existing_val MONGO_IMAGE_TAG "")"
  MONGO_CACHE_GB="$(get_existing_val MONGO_CACHE_GB "")"
  MONGO_MEMORY_LIMIT="$(get_existing_val MONGO_MEMORY_LIMIT "")"

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
    info "No public URL set — local access only (http://localhost:${APP_PORT})."
    info "You can add FRONTEND_PUBLIC_URL to .env later."
  fi

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

# ── Compose project (isolates volumes/network from other copies on this host) ─
COMPOSE_PROJECT_NAME=${PROJECT_NAME}

# ── Compose profiles (controls which optional containers start) ──────────────
# Values: graph-arango | graph-neo4j | kv-etcd | broker-kafka  (comma-separated)
COMPOSE_PROFILES=${COMPOSE_PROFILES}

# ── Core ─────────────────────────────────────────────────────────────────────
NODE_ENV=production
LOG_LEVEL=info
SECRET_KEY=${SECRET_KEY}

# Public URL — HTTPS domain for cloud/external deployments (leave blank for localhost)
# Required for OAuth callbacks, webhook integrations, and browser security.
# Example: https://pipeshub.yourdomain.com
FRONTEND_PUBLIC_URL=${FRONTEND_PUBLIC_URL}

# Host port PipesHub is exposed on
APP_PORT=${APP_PORT}

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

# ── Key-value / config store ─────────────────────────────────────────────────
# "redis" (default, uses always-on Redis) | "etcd" (dedicated container)
KV_STORE_TYPE=${KV_STORE}

# ── Message broker ──────────────────────────────────────────────────────────
# "redis" (Redis Streams, default) | "kafka" (Kafka + Zookeeper)
MESSAGE_BROKER=${BROKER}
REDIS_STREAMS_MAXLEN=500000

# ── Redis ────────────────────────────────────────────────────────────────────
REDIS_PASSWORD=${REDIS_PASSWORD}

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

# ── Qdrant ───────────────────────────────────────────────────────────────────
QDRANT_API_KEY=${QDRANT_API_KEY}

# ── Indexing concurrency ─────────────────────────────────────────────────────
# Do not write MAX_CONCURRENT_* / EMBEDDING_*_CONCURRENCY here. Empty values
# crash Hub slim (int("")); omitting them lets slim use built-in defaults and
# lets new images size from CPU. Set an integer in .env only to cap.
# Governor slot ratios (1 / 10 / 100) stay empty.
GOVERNOR_HEAVY_PARSE_SLOTS_PER_CPU=
GOVERNOR_LIGHT_PARSE_SLOTS_PER_CPU=
GOVERNOR_INDEX_SLOTS_PER_PARSE_SLOT=
GOVERNOR_HEAVY_PARSE_WORKING_SET_GB=
INDEXING_UVICORN_WORKERS=1
PARSING_UVICORN_WORKERS=1
DOCLING_UVICORN_WORKERS=1
LOCAL_DOCLING_PARSE_WORKERS=1
PDF_OCR_DETECTION_WORKERS=1

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

  success ".env written to $ENV_FILE (permissions: owner read/write only)"

fi  # end wizard

# Reused .env files from older installs may still be 644. Always lock them
# before we print secrets in the summary or start containers.
if [[ -f "$ENV_FILE" ]] && ! chmod 600 "$ENV_FILE"; then
  die "Could not restrict permissions on $ENV_FILE"
fi

# Keep .env and -p in sync so later --stop / --uninstall only tear down this copy.
persist_env_var "COMPOSE_PROJECT_NAME" "$PROJECT_NAME"

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
_app_mem_mb="$(_parse_mem_mb "${APP_MEMORY_LIMIT:-12G}")" || _app_mem_mb=""
_app_memswap_mb="$(_parse_mem_mb "${APP_MEMSWAP_LIMIT:-16G}")" || _app_memswap_mb=""
if [[ -n "$_app_mem_mb" && -n "$_app_memswap_mb" ]] && (( _app_memswap_mb < _app_mem_mb )); then
  die "APP_MEMSWAP_LIMIT (${APP_MEMSWAP_LIMIT:-16G}) must be >= APP_MEMORY_LIMIT (${APP_MEMORY_LIMIT:-12G}). Raise APP_MEMSWAP_LIMIT in .env (or raise both together) before launching."
fi

# Resolve APP_PORT from .env when wizard was skipped (upgrade / reuse)
APP_PORT="${APP_PORT:-3000}"
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

# On reuse/upgrade the wizard's interactive port scan was skipped. Confirm the
# app port is free — or already held by our own stack (a restart) — and otherwise
# fail clearly instead of letting docker emit a cryptic bind error mid-launch.
if ${SKIP_WIZARD:-false}; then
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
printf "  %-22s %s\n" "Graph DB:"      "${DATA_STORE:-(unset)}"
printf "  %-22s %s\n" "KV store:"      "${KV_STORE_TYPE:-redis}"
printf "  %-22s %s\n" "Broker:"        "${MESSAGE_BROKER:-redis}"
printf "  %-22s %s\n" "Profiles:"      "${COMPOSE_PROFILES:-(none)}"
printf "  %-22s %s\n" "Local URL:"     "http://localhost:${APP_PORT}"
if [[ -n "${FRONTEND_PUBLIC_URL:-}" ]]; then
  printf "  %-22s %s\n" "Public URL:"  "${FRONTEND_PUBLIC_URL}"
fi
printf "\n"

# --print-env-only: show the compose command and exit
if $FLAG_PRINT_ENV_ONLY; then
  _build_flag=""
  [[ "${IMAGE_SOURCE:-prebuilt}" == "local" ]] && _build_flag=" --build"
  printf "\n"
  info "Run the following to start PipesHub:"
  printf "\n  ${BOLD}COMPOSE_PROFILES=%s \\\\\n    docker compose -f %s -p %s up -d%s${RESET}\n\n" \
    "${COMPOSE_PROFILES:-}" "$COMPOSE_FILE" "$PROJECT_NAME" "$_build_flag"
  success "Done (--print-env-only mode; not launching)."
  exit 0
fi

# Confirm before launching (skip for --upgrade which already confirmed intent)
if ! $FLAG_YES && ! $FLAG_UPGRADE; then
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

# ==============================================================================
# 15. LAUNCH
# ==============================================================================
header "$( $FLAG_UPGRADE && echo 'Upgrading PipesHub' || echo 'Launching PipesHub' )"

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

compose_up()        { docker compose "${_PROGRESS[@]}" -f "$COMPOSE_FILE" -p "$PROJECT_NAME" --env-file "$ENV_FILE" up -d "$@"; }
compose_logs_tail() { docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" --env-file "$ENV_FILE" logs --tail 30 2>&1 || true; }

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

_DO_PULL="$(should_pull_image "$_USE_BUILD" "$FLAG_NO_PULL" "${PIPESHUB_NO_PULL:-}")"
# Pinning a specific tag (--version / PIPESHUB_VERSION) still benefits from the
# pull: it fetches exactly that immutable tag rather than a moving :latest, so
# reproducibility is preserved while a stale local copy is corrected.
_APP_IMAGE="pipeshubai/pipeshub-ai:${IMAGE_TAG:-latest}"
_SANDBOX_IMAGE="${SANDBOX_DOCKER_IMAGE:-pipeshubai/pipeshub-sandbox:${IMAGE_TAG:-latest}}"

if project_has_pinned_container_names; then
  warn_pinned_container_rename
fi

# Baseline before anything starts, so the post-start checks below can tell a
# crash loop from restarts this run had nothing to do with.
snapshot_mongo_restarts

if $_USE_BUILD; then
  $FLAG_UPGRADE && info "Rebuilding image from source for tag: ${IMAGE_TAG:-local}..."
  info "Building image from source and starting containers..."
  info "(This may take 10–30+ minutes on first run)"
  if ! compose_up_with_mongo_heal "up --build" --build; then
    die "Fix the error above and re-run install.sh."
  fi
else
  if [[ "$_DO_PULL" == true ]]; then
    info "Refreshing the PipesHub images ($_APP_IMAGE, $_SANDBOX_IMAGE)... (pass --no-pull to keep cached images)"
    # App and sandbox images share the moving IMAGE_TAG (often :latest). Infra
    # images use pinned tags and are fetched by `up -d` when absent.
    # A pull failure is non-fatal when an image is already cached, so a flaky
    # network or a temporary registry outage does not block a working install.
    if ! docker compose "${_PROGRESS[@]}" \
        -f "$COMPOSE_FILE" \
        -p "$PROJECT_NAME" \
        --env-file "$ENV_FILE" \
        pull pipeshub-ai sandbox-image 2>&1; then
      if docker image inspect "$_APP_IMAGE" >/dev/null 2>&1 &&
          docker image inspect "$_SANDBOX_IMAGE" >/dev/null 2>&1; then
        warn "Could not refresh images; continuing with cached copies if present."
      else
        warn "Could not pull a required image and it is not cached locally — the next step may fail."
        warn "On an air-gapped host, preload the image (docker load) and re-run with --no-pull."
      fi
    fi
  else
    info "Skipping image refresh; using locally cached images (--no-pull)."
  fi
  info "Starting containers..."
  if ! compose_up_with_mongo_heal "up"; then
    die "Fix the error above and re-run install.sh."
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
    warn "  docker compose -f ${COMPOSE_FILE} -p ${PROJECT_NAME} logs -f pipeshub-ai"
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
          warn "  docker compose -f ${COMPOSE_FILE} -p ${PROJECT_NAME} logs -f pipeshub-ai"
        fi
      else
        CONTAINER_HEALTHY=false
        warn "Applied the MongoDB tunable, but restarting the stack failed."
        warn "  docker compose -f ${COMPOSE_FILE} -p ${PROJECT_NAME} logs mongodb"
      fi
    else
      warn "MongoDB restarted ${_mongo_restarts} time(s) during this install."
      warn "The stack is healthy right now, but a database that keeps restarting"
      warn "drops every connection each time it goes. Check why:"
      warn "  docker compose -f ${COMPOSE_FILE} -p ${PROJECT_NAME} logs --tail 40 mongodb"
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
  warn "  docker compose -f ${COMPOSE_FILE} -p ${PROJECT_NAME} logs -f pipeshub-ai"
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
  warn "  Diagnose: docker compose -f ${COMPOSE_FILE} -p ${PROJECT_NAME} exec -T pipeshub-ai curl -s -o /dev/null -m 6 -w '%{http_code}\\n' https://1.1.1.1/"
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
printf "  docker compose -f %s -p %s logs -f pipeshub-ai\n\n" "$COMPOSE_FILE" "$PROJECT_NAME"
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
printf "  ${DIM}# Reconfigure (re-run wizard)${RESET}\n"
printf "  ./install.sh --reconfigure\n\n"
printf "  ${DIM}# Uninstall and remove all data (irreversible)${RESET}\n"
printf "  ./install.sh --uninstall\n\n"
if $READY; then
  printf "${BOLD}${GREEN}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"
else
  printf "${BOLD}${YELLOW}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"
fi

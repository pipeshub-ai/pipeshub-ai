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
#   PIPESHUB_PORT            host port to expose on (default 3000)
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
PROJECT_NAME="pipeshub-ai"
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
  PIPESHUB_PORT          host port (default: 3000)
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
    if [[ "$line" == "${key}="* ]]; then
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

# List working directories of RUNNING containers in our Compose project that
# were launched from a directory other than this one. Compose stamps each
# container with com.docker.compose.project.working_dir. Because the project
# name, container names, network, and volumes are all fixed, a stack started
# from elsewhere shares all of them — launching from here reconciles/takes over
# that deployment instead of starting an independent one.
compose_other_working_dirs() {
  docker ps \
    --filter "label=com.docker.compose.project=${PROJECT_NAME}" \
    --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null \
    | grep -v '^$' | sort -u | grep -vxF "$SCRIPT_DIR" || true
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
# ==============================================================================
[[ -t 1 ]] && clear 2>/dev/null || true
cat <<'BANNER'

  ██████╗ ██╗██████╗ ███████╗███████╗██╗  ██╗██╗   ██╗██████╗
  ██╔══██╗██║██╔══██╗██╔════╝██╔════╝██║  ██║██║   ██║██╔══██╗
  ██████╔╝██║██████╔╝█████╗  ███████╗███████║██║   ██║██████╔╝
  ██╔═══╝ ██║██╔═══╝ ██╔══╝  ╚════██║██╔══██║██║   ██║██╔══██╗
  ██║     ██║██║     ███████╗███████║██║  ██║╚██████╔╝██████╔╝
  ╚═╝     ╚═╝╚═╝     ╚══════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝
                        AI Platform Installer
BANNER
printf "  ${DIM}v%s${RESET}\n\n" "$INSTALLER_VERSION"

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

# ==============================================================================
# 2b. EARLY-EXIT COMMANDS (--stop, --uninstall)
# These run without resource checks since they operate on an existing deployment.
# ==============================================================================
if $FLAG_STOP; then
  header "Stopping PipesHub"
  if [[ -f "$ENV_FILE" ]]; then set -a; . "$ENV_FILE"; set +a; fi
  # Enable ALL profiles so `down` removes every profile-gated container
  # (ArangoDB, Neo4j, etcd, Kafka/Zookeeper) regardless of which profile this
  # .env currently selects. Otherwise a container started under a different
  # profile stays attached to the network and blocks its removal
  # ("Resource is still in use"). --remove-orphans clears containers left by a
  # previously-active profile too.
  export COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down --remove-orphans
  success "PipesHub stopped. Data volumes are preserved."
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
  # Enable ALL profiles so down -v includes every profile-gated service's
  # volume (ArangoDB, Neo4j, etcd, Kafka/Zookeeper) regardless of which
  # profile was active for this deployment.  Without this, volumes from a
  # previously-used profile (e.g. arango_data after switching to neo4j) would
  # be silently left behind.
  export COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down -v --remove-orphans
  success "PipesHub stopped and all data volumes removed."
  exit 0
fi

# ==============================================================================
# 2c. CROSS-DIRECTORY DEPLOYMENT GUARD
# Warn before launching if PipesHub is already running from another directory.
# ==============================================================================
_OTHER_DIRS="$(compose_other_working_dirs)"
if [[ -n "$_OTHER_DIRS" ]]; then
  header "Existing deployment detected"
  warn "PipesHub (project '${PROJECT_NAME}') is already running from another directory:"
  while IFS= read -r _d; do [[ -n "$_d" ]] && warn "    $_d"; done <<< "$_OTHER_DIRS"
  warn "Container names, network, and data volumes are shared by project name, so"
  warn "launching from here manages that same stack rather than a separate one."
  warn "If this directory has a different .env, its secrets may not match the existing"
  warn "data volumes and can cause database auth failures."
  warn "Recommended: manage PipesHub from one directory, or run --uninstall there first."
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

  # System RAM — 16 GB-class machine recommended
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
elif $ENV_EXISTS && ! $FLAG_RECONFIGURE; then
  # .env exists and --reconfigure was not requested: always reuse without prompting.
  # Use --reconfigure to overwrite.
  info "Existing .env found — reusing. Pass --reconfigure to overwrite."
  set -a; . "$ENV_FILE"; set +a
  SKIP_WIZARD=true
else
  SKIP_WIZARD=false
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
    arango*) DATA_STORE="arangodb"; GRAPH_DB_TYPE="arangodb" ;;
    neo4j*)  DATA_STORE="neo4j";    GRAPH_DB_TYPE="neo4j" ;;
  esac

  # ── 10. PORT SELECTION ──────────────────────────────────────────────────────
  header "Port selection"

  DESIRED_PORT="${PIPESHUB_PORT:-3000}"
  if ! $FLAG_YES; then
    prompt_input DESIRED_PORT "Port to expose PipesHub on?" "$DESIRED_PORT"
  fi

  # Validate it's a number
  [[ "$DESIRED_PORT" =~ ^[0-9]+$ ]] || die "Invalid port: $DESIRED_PORT"
  APP_PORT="$DESIRED_PORT"
  MAX_PORT=$(( DESIRED_PORT + 20 ))

  while port_in_use "$APP_PORT" 2>/dev/null && (( APP_PORT < MAX_PORT )); do
    warn "Port ${APP_PORT} is in use, trying $(( APP_PORT + 1 ))..."
    APP_PORT=$(( APP_PORT + 1 ))
  done

  if port_in_use "$APP_PORT" 2>/dev/null; then
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
# DATA_STORE / GRAPH_DB_TYPE: "arangodb" or "neo4j"
DATA_STORE=${DATA_STORE}
GRAPH_DB_TYPE=${GRAPH_DB_TYPE}

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

# ── Qdrant ───────────────────────────────────────────────────────────────────
QDRANT_API_KEY=${QDRANT_API_KEY}

# ── Indexing concurrency ─────────────────────────────────────────────────────
MAX_CONCURRENT_PARSING=5
MAX_CONCURRENT_INDEXING=7
MAX_PENDING_INDEXING_TASKS=40
INDEXING_UVICORN_WORKERS=1
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

# ==============================================================================
# 14. DEPLOYMENT SUMMARY
# ==============================================================================
header "Deployment summary"

# Source .env so all variables are available for display (and for launch)
set -a; . "$ENV_FILE"; set +a

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
    GRAPH_DB_TYPE="$DATA_STORE"

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
    persist_env_var GRAPH_DB_TYPE "$GRAPH_DB_TYPE"
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

# ==============================================================================
# 15. LAUNCH
# ==============================================================================
header "$( $FLAG_UPGRADE && echo 'Upgrading PipesHub' || echo 'Launching PipesHub' )"

export COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"

# Determine whether to pass --build to compose up.
# IMAGE_SOURCE is read from the sourced .env (covers reuse / upgrade paths too).
_USE_BUILD=false
[[ "${IMAGE_SOURCE:-prebuilt}" == "local" ]] && _USE_BUILD=true

# Compose's default "tty" progress redraws a single block via cursor-movement
# escapes. When stdout is captured (terminal logs, `curl | bash`, CI), those
# escapes don't collapse and every frame is recorded, producing hundreds of
# duplicated "[+] Running N/17" blocks. Plain progress is append-only and stays
# readable in every context.
_PROGRESS=(--progress plain)

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

if $_USE_BUILD; then
  $FLAG_UPGRADE && info "Rebuilding image from source for tag: ${IMAGE_TAG:-local}..."
  info "Building image from source and starting containers..."
  info "(This may take 10–30+ minutes on first run)"
  if ! docker compose "${_PROGRESS[@]}" \
      -f "$COMPOSE_FILE" \
      -p "$PROJECT_NAME" \
      --env-file "$ENV_FILE" \
      up -d --build; then
    error "docker compose up --build failed. Last 30 lines of container logs:"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" --env-file "$ENV_FILE" logs --tail 30 2>&1 || true
    die "Fix the build error above and re-run install.sh."
  fi
else
  if [[ "$_DO_PULL" == true ]]; then
    info "Refreshing the PipesHub image ($_APP_IMAGE)... (pass --no-pull to keep the cached image)"
    # Only the app image is on a moving :latest; infra images use pinned tags and
    # are fetched by `up -d` when absent, so refreshing just pipeshub-ai is enough.
    # A pull failure is non-fatal when an image is already cached, so a flaky
    # network or a temporary registry outage does not block a working install.
    if ! docker compose "${_PROGRESS[@]}" \
        -f "$COMPOSE_FILE" \
        -p "$PROJECT_NAME" \
        --env-file "$ENV_FILE" \
        pull pipeshub-ai 2>&1; then
      if docker image inspect "$_APP_IMAGE" >/dev/null 2>&1; then
        warn "Could not refresh the image; continuing with the cached $_APP_IMAGE."
      else
        warn "Could not pull $_APP_IMAGE and none is cached locally — the next step may fail."
        warn "On an air-gapped host, preload the image (docker load) and re-run with --no-pull."
      fi
    fi
  else
    info "Skipping image refresh; using the locally cached $_APP_IMAGE (--no-pull)."
  fi
  info "Starting containers..."
  if ! docker compose "${_PROGRESS[@]}" \
      -f "$COMPOSE_FILE" \
      -p "$PROJECT_NAME" \
      --env-file "$ENV_FILE" \
      up -d; then
    error "docker compose up failed. Last 30 lines of container logs:"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" --env-file "$ENV_FILE" logs --tail 30 2>&1 || true
    die "Fix the error above and re-run install.sh."
  fi
fi

# ==============================================================================
# 16. HEALTH WAIT
# Uses docker exec so curl and python3 run inside the container — no host deps.
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
  docker exec pipeshub-ai \
    curl -sf http://localhost:3000/api/v1/health/services \
    -o /tmp/pipeshub_hc.json 2>/dev/null || return 1
  docker exec pipeshub-ai python3 -c "
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

# Poll until healthy or the deadline passes. On a TTY, redraw a single spinner
# line in place (clean, one line); when output is captured (logs, curl | bash,
# CI) emit a sparse heartbeat instead so transcripts don't fill with frames.
ELAPSED=0
CHECK_EVERY=5
HEARTBEAT_EVERY=30
START_TS=$SECONDS
_spinner=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
_spin=0
_CRASH_REPORT=""
_is_tty=false; [[ -t 1 ]] && _is_tty=true

while (( ELAPSED < HEALTH_WAIT_SECS )); do
  if (( ELAPSED % CHECK_EVERY == 0 )) && app_is_healthy; then
    CONTAINER_HEALTHY=true
    break
  fi
  # After a grace period for normal startup churn (e.g. Kafka waiting on
  # Zookeeper), give up early if a container is clearly restart-looping — it will
  # not recover on its own, so there is no point waiting out the full timeout.
  if (( ELAPSED >= 30 && ELAPSED % 15 == 0 )); then
    _CRASH_REPORT="$(crash_looping_containers)"
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
  warn "  • exit 139            → the service crashed (segfault). Usually a corrupted data"
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
  docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'pipeshub-ai' || return 1
  if docker exec pipeshub-ai sh -c \
      'command -v curl >/dev/null 2>&1 && curl -sf -m 8 -4 -o /dev/null https://1.1.1.1/ 2>/dev/null'; then
    return 0
  fi
  docker exec pipeshub-ai sh -c \
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
  warn "  Diagnose: docker exec pipeshub-ai curl -s -o /dev/null -m 6 -w '%{http_code}\\n' https://1.1.1.1/"
  warn "  Docs: deployment/docker-compose/ADVANCED_DEPLOYMENT.md#container-outbound-connectivity"
}

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'pipeshub-ai'; then
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
printf "\n  ${BOLD}Useful commands${RESET}\n"
printf "  ${DIM}%s${RESET}\n\n" "$(printf '─%.0s' {1..53})"
printf "  ${DIM}# Check health from this host${RESET}\n"
printf "  curl -fsS http://localhost:%s/api/v1/health/services\n\n" "$APP_PORT"
printf "  ${DIM}# View logs${RESET}\n"
printf "  docker compose -f %s -p %s logs -f pipeshub-ai\n\n" "$COMPOSE_FILE" "$PROJECT_NAME"
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

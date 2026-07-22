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
HEALTH_WAIT_SECS=300
# APP_PORT and HEALTH_URL are resolved later (after port selection in the wizard)

# ── CLI flags ─────────────────────────────────────────────────────────────────
FLAG_YES=false
FLAG_PRINT_ENV_ONLY=false
FLAG_RECONFIGURE=false
FLAG_UPGRADE=false
FLAG_STOP=false
FLAG_UNINSTALL=false
FLAG_BUILD=false
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
  export COMPOSE_PROFILES="${COMPOSE_PROFILES:-}"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down
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
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down -v
  success "PipesHub stopped and all data volumes removed."
  exit 0
fi

# ==============================================================================
# 3. RESOURCE CHECKS (skip for --upgrade; resources are already allocated)
# ==============================================================================
if ! $FLAG_UPGRADE; then

  # System RAM — minimum 15 GB required
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
  if $IS_WSL; then
    _RAM_MIN_MB=10240
    _RAM_MIN_LABEL="10 GB"
  else
    _RAM_MIN_MB=15360
    _RAM_MIN_LABEL="15 GB"
  fi

  if (( TOTAL_RAM_MB > 0 && TOTAL_RAM_MB < _RAM_MIN_MB )); then
    warn "Low RAM: ${TOTAL_RAM_MB} MB detected. PipesHub recommends at least ${_RAM_MIN_LABEL}."
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
    _docker_mem="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
    _docker_mem_mb=$(( _docker_mem / 1024 / 1024 ))
    if (( _docker_mem_mb > 0 && _docker_mem_mb < 8192 )); then
      warn "Docker Desktop has only ${_docker_mem_mb} MB allocated to its VM. Recommend at least 8 GB in Docker Desktop → Settings → Resources → Memory."
    fi
  fi

  # Docker Desktop on Windows (Git Bash) — host RAM is not readable from Git Bash,
  # so probe the Docker Desktop VM allocation directly (same approach as macOS).
  if $IS_WINDOWS; then
    _docker_mem="$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)"
    _docker_mem_mb=$(( _docker_mem / 1024 / 1024 ))
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

  # Backup existing .env before overwriting
  if [[ -f "$ENV_FILE" ]]; then
    _backup="${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
    cp "$ENV_FILE" "$_backup"
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

  success ".env written to $ENV_FILE"

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

printf "\n"
printf "  %-22s %s\n" "Image source:"  "${IMAGE_SOURCE:-prebuilt}"
printf "  %-22s %s\n" "Image tag:"     "${IMAGE_TAG:-latest}"
printf "  %-22s %s\n" "Graph DB:"      "${DATA_STORE:-arangodb}"
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

if $FLAG_UPGRADE; then
  if $_USE_BUILD; then
    info "Rebuilding image from source for tag: ${IMAGE_TAG:-local}..."
  else
    info "Pulling new images for tag: ${IMAGE_TAG:-latest}..."
    docker compose \
      -f "$COMPOSE_FILE" \
      -p "$PROJECT_NAME" \
      --env-file "$ENV_FILE" \
      pull 2>&1 || true
  fi
fi

if $_USE_BUILD; then
  info "Building image from source and starting containers..."
  info "(This may take 10–30+ minutes on first run)"
  if ! docker compose \
      -f "$COMPOSE_FILE" \
      -p "$PROJECT_NAME" \
      --env-file "$ENV_FILE" \
      up -d --build; then
    error "docker compose up --build failed. Last 30 lines of container logs:"
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" --env-file "$ENV_FILE" logs --tail 30 2>&1 || true
    die "Fix the build error above and re-run install.sh."
  fi
else
  info "Starting containers..."
  if ! docker compose \
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

ELAPSED=0
INTERVAL=10
HEALTHY=false

while (( ELAPSED < HEALTH_WAIT_SECS )); do
  if docker exec pipeshub-ai \
      curl -sf http://localhost:3000/api/v1/health/services \
      -o /tmp/pipeshub_hc.json 2>/dev/null && \
     docker exec pipeshub-ai \
      python3 -c "
import json, sys
d = json.load(open('/tmp/pipeshub_hc.json'))
s = d.get('services', {}) or {}
# embedding is intentionally excluded: on first run it downloads its model
# and may remain 'unhealthy' for several minutes; it is not required for
# the core application to be usable.
required = ('query', 'connector', 'indexing', 'docling')
ok = all(s.get(k) == 'healthy' for k in required)
sys.exit(0 if ok else 1)
" 2>/dev/null; then
    HEALTHY=true
    break
  fi
  printf "  Waiting... %ds elapsed\n" "$ELAPSED"
  sleep "$INTERVAL"
  ELAPSED=$(( ELAPSED + INTERVAL ))
done

printf "\n"

if $HEALTHY; then
  success "PipesHub is healthy!"
else
  warn "Health check did not pass within ${HEALTH_WAIT_SECS}s."
  warn "Services may still be starting. Check logs:"
  warn "  docker compose -f ${COMPOSE_FILE} -p ${PROJECT_NAME} logs -f pipeshub-ai"
fi

# ==============================================================================
# 17. SUCCESS BANNER
# ==============================================================================
printf "\n${BOLD}${GREEN}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"
printf "  ${BOLD}PipesHub AI is running!${RESET}\n\n"
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
printf "${BOLD}${GREEN}%s${RESET}\n\n" "$(printf '━%.0s' {1..64})"

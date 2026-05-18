#!/usr/bin/env bash

# PipesHub installation script
# Downloads the production Docker Compose files, ensures Docker is available,
# generates secrets, and starts the stack.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-pipeshub}"
GITHUB_RAW="https://raw.githubusercontent.com/pipeshub-ai/pipeshub-ai/main/deployment/docker-compose"
COMPOSE_FILE="docker-compose.prod.yml"
ENV_TEMPLATE="env.template"
PROJECT_NAME="pipeshub-ai"

# --- Output helpers ---
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'
print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error()   { echo -e "${RED}✗${NC} $1"; }
print_info()    { echo -e "${YELLOW}ℹ${NC} $1"; }
print_step()    { echo ""; echo -e "${BLUE}${BOLD}=== $1 ===${NC}"; echo ""; }

echo ""
echo -e "${BLUE}${BOLD}PipesHub Installation${NC}"
echo "====================="

# --- Downloader detection (curl with wget fallback) ---
if command -v curl &> /dev/null; then
    DOWNLOADER="curl"
elif command -v wget &> /dev/null; then
    DOWNLOADER="wget"
else
    print_error "Neither curl nor wget found. Please install one and retry."
    exit 1
fi

download_file() {
    local url="$1" output="$2"
    if [[ "$DOWNLOADER" == "curl" ]]; then
        curl -fsSL --retry 3 --retry-delay 2 -o "$output" "$url"
    else
        wget -q --tries=3 --timeout=20 -O "$output" "$url"
    fi
}

# --- Install Docker if missing (Linux only) ---
print_step "Checking Docker"
if ! command -v docker &> /dev/null; then
    if [[ "$OSTYPE" == "linux-gnu"* ]] || [[ -n "${WSL_DISTRO_NAME:-}" ]]; then
        print_info "Docker not found. Installing via get.docker.com..."
        download_file "https://get.docker.com" /tmp/get-docker.sh
        sudo sh /tmp/get-docker.sh
        rm -f /tmp/get-docker.sh
        sudo systemctl enable --now docker 2>/dev/null || sudo service docker start 2>/dev/null || true
        if ! command -v docker &> /dev/null; then
            print_error "Docker installation failed. See https://docs.docker.com/get-docker/"
            exit 1
        fi
        print_success "Docker installed"
    else
        print_error "Docker is required. Install it from https://docs.docker.com/get-docker/"
        exit 1
    fi
else
    print_success "Docker is installed"
fi

# --- Determine compose command ---
if docker compose version &> /dev/null; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose &> /dev/null; then
    COMPOSE_CMD="docker-compose"
else
    print_error "Docker Compose is required. See https://docs.docker.com/compose/install/"
    exit 1
fi
print_success "Docker Compose is available"

# --- Check Docker daemon ---
if ! docker info &> /dev/null; then
    print_error "Docker daemon is not running. Start Docker and retry."
    exit 1
fi
print_success "Docker daemon is running"

# --- Download compose files ---
print_step "Downloading deployment files"
mkdir -p "$INSTALL_DIR"
download_file "${GITHUB_RAW}/${COMPOSE_FILE}" "${INSTALL_DIR}/${COMPOSE_FILE}"
print_success "Downloaded ${COMPOSE_FILE}"
download_file "${GITHUB_RAW}/${ENV_TEMPLATE}" "${INSTALL_DIR}/${ENV_TEMPLATE}"
print_success "Downloaded ${ENV_TEMPLATE}"

# --- Generate .env with secrets ---
print_step "Configuring environment"
ENV_FILE="${INSTALL_DIR}/.env"
if [[ -f "$ENV_FILE" ]]; then
    print_info "Existing .env found — keeping current configuration"
else
    if ! command -v openssl &> /dev/null; then
        print_error "openssl is required to generate secrets but was not found."
        exit 1
    fi
    cp "${INSTALL_DIR}/${ENV_TEMPLATE}" "$ENV_FILE"
    set_env() { sed -i.bak "s|^$1=.*|$1=$2|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"; }
    set_env NODE_ENV       "production"
    set_env IMAGE_TAG      "latest"
    set_env SECRET_KEY     "$(openssl rand -hex 32)"
    set_env ARANGO_PASSWORD "$(openssl rand -hex 16)"
    set_env MONGO_PASSWORD  "$(openssl rand -hex 16)"
    set_env QDRANT_API_KEY  "$(openssl rand -hex 16)"
    print_success "Created .env with generated secrets"
fi

# --- Start the stack ---
print_step "Starting PipesHub"
(cd "$INSTALL_DIR" && $COMPOSE_CMD -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d)

echo ""
print_success "PipesHub is starting up!"
echo ""
print_info "Access it at: ${BOLD}http://localhost:3000${NC}"
print_info "View logs:    cd ${INSTALL_DIR} && ${COMPOSE_CMD} -f ${COMPOSE_FILE} -p ${PROJECT_NAME} logs -f"
print_info "Stop:         cd ${INSTALL_DIR} && ${COMPOSE_CMD} -f ${COMPOSE_FILE} -p ${PROJECT_NAME} down"
echo ""

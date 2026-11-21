#!/usr/bin/env bash
# ==============================================================================
# D1: the installer's code-execution decisions -- pure bash, no Docker, no
# network, no root. The real functions are extracted from install.sh and run
# against stubbed commands on PATH.
#
#   IN-1  sandbox_plan: what a run does for each deploy type and SANDBOX_MODE
#   IN-2  derive_compose_profiles pairs the sandbox profile with the mode
#   IN-3  gvisor_plan: install only on a Linux Docker Engine it can administer
#   IN-4  install_gvisor: verified download, install, register with
#         --oci-seccomp, reload; a bad checksum installs nothing; ensure_gvisor is
#         a no-op once runsc is listed with its syscall filter
#   IN-4b runsc registered without --oci-seccomp: read as Go reads the flag, and
#         re-registered with it (its other arguments kept) only on an explicit yes
#   IN-5  migrate_sandbox_to_broker: switch once the broker is healthy, else
#         keep the socket through the legacy overlay; after the switch no
#         legacy proxy container keeps the socket (SOCKPROXY-1)
#   IN-6  the wizard writes SANDBOX_MODE (broker; blank for eval)
#   IN-7  static: syntax, shellcheck on the D1 functions when available
#
# Run: bash deployment/docker-compose/tests/sandbox_plane_test.sh
# ==============================================================================
# The extracted functions read globals this file sets inside ( ) groups and
# stubs they call indirectly; expected strings are literal installer text.
# shellcheck disable=SC2015,SC2016,SC2030,SC2031,SC2034,SC2317
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALLER="$COMPOSE_DIR/install.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PASS_FILE="$TMP_ROOT/.pass"; FAIL_FILE="$TMP_ROOT/.fail"
: >"$PASS_FILE"; : >"$FAIL_FILE"
pass() { printf "  ok   - %s\n" "$1"; echo x >>"$PASS_FILE"; }
fail() { printf "  FAIL - %s\n" "$1"; echo "$1" >>"$FAIL_FILE"; }
eq() { # eq "desc" actual expected
  if [[ "$2" == "$3" ]]; then pass "$1"; else
    fail "$1"; printf "         expected: %s\n         got:      %s\n" "$3" "$2"; fi
}
contains() { # contains "desc" haystack needle
  if [[ "$2" == *"$3"* ]]; then pass "$1"; else
    fail "$1"; printf "         expected to contain: %s\n" "$3"; fi
}

extract_fn() { awk -v fn="$1" '$0 ~ "^"fn"\\(\\) \\{"{g=1} g{print} g&&/^\}/{exit}' "$2"; }
D1_FUNCTIONS=(sandbox_plan sync_docker_proxy_app_token service_health wait_service_healthy
  remove_legacy_socket_proxies migrate_sandbox_to_broker gvisor_arch runsc_runtime_args runsc_runtime_path runsc_filters_syscalls
  gvisor_plan gvisor_sha512 install_gvisor harden_gvisor ensure_gvisor
  sandbox_isolation_label prepare_sandbox_plane)
for fn in "${D1_FUNCTIONS[@]}" derive_compose_profiles persist_env_var get_existing_val gen_secret; do
  body="$(extract_fn "$fn" "$INSTALLER")"
  if [[ -z "$body" ]]; then fail "install.sh defines $fn"; continue; fi
  eval "$body"
done

# The runtime arguments the installer registers runsc with, as install.sh sets them.
eval "$(grep '^GVISOR_RUNTIME_ARGS=' "$INSTALLER")"
GVISOR_SHA512=""
eq "install.sh registers runsc with its syscall filter" "${GVISOR_RUNTIME_ARGS[*]:-}" "--oci-seccomp"

# Output helpers the functions call; quiet unless a test captures them.
LOG="$TMP_ROOT/log"
info()    { echo "info: $*" >>"$LOG"; }
success() { echo "success: $*" >>"$LOG"; }
warn()    { echo "warn: $*" >>"$LOG"; }
BOLD="" RESET=""

echo "== IN-1 sandbox_plan =="
while IFS='|' read -r deploy mode expected; do
  eq "plan(${deploy:-<none>}, ${mode:-<blank>})" "$(sandbox_plan "$deploy" "$mode")" "$expected"
done <<'TABLE'
eval|__unset__|disabled
eval||disabled
eval|broker|broker
slim|__unset__|migrate
full|__unset__|migrate
|__unset__|migrate
slim|docker|migrate
slim|broker|broker
full|broker|broker
slim||disabled
slim|e2b|keep
full|local|keep
slim|docekr|keep
TABLE

echo "== IN-2 derive_compose_profiles pairs sandbox with SANDBOX_MODE =="
dp() { DATA_STORE=neo4j KV_STORE_TYPE=redis MESSAGE_BROKER=redis SANDBOX_MODE="$1" derive_compose_profiles; }
eq "broker starts the sandbox profile and the broker's" "$(dp broker)" "graph-neo4j,sandbox,sandbox-broker"
eq "docker (compatibility) without the harness starts what a release without the broker did" \
  "$(AGENT_HARNESS_WORKER_ENABLED=false dp docker)" "graph-neo4j,sandbox"
eq "docker migrating to the broker starts the broker's profile too" \
  "$(SANDBOX_PLAN=migrate dp docker)" "graph-neo4j,sandbox,sandbox-broker"
eq "docker with the agent harness starts the broker's profile too" \
  "$(AGENT_HARNESS_WORKER_ENABLED=true dp docker)" "graph-neo4j,sandbox,sandbox-broker"
eq "IN-1b compatibility keeps docker" "$(sandbox_plan slim __unset__ false false)" "docker"
eq "IN-1b the harness migrates" "$(sandbox_plan slim docker false true)" "migrate"
eq "blank (eval) has none" "$(dp '')" "graph-neo4j"
eq "e2b needs no broker or socket" "$(dp e2b)" "graph-neo4j"
eq "local needs none either" "$(dp local)" "graph-neo4j"

echo "== IN-3 gvisor_plan =="
RT_RUNC='{"io.containerd.runc.v2":{},"runc":{}}'
RT_RUNSC='{"runc":{},"runsc":{"path":"/usr/local/bin/runsc","runtimeArgs":["--oci-seccomp"],"status":{"x":"{\"a\":1}"}}}'
RT_RUNSC_BARE='{"runc":{},"runsc":{"path":"/usr/bin/runsc","status":{"org.opencontainers.runtime-spec.features":"{\"seccomp\":{}}"}}}'
ENGINE="Ubuntu 24.04 LTS"
SEC='["name=apparmor","name=seccomp,profile=builtin","name=cgroupns"]'
DESKTOP="Docker Desktop"
SEC_ROOTLESS='["name=seccomp,profile=builtin","name=rootless","name=cgroupns"]'
gp() { # choice sandbox os wsl arch runtimes daemon_os security docker_host privilege
  gvisor_plan "$@"
}
while IFS='|' read -r desc expected choice sandbox os wsl arch runtimes daemon sec host priv; do
  eq "$desc" "$(gp "$choice" "$sandbox" "$os" "$wsl" "$arch" "${!runtimes}" "${!daemon}" "${!sec}" "$host" "$priv")" "$expected"
done <<'TABLE'
Linux Engine x86_64 as root|install|auto|true|linux|false|x86_64|RT_RUNC|ENGINE|SEC||root
Linux Engine aarch64 with sudo|install|auto|true|linux|false|aarch64|RT_RUNC|ENGINE|SEC|unix:///var/run/docker.sock|sudo
runsc already registered|present|auto|true|linux|false|x86_64|RT_RUNSC|ENGINE|SEC||root
runsc without its syscall filter|harden|auto|true|linux|false|x86_64|RT_RUNSC_BARE|ENGINE|SEC||sudo
runsc without the filter and no root|present|auto|true|linux|false|x86_64|RT_RUNSC_BARE|ENGINE|SEC||none
operator skipped it|skip:disabled|skip|true|linux|false|x86_64|RT_RUNC|ENGINE|SEC||root
code execution off|skip:sandbox_off|auto|false|linux|false|x86_64|RT_RUNC|ENGINE|SEC||root
macOS|skip:not_linux|auto|true|darwin|false|arm64|RT_RUNC|ENGINE|SEC||root
Windows Git Bash|skip:not_linux|auto|true|windows|false|x86_64|RT_RUNC|ENGINE|SEC||root
WSL|skip:wsl|auto|true|linux|true|x86_64|RT_RUNC|ENGINE|SEC||root
Docker Desktop on Linux|skip:docker_desktop|auto|true|linux|false|x86_64|RT_RUNC|DESKTOP|SEC||root
rootless Docker|skip:rootless|auto|true|linux|false|x86_64|RT_RUNC|ENGINE|SEC_ROOTLESS||root
remote daemon|skip:remote_daemon|auto|true|linux|false|x86_64|RT_RUNC|ENGINE|SEC|tcp://10.0.0.5:2376|root
unsupported arch|skip:arch|auto|true|linux|false|armv7l|RT_RUNC|ENGINE|SEC||root
no root and no sudo|skip:no_root|auto|true|linux|false|x86_64|RT_RUNC|ENGINE|SEC||none
TABLE

echo "== IN-4 install_gvisor with stubbed commands =="
STUB="$TMP_ROOT/bin"; mkdir -p "$STUB"
CALLS="$TMP_ROOT/calls"; : >"$CALLS"
BIN_DIR="$TMP_ROOT/usr-local-bin"; mkdir -p "$BIN_DIR"
# curl: serves a tarball stand-in (a tampered one when BAD_SUM=1); the checksum is the installer's pin.
cat >"$STUB/curl" <<'EOF'
#!/usr/bin/env bash
out=""; url=""
while [[ $# -gt 0 ]]; do
  case "$1" in -o) out="$2"; shift ;; -*) ;; *) url="$1" ;; esac
  shift
done
echo "curl $url" >>"$CALLS"
if [[ "${BAD_SUM:-0}" == 1 ]]; then printf 'tampered %s\n' "$url" >"$out"; else printf 'tarball for %s\n' "$url" >"$out"; fi
EOF
# tar: unpacks the two binaries the installer asks for; runsc logs its calls.
cat >"$STUB/tar" <<'EOF'
#!/usr/bin/env bash
dir=""; while [[ $# -gt 0 ]]; do [[ "$1" == "-C" ]] && dir="$2"; shift; done
printf '#!/usr/bin/env bash\necho "runsc $*" >>"%s"\n[[ "$1" == install ]] && touch "%s"\n' "$CALLS" "$TMP_ROOT/registered" >"$dir/runsc"
printf '#!/usr/bin/env bash\n' >"$dir/containerd-shim-runsc-v1"
echo "tar -C $dir" >>"$CALLS"
EOF
cat >"$STUB/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "systemctl $*" >>"$CALLS"
EOF
# docker: lists runsc once `runsc install` has registered it.
cat >"$STUB/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $*" >>"$CALLS"
if [[ "$1" == info ]]; then
  case "$*" in
    *Runtimes*)
      if [[ -f "$TMP_ROOT/filtered" ]]; then echo '{"runc":{},"runsc":{"path":"/usr/bin/runsc","runtimeArgs":["--platform=systrap","--oci-seccomp"]}}'
      elif [[ -f "$TMP_ROOT/registered" ]]; then echo '{"runc":{},"runsc":{"path":"/usr/bin/runsc","runtimeArgs":["--platform=systrap"]}}'
      else echo '{"runc":{}}'; fi ;;
    *OperatingSystem*) echo "Ubuntu 24.04 LTS" ;;
    *SecurityOptions*) echo '["name=seccomp,profile=builtin"]' ;;
  esac
fi
EOF
chmod +x "$STUB"/*
export CALLS TMP_ROOT
(
  PATH="$STUB:$PATH"
  GVISOR_BASE_URL="https://gvisor.example/releases/release" GVISOR_RELEASE="20260928" GVISOR_BIN_DIR="$BIN_DIR"
  GVISOR_SHA512="$(printf 'tarball for %s\n' "https://gvisor.example/releases/release/20260928/x86_64/gvisor.tar.bz2" | sha512sum | cut -d' ' -f1)"
  if install_gvisor x86_64 ""; then pass "install_gvisor succeeds"; else fail "install_gvisor succeeds"; fi
  calls="$(cat "$CALLS")"
  contains "downloads the pinned x86_64 tarball" "$calls" "curl https://gvisor.example/releases/release/20260928/x86_64/gvisor.tar.bz2"
  [[ "$calls" != *".sha512"* ]] && pass "reads no checksum from the bucket (R41SUPPLY-FU-10)" || fail "reads no checksum from the bucket (R41SUPPLY-FU-10)"
  [[ -x "$BIN_DIR/runsc" && -x "$BIN_DIR/containerd-shim-runsc-v1" ]] && pass "runsc and the shim are installed executable" || fail "runsc and the shim are installed executable"
  contains "registers the runtime with its syscall filter" "$calls" "runsc install -- --oci-seccomp"
  contains "reloads, not restarts, the daemon" "$calls" "systemctl reload docker"
  [[ "$calls" != *"systemctl restart"* ]] && pass "never restarts Docker" || fail "never restarts Docker"
)
(
  PATH="$STUB:$PATH"; : >"$CALLS"; rm -f "$TMP_ROOT/registered"
  BIN_DIR2="$TMP_ROOT/bin-aarch64"; mkdir -p "$BIN_DIR2"
  GVISOR_BASE_URL="https://gvisor.example/r" GVISOR_RELEASE="20260928" GVISOR_BIN_DIR="$BIN_DIR2"
  GVISOR_SHA512="$(printf 'tarball for %s\n' "https://gvisor.example/r/20260928/aarch64/gvisor.tar.bz2" | sha512sum | cut -d' ' -f1)"
  install_gvisor arm64 "" >/dev/null
  contains "arm64 hosts fetch the aarch64 build" "$(cat "$CALLS")" "/20260928/aarch64/gvisor.tar.bz2"
)
(
  PATH="$STUB:$PATH"; : >"$CALLS"; rm -f "$TMP_ROOT/registered"
  BIN_DIR3="$TMP_ROOT/bin-bad"; mkdir -p "$BIN_DIR3"
  GVISOR_BASE_URL="https://gvisor.example/r" GVISOR_RELEASE="20260928" GVISOR_BIN_DIR="$BIN_DIR3" GVISOR_SHA512=""
  export BAD_SUM=1
  if install_gvisor x86_64 ""; then fail "a bad checksum fails"; else pass "a bad checksum fails"; fi
  [[ -z "$(ls -A "$BIN_DIR3")" ]] && pass "a bad checksum installs nothing" || fail "a bad checksum installs nothing"
  [[ "$(cat "$CALLS")" != *"runsc install"* ]] && pass "a bad checksum registers nothing" || fail "a bad checksum registers nothing"
  if install_gvisor sparc64 ""; then fail "an unknown arch is refused"; else pass "an unknown arch is refused"; fi
  unset BAD_SUM; : >"$CALLS"
  GVISOR_RELEASE="20990101"
  if install_gvisor x86_64 "" 2>/dev/null; then fail "a release with no pinned checksum is refused"; else pass "a release with no pinned checksum is refused"; fi
  [[ ! -s "$CALLS" ]] && pass "and nothing is downloaded for it" || fail "and nothing is downloaded for it"
)
eq "the pinned x86_64 checksum is a SHA-512" "$(gvisor_sha512 20260928 x86_64 | grep -cE '^[0-9a-f]{128}$')" "1"
eq "the pinned aarch64 checksum is a SHA-512" "$(gvisor_sha512 20260928 aarch64 | grep -cE '^[0-9a-f]{128}$')" "1"
eq "the default release has its checksums pinned" "$(grep -c "^GVISOR_RELEASE=\"\${PIPESHUB_GVISOR_RELEASE:-20260928}\"" "$INSTALLER")" "1"
(
  PATH="$STUB:$PATH"; : >"$CALLS"; touch "$TMP_ROOT/registered"
  IS_LINUX=true IS_MACOS=false IS_WINDOWS=false IS_WSL=false ARCH=x86_64 FLAG_YES=true FLAG_SKIP_GVISOR=false FLAG_INSTALL_GVISOR=false
  touch "$TMP_ROOT/filtered"
  ensure_gvisor true
  eq "ensure_gvisor sees runsc already registered" "$GVISOR_STATE" "present"
  [[ "$(cat "$CALLS")" != *curl* ]] && pass "and downloads nothing (idempotent)" || fail "and downloads nothing (idempotent)"
  rm -f "$TMP_ROOT/filtered"
  FLAG_SKIP_GVISOR=true; ensure_gvisor true
  eq "--skip-gvisor wins" "$GVISOR_STATE" "skip:disabled"
) || fail "the ensure_gvisor cases run to the end"
# A host-wide change (daemon.json, a Docker reload) needs an explicit yes: --yes
# accepts the installer's defaults, never this.
(
  printf '#!/usr/bin/env bash\necho "sudo $*" >>"$CALLS"\n[[ "$1" == -n ]] && exit 0\nexec "$@"\n' >"$STUB/sudo"
  chmod +x "$STUB/sudo"
  PATH="$STUB:$PATH"; : >"$CALLS"; rm -f "$TMP_ROOT/registered"
  IS_LINUX=true IS_MACOS=false IS_WINDOWS=false IS_WSL=false ARCH=x86_64 FLAG_YES=true FLAG_SKIP_GVISOR=false
  FLAG_INSTALL_GVISOR=false GVISOR_RELEASE="20260928"
  install_gvisor() { echo "install_gvisor $*" >>"$CALLS"; return 0; }
  unset PIPESHUB_GVISOR
  ensure_gvisor true
  eq "--yes alone leaves the Docker daemon alone" "$GVISOR_STATE" "skip:not_requested"
  [[ "$(cat "$CALLS")" != *install_gvisor* ]] && pass "and installs nothing" || fail "and installs nothing"
  PIPESHUB_GVISOR=install ensure_gvisor true
  eq "PIPESHUB_GVISOR=install opts in under --yes" "$GVISOR_STATE" "present"
  : >"$CALLS"; FLAG_INSTALL_GVISOR=true; ensure_gvisor true
  eq "--install-gvisor opts in under --yes" "$GVISOR_STATE" "present"
  contains "and installs it" "$(cat "$CALLS")" "install_gvisor x86_64 sudo"
  rm -f "$STUB/sudo"
) || fail "the gVisor opt-in cases run to the end"

echo "== IN-4b gVisor's syscall filter (--oci-seccomp) =="
filtered() { if runsc_filters_syscalls "$1"; then echo on; else echo off; fi; }
rt() { printf '{"runc":{},"runsc":{"path":"/usr/bin/runsc","runtimeArgs":[%s],"status":{"k":"{\\"runtimeArgs\\":[]}"}}}' "$1"; }
eq "a bare flag is on" "$(filtered "$(rt '"--oci-seccomp"')")" on
eq "a single dash, =true and =1 are on" "$(filtered "$(rt '"-oci-seccomp=true","--oci-seccomp=1"')")" on
eq "the last occurrence wins" "$(filtered "$(rt '"--oci-seccomp","--OCI-SECCOMP=false"')")" off
eq "other flags are not it" "$(filtered "$(rt '"--platform=systrap"')")" off
eq "no runtimeArgs is off" "$(filtered "$RT_RUNSC_BARE")" off
eq "no runsc is off" "$(filtered "$RT_RUNC")" off
eq "the daemon's runsc path is read" "$(runsc_runtime_path "$RT_RUNSC_BARE")" "/usr/bin/runsc"
eq "the summary says gVisor runs unfiltered" "$(sandbox_isolation_label broker present:unfiltered)" \
  'gVisor (runsc) via the sandbox broker, without its own syscall filter (see GVISOR.md, "Syscall filter")'
(
  printf '#!/usr/bin/env bash\necho "sudo $*" >>"$CALLS"\n[[ "$1" == -n ]] && exit 0\nexec "$@"\n' >"$STUB/sudo"
  printf '#!/usr/bin/env bash\necho "runsc $*" >>"$CALLS"\n[[ "$1" == install ]] && touch "%s"\n' "$TMP_ROOT/filtered" >"$STUB/runsc"
  chmod +x "$STUB/sudo" "$STUB/runsc"
  PATH="$STUB:$PATH"; : >"$CALLS"; touch "$TMP_ROOT/registered"; rm -f "$TMP_ROOT/filtered"
  IS_LINUX=true IS_MACOS=false IS_WINDOWS=false IS_WSL=false ARCH=x86_64 FLAG_YES=true FLAG_SKIP_GVISOR=false
  FLAG_INSTALL_GVISOR=false
  unset PIPESHUB_GVISOR
  ensure_gvisor true
  eq "--yes alone leaves an unfiltered runsc alone" "$GVISOR_STATE" "present:unfiltered"
  [[ "$(cat "$CALLS")" != *"runsc install"* ]] && pass "and registers nothing" || fail "and registers nothing"
  contains "and says how to turn the filter on" "$(cat "$LOG")" "pass --install-gvisor to register runsc with --oci-seccomp"
  : >"$CALLS"; FLAG_INSTALL_GVISOR=true
  # The daemon names its own runsc; the stub on PATH stands in for it.
  runsc_runtime_path() { echo "$STUB/runsc"; }
  ensure_gvisor true
  eq "--install-gvisor turns the filter on" "$GVISOR_STATE" "present"
  calls="$(cat "$CALLS")"
  contains "keeping the runtime's other arguments" "$calls" "runsc install --runtime=runsc -- --platform=systrap --oci-seccomp"
  contains "and reloads Docker" "$calls" "systemctl reload docker"
  [[ "$calls" != *"systemctl restart"* ]] && pass "never restarts Docker" || fail "never restarts Docker"
  [[ "$calls" != *curl* ]] && pass "and downloads nothing" || fail "and downloads nothing"
  rm -f "$STUB/sudo" "$STUB/runsc" "$TMP_ROOT/filtered"
) || fail "the syscall-filter cases run to the end"

echo "== IN-5 migrate_sandbox_to_broker =="
(
  ENV_FILE="$TMP_ROOT/env-migrate-ok"
  printf 'DEPLOY_TYPE=slim\nCOMPOSE_PROFILES=graph-neo4j,sandbox\nDOCKER_PROXY_TOKEN=the-apps\n' >"$ENV_FILE"
  COMPOSE_FILE="/c/docker-compose.yml"
  COMPOSE_FILES=(-f "$COMPOSE_FILE") PROJECT_NAME=pxh SANDBOX_BROKER_WAIT_SECS=6
  started=""
  compose_up() { started="$*"; }
  # Called in a subshell: count polls in a file.
  : >"$TMP_ROOT/polls"
  service_health() { echo x >>"$TMP_ROOT/polls"; (( $(wc -l <"$TMP_ROOT/polls") >= 2 )) && echo healthy || echo starting; }
  sleep() { :; }
  : >"$TMP_ROOT/removed"
  remove_legacy_socket_proxies() { echo called >>"$TMP_ROOT/removed"; }
  migrate_sandbox_to_broker
  eq "starts the socket proxy, the broker and the egress proxy first" "$started" \
    "docker-socket-proxy sandbox-broker egress-proxy"
  eq "removes the legacy layout's proxy once the broker serves" "$(cat "$TMP_ROOT/removed")" "called"
  eq "switches once healthy" "$SANDBOX_MODE" "broker"
  contains "persists SANDBOX_MODE=broker" "$(cat "$ENV_FILE")" "SANDBOX_MODE=broker"
  eq "no legacy overlay" "${COMPOSE_FILES[*]}" "-f /c/docker-compose.yml"
  # R51-COMPAT: on the broker the app holds no credential the Docker API proxy accepts.
  grep -qx 'DOCKER_PROXY_TOKEN=' "$ENV_FILE" && pass "clears the app's proxy token" || fail "clears the app's proxy token"
)
(
  ENV_FILE="$TMP_ROOT/env-migrate-down"
  printf 'DEPLOY_TYPE=slim\n' >"$ENV_FILE"
  COMPOSE_FILE="/c/docker-compose.yml"
  COMPOSE_FILES=(-f "$COMPOSE_FILE") PROJECT_NAME=pxh SANDBOX_BROKER_WAIT_SECS=6
  compose_up() { :; }
  service_health() { echo unhealthy; }
  sleep() { :; }
  : >"$LOG"
  : >"$TMP_ROOT/removed-down"
  remove_legacy_socket_proxies() { echo called >>"$TMP_ROOT/removed-down"; }
  migrate_sandbox_to_broker
  eq "keeps docker while the broker is down" "$SANDBOX_MODE" "docker"
  eq "and keeps the legacy proxy the app still needs" "$(cat "$TMP_ROOT/removed-down")" ""
  contains "persists SANDBOX_MODE=docker so the next run retries" "$(cat "$ENV_FILE")" "SANDBOX_MODE=docker"
  # R51-COMPAT: the base file carries docker mode; the app reaches the proxy with its own token.
  eq "the app keeps Docker through the base file's proxy" "${COMPOSE_FILES[*]}" "-f /c/docker-compose.yml"
  grep -qE '^DOCKER_PROXY_TOKEN=[a-f0-9]{64}$' "$ENV_FILE" && pass "with its proxy token" || fail "with its proxy token"
  contains "and says so" "$(cat "$LOG")" "warn: The sandbox broker did not become healthy"
)
(
  ENV_FILE="$TMP_ROOT/env-migrate-fail"
  : >"$ENV_FILE"
  COMPOSE_FILE="/c/a.yml" COMPOSE_FILES=(-f "$COMPOSE_FILE") PROJECT_NAME=pxh
  SANDBOX_BROKER_WAIT_SECS=6
  compose_up() { return 1; }
  service_health() { echo healthy; }
  migrate_sandbox_to_broker
  eq "a broker that cannot even be created keeps docker" "$SANDBOX_MODE" "docker"
)
(
  PATH="$STUB:$PATH"; ENV_FILE=/dev/null PROJECT_NAME=pxh COMPOSE_FILES=(-f /c/a.yml)
  docker() {
    case "$1 $2" in
      "compose -f") echo "abc123" ;;
      "inspect --format") echo healthy ;;
    esac
  }
  eq "service_health reads the container's health" "$(service_health sandbox-broker)" "healthy"
  docker() { :; }
  eq "service_health without a container" "$(service_health sandbox-broker)" "missing"
)

echo "== IN-5b remove_legacy_socket_proxies (SOCKPROXY-1) =="
(
  PROJECT_NAME=pxh LOG="$TMP_ROOT/log-legacy"; : >"$LOG"
  : >"$TMP_ROOT/docker-calls"
  docker() {
    echo "$*" >>"$TMP_ROOT/docker-calls"
    case "$1" in
      ps) printf 'legacyproxy\nappcontainer\n' ;;
      inspect)
        case "${*: -1}" in
          legacyproxy) echo "/var/run/docker.sock " ;;
          appcontainer) echo "/data/pipeshub /root/.local " ;;
        esac ;;
    esac
  }
  remove_legacy_socket_proxies
  calls="$(cat "$TMP_ROOT/docker-calls")"
  contains "lists only this project's containers" "$calls" "--filter label=com.docker.compose.project=pxh"
  contains "on the legacy overlay's app network" "$calls" "--filter network=pxh_sandbox-control"
  contains "removes the one holding the socket" "$calls" "rm -f legacyproxy"
  [[ "$calls" != *"rm -f appcontainer"* ]] && pass "keeps a container without the socket (the app)" \
    || fail "keeps a container without the socket (the app)"
  contains "and says so" "$(cat "$LOG")" "Removed a legacy Docker API proxy container"
  docker() { :; }
  : >"$LOG"
  remove_legacy_socket_proxies
  eq "nothing to remove is quiet" "$(cat "$LOG")" ""
  # R51-COMPAT: this run's proxy is on sandbox-control too (docker mode lives in the base file); it is the one on
  # sandbox_docker, and stays.
  : >"$TMP_ROOT/docker-calls"
  docker() {
    echo "$*" >>"$TMP_ROOT/docker-calls"
    case "$1" in
      ps) echo currentproxy ;;
      inspect) case "$3" in *Mounts*) echo "/var/run/docker.sock " ;; *Networks*) echo "pxh_sandbox-control pxh_sandbox_docker " ;; esac ;;
    esac
  }
  remove_legacy_socket_proxies
  [[ "$(cat "$TMP_ROOT/docker-calls")" != *"rm -f currentproxy"* ]] && pass "keeps the broker's front" \
    || fail "keeps the broker's front"
)

echo "== IN-6 the wizard writes SANDBOX_MODE =="
inner="$(cat "$INSTALLER")"
contains ".env template carries SANDBOX_MODE" "$inner" 'SANDBOX_MODE=${SANDBOX_MODE}'
contains "eval writes it blank" "$inner" 'if [[ "$DEPLOY_TYPE" == "eval" ]]; then
    SANDBOX_MODE=""'
contains "slim/full default to the broker when hardened or with the harness" "$inner" 'if [[ "$PIPESHUB_HARDENED" == true || "$AGENT_HARNESS_WORKER_ENABLED" == true ]]; then SANDBOX_MODE=broker'
contains "and to docker under the compatibility profile" "$inner" '         else SANDBOX_MODE=docker; fi ;;'
contains "an absent key reads as __unset__" "$inner" 'sandbox_plan "${DEPLOY_TYPE:-}" "${SANDBOX_MODE-__unset__}" "${PIPESHUB_HARDENED:-false}"'
contains "--skip-gvisor is parsed" "$inner" "--skip-gvisor)       FLAG_SKIP_GVISOR=true ;;"
contains "the sandbox plane is prepared before the prebuilt start" "$inner" '  prepare_sandbox_plane
  info "Starting containers..."'
eq "isolation label on runc says reduced" "$(sandbox_isolation_label broker skip:docker_desktop)" "hardened Docker (runc) via the sandbox broker: reduced isolation, shared host kernel"
eq "isolation label with gVisor" "$(sandbox_isolation_label migrate present)" "gVisor (runsc) via the sandbox broker"
eq "isolation label for eval" "$(sandbox_isolation_label disabled '')" "off (run_code unavailable)"

echo "== IN-8 the real installer on an existing .env (--upgrade --print-env-only) =="
if docker info >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  work="$TMP_ROOT/upgrade"; mkdir -p "$work"
  cp "$INSTALLER" "$COMPOSE_DIR/docker-compose.yml" "$work/"
  seed() { # deploy_type, then extra .env lines
    printf 'DEPLOY_TYPE=%s\nIMAGE_TAG=0.9.0\nSECRET_KEY=keep\nAPP_PORT=3000\nDATA_STORE=neo4j\nKV_STORE_TYPE=redis\nMESSAGE_BROKER=redis\nCOMPOSE_PROFILES=graph-neo4j,sandbox\n' "$1" >"$work/.env"
    shift
    for line in "$@"; do printf '%s\n' "$line" >>"$work/.env"; done
  }
  upgrade() { (cd "$work" && PIPESHUB_PROJECT=pxh-d1-installer bash ./install.sh --upgrade --print-env-only 2>&1); }

  # R51-COMPAT: an .env from before the broker keeps running code as it did, unless the operator opts in.
  seed slim
  out="$(upgrade)"
  contains "an .env from before the broker keeps docker (compatibility)" "$out" "SANDBOX_MODE=docker (the app"
  contains "written down" "$(cat "$work/.env")" "SANDBOX_MODE=docker"
  grep -qx 'COMPOSE_PROFILES=graph-neo4j,sandbox' "$work/.env" && pass "and starts no broker" || fail "and starts no broker"

  seed slim
  out="$(cd "$work" && PIPESHUB_HARDENED=true PIPESHUB_PROJECT=pxh-d1-installer bash ./install.sh --upgrade --print-env-only 2>&1)"
  contains "opted in, an .env from before the broker is moved to it" "$out" "moving to the sandbox broker"
  contains "it gets the broker's profile" "$(cat "$work/.env")" "COMPOSE_PROFILES=graph-neo4j,sandbox,sandbox-broker"
  [[ "$(cat "$work/.env")" != *SANDBOX_MODE=* ]] && pass "SANDBOX_MODE is not written before the broker is healthy" \
    || fail "SANDBOX_MODE is not written before the broker is healthy"

  seed eval
  out="$(upgrade)"
  contains "eval turns run_code off" "$out" "off (run_code unavailable)"
  grep -qx 'SANDBOX_MODE=' "$work/.env" && pass "eval persists SANDBOX_MODE= (blank)" || fail "eval persists SANDBOX_MODE= (blank)"
  contains "eval drops the sandbox profile" "$(cat "$work/.env")" $'COMPOSE_PROFILES=graph-neo4j\n'

  seed slim "SANDBOX_MODE=e2b"
  out="$(upgrade)"
  contains "an explicit e2b is left alone" "$(cat "$work/.env")" "SANDBOX_MODE=e2b"
  contains "and starts no broker" "$(cat "$work/.env")" $'COMPOSE_PROFILES=graph-neo4j\n'

  seed slim "SANDBOX_MODE=broker"
  out="$(upgrade)"
  contains "a broker install reports the broker" "$out" "sandbox broker"
  contains "and keeps its profile" "$(cat "$work/.env")" "COMPOSE_PROFILES=graph-neo4j,sandbox"
else
  printf "  skip - needs a reachable Docker daemon and Compose v2 (the installer's pre-flight)\n"
fi

echo "== IN-7 static =="
if bash -n "$INSTALLER"; then pass "install.sh parses"; else fail "install.sh parses"; fi
if command -v shellcheck >/dev/null 2>&1; then
  {
    echo '#!/usr/bin/env bash'
    # Installer globals (IS_WSL, ARCH, colours) are set outside these functions.
    echo '# shellcheck disable=SC2034,SC2153,SC2154'
    for fn in "${D1_FUNCTIONS[@]}"; do extract_fn "$fn" "$INSTALLER"; done
  } >"$TMP_ROOT/d1-functions.sh"
  if out="$(shellcheck -s bash "$TMP_ROOT/d1-functions.sh" 2>&1)"; then
    pass "shellcheck: the D1 functions are clean"
  else
    fail "shellcheck: the D1 functions are clean"; printf '%s\n' "$out" | head -40
  fi
else
  printf "  skip - shellcheck not installed\n"
fi

passed="$(wc -l <"$PASS_FILE" | tr -d ' ')"; failed="$(wc -l <"$FAIL_FILE" | tr -d ' ')"
printf "\nResults: %s passed, %s failed\n" "$passed" "$failed"
[[ "$failed" == "0" ]]

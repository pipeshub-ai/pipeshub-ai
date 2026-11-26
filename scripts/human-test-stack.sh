#!/usr/bin/env bash
# The human-test stack: a PipesHub stack a person tests by hand in the browser, from a commit, on the machine this
# script runs on.
#
#   scripts/human-test-stack.sh up <sha> [options]       check the commit out, bring the stack up, seed it, print how
#                                                        to reach it
#   scripts/human-test-stack.sh refresh <sha> [options]  move the stack to a newer commit, keeping its data and accounts
#                                                        (a seeded model or MCP server the config store lost fails it;
#                                                        E2E_SEED_HEAL=1 makes them again, keeping the org's default model)
#   scripts/human-test-stack.sh status          what runs, where, at which commit, and its memory and CPU now
#   scripts/human-test-stack.sh smoke           the smoke check (deployment/e2e/smoke_human.py): no live model call
#   scripts/human-test-stack.sh down            remove the stack, its data and the credentials file
#   scripts/human-test-stack.sh rotate-secret-key [STEP...]
#                                               the SECRET_KEY rotation (guides/runbooks.md), with the runbook's own
#                                               commands in the app container: dry-run, rotate, verify, retire by
#                                               default; introduce, switch and drop-previous change the stack's keys
#
# It is a profile of the e2e runner, not a second stack builder: the commit's own deployment/e2e/run.sh brings the
# stack up (`human-up`) and rebuilds it (`rebuild`); docker-compose.human.yml says how it differs from the e2e lanes.
# The commit is checked out in its own worktree (HT1_HOME/src), so the stack never runs from a tree someone edits.
# Compose project pxh-human-ht1; everything binds to 127.0.0.1. Passwords go only to HT1_HOME/credentials.txt (600).
#
# Options of up and refresh, for people's own Claude accounts in Claude Code (SIGNIN; off unless asked for):
#   --engine-signin / --no-engine-signin   the sign-in add-on on or off: the egress proxy's CONNECT tunnels to the
#                                          release's signed Anthropic hosts (docker-compose.egress-tunnels.yml), set on
#                                          the proxy and the harness worker
#   --signin-idle-minutes N|default        how long a signed-in sandbox waits idle (5..120; default 30)
#   --signin-max-hours N|default           how long a sign-in lasts at most (1..24; default 12)
# Option of up and refresh for the stack's public name, when a proxy in front of the tunnel serves it:
#   --public-url URL|default               https://HOST[:PORT], no path (a trailing / is dropped): the stack's
#                                          FRONTEND_PUBLIC_URL (links in emails and the UI) and one more of its
#                                          ALLOWED_ORIGINS; default goes back to the tunnel's http://127.0.0.1:PORT
# Option of up and refresh for the engines' sandbox runtime (R32-TRUST):
#   --runtime runsc|auto|runc|default      runsc: the production floor, engines on gVisor and the ACP profiles verified
#                                          against the stack's own test root, all eight engines (the stack's default where
#                                          the Docker daemon has runsc); auto: the dev override and unsigned profiles,
#                                          as before R32-TRUST; runc: hardened runc with runsc withheld; default goes
#                                          back to the stack's default
# A refresh without one of them keeps what the stack has; a new stack starts with the add-on off and no public URL.
# The runtime is the exception: unless --runtime chose one, the stack runs at its default, the production floor where the
# host offers it, so people test what production installs run.
set -euo pipefail

HT1_HOME="${HT1_HOME:-$HOME/.cache/pxh-ht1}"
SRC="$HT1_HOME/src"
RUN=ht1
export E2E_STATE_DIR="$HT1_HOME/state"
export E2E_RUN="$RUN" E2E_PROJECT_PREFIX=pxh-human- E2E_STACK=ST-1
export E2E_SANDBOX_IMAGE="${E2E_SANDBOX_IMAGE:-pxh-sandbox:pr1}"
export E2E_HUMAN_DIR="$HT1_HOME" E2E_ADMIN_EMAIL_WANT=admin@acme-demo.example E2E_UI_CACHE=1
# A person reads the real github.com: never the e2e lanes' stand-in git host (docker-compose.fakegit.yml).
export E2E_FAKE_GIT=0
export E2E_FAKE_SLACK=0
# Nor the journeys' scripted MCP and OAuth servers (docker-compose.mcp.yml): its people connect their own.
export E2E_MCP_HUB=0
WORK="$E2E_STATE_DIR/$RUN"
CREDENTIALS="$HT1_HOME/credentials.txt"
PROJECT=pxh-human-ht1
# Only this script's options set these: a value left in the caller's shell never turns the add-on on.
unset E2E_ENGINE_SIGNIN E2E_SIGNIN_IDLE_MINUTES E2E_SIGNIN_MAX_HOURS E2E_PUBLIC_URL
# R32-TRUST: so is the runtime, which only --runtime (or the stack's default) chooses.
unset E2E_SANDBOX_RUNTIME E2E_ACP_PROFILES E2E_ENGINES_WITHOUT_GVISOR
# The runtime a person chose with --runtime, kept for later refreshes; without it, the stack's default (default_runtime).
RUNTIME_CHOICE="$HT1_HOME/runtime"

say() { printf 'human-test-stack: %s\n' "$*"; }
die() { printf 'human-test-stack: %s\n' "$*" >&2; exit 1; }
envv() { grep -E "^$1=" "$WORK/stack.env" | head -1 | cut -d= -f2-; }

# The checkout this script belongs to, and the repository's main worktree (node_modules and the Python venv).
SELF_REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
MAIN_REPO="$(git -C "$SELF_REPO" worktree list --porcelain | awk 'NR==1 {print $2}')"
# The caller's choice, else the one the stack was started with: run.sh's rebuild takes an exported value over the
# stack's env file, so a refresh that fell back to the main worktree's venv mounted older packages than the commit pins.
site_packages() {
  local kept=""
  [[ ! -f "$WORK/stack.env" ]] || kept="$(envv E2E_SITE_PACKAGES || true)"
  printf '%s\n' "${E2E_SITE_PACKAGES:-${kept:-$(readlink -f "$MAIN_REPO/backend/python/venv/lib/python3.12/site-packages")}}"
}
E2E_SITE_PACKAGES="$(site_packages)"
export E2E_SITE_PACKAGES

# Packages installed from an older lockfile built the stack without @aws-sdk and failed in tsc; say so first.
# npm ci records what it installed in node_modules/.package-lock.json; an optional package may be absent there.
packages_current() {
  local dir="$1" problem
  [[ -f "$dir/package-lock.json" ]] || return 0
  problem="$(node - "$dir/package-lock.json" "$dir/node_modules/.package-lock.json" <<'JS'
  const fs = require("fs");
  const want = JSON.parse(fs.readFileSync(process.argv[2], "utf8")).packages || {};
  let have;
  try { have = JSON.parse(fs.readFileSync(process.argv[3], "utf8")).packages || {}; }
  catch { console.log("no install record"); process.exit(0); }
  const bad = [];
  for (const [p, w] of Object.entries(want)) {
    if (!p.startsWith("node_modules/") || w.optional || w.link) continue;
    const h = have[p];
    if (!h) bad.push(`${p.slice(13)} missing`);
    else if (h.version !== w.version) bad.push(`${p.slice(13)} ${h.version}, wants ${w.version}`);
  }
  if (bad.length) console.log(`${bad.length} packages differ (${bad.slice(0, 3).join("; ")})`);
JS
)" || die "could not compare $dir's packages with its lockfile"
  [[ -z "$problem" ]] || die "$(readlink -f "$dir/node_modules" || echo "$dir/node_modules") is not installed from this commit's lockfile: $problem; run npm ci in $(dirname "$(readlink -f "$dir/node_modules")")"
}

checkout() {
  local sha="$1"
  git -C "$MAIN_REPO" rev-parse --verify -q "$sha^{commit}" >/dev/null || die "no commit $sha in $MAIN_REPO"
  if [[ -e "$SRC/.git" ]]; then
    [[ -z "$(git -C "$SRC" status --porcelain --untracked-files=no)" ]] || die "$SRC has local changes"
    git -C "$SRC" checkout -q --detach "$sha"
  else
    mkdir -p "$HT1_HOME"
    git -C "$MAIN_REPO" worktree add -q --detach "$SRC" "$sha"
  fi
  grep -q '^  human-up)' "$SRC/deployment/e2e/run.sh" \
    || die "commit $sha predates the human profile of deployment/e2e/run.sh"
  # The build uses the main checkout's packages; a commit that changed them needs them installed there first.
  local dir
  # A link is always repointed: one left at a removed worktree's packages fails `-e` yet still blocks `ln -s`.
  for dir in backend/nodejs/apps frontend; do
    if [[ -L "$SRC/$dir/node_modules" || ! -e "$SRC/$dir/node_modules" ]]; then
      ln -sfn "$MAIN_REPO/$dir/node_modules" "$SRC/$dir/node_modules"
    fi
    packages_current "$SRC/$dir"
  done
  git -C "$SRC" rev-parse HEAD >"$HT1_HOME/sha"
}

keep_option() {
  local name="$1" min="$2" max="$3" value="$4"
  if [[ "$value" == default ]]; then
    export "$name="
    return 0
  fi
  if ! [[ "$value" =~ ^[0-9]{1,3}$ ]] || ((10#$value < min || 10#$value > max)); then
    die "${name#E2E_}: '$value' is not a whole number from $min to $max (or default)"
  fi
  export "$name=$((10#$value))"
}

# An origin, as browsers send it: scheme and host lower case, no path, query, fragment or user. Only https: the name is
# reached from outside this host, and a session cookie over plain http would cross the network in the clear.
public_url_option() {
  local value="$1"
  if [[ "$value" == default ]]; then
    export E2E_PUBLIC_URL=
    return 0
  fi
  value="${value%/}"
  value="${value,,}"
  [[ "$value" =~ ^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]{1,5})?$ ]] \
    || die "public URL: '$1' is not https://HOST[:PORT] without a path (or default)"
  export E2E_PUBLIC_URL="$value"
}

parse_options() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --engine-signin) export E2E_ENGINE_SIGNIN=1 ;;
      --no-engine-signin) export E2E_ENGINE_SIGNIN=0 ;;
      --signin-idle-minutes) keep_option E2E_SIGNIN_IDLE_MINUTES 5 120 "${2:-}"; shift ;;
      --signin-max-hours) keep_option E2E_SIGNIN_MAX_HOURS 1 24 "${2:-}"; shift ;;
      --public-url) public_url_option "${2:-}"; shift ;;
      --runtime) runtime_option "${2:-}"; shift ;;
      *) die "unknown option '$1' (--engine-signin, --no-engine-signin, --signin-idle-minutes N, --signin-max-hours N, --public-url URL, --runtime runsc|auto|runc|default)" ;;
    esac
    shift
  done
}

# Whether the commit's run.sh has a line matching the pattern. It reads the whole file first: under pipefail,
# `git show | grep -q` fails whenever grep stops at an early match while git is still writing (run.sh passed 64 KB).
commit_run_sh_has() {
  local body
  body=$(git -C "$MAIN_REPO" show "$1:deployment/e2e/run.sh" 2>/dev/null) || return 1
  grep -q -- "$2" <<<"$body"
}

# The commit's own run.sh reads the options from the stack's env file; one from before them would drop them silently.
require_signin_support() {
  local sha="$1"
  [[ -n "${E2E_ENGINE_SIGNIN+set}${E2E_SIGNIN_IDLE_MINUTES+set}${E2E_SIGNIN_MAX_HOURS+set}" ]] || return 0
  commit_run_sh_has "$sha" '^TUNNELS_OVERLAY=' \
    || die "commit $sha predates the sign-in options of deployment/e2e/run.sh (harness/signkit)"
}

# The same for the public URL (run.sh's write_public_url).
require_public_url_support() {
  local sha="$1"
  [[ -n "${E2E_PUBLIC_URL+set}" ]] || return 0
  commit_run_sh_has "$sha" '^write_public_url() {' \
    || die "commit $sha predates the --public-url option of deployment/e2e/run.sh (R25-UI)"
}

# R32-TRUST: the stack's default runtime, the production floor (runsc) where the daemon offers it, else auto.
default_runtime() {
  if docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'; then echo runsc; else echo auto; fi
}

runtime_option() {
  case "$1" in
    runsc | auto | runc) echo "$1" >"$RUNTIME_CHOICE" ;;
    default) rm -f "$RUNTIME_CHOICE" ;;
    *) die "runtime: '$1' is not runsc, auto, runc or default" ;;
  esac
}

# The runtime the stack runs on, for run.sh: the person's choice, else the stack's default. A commit whose run.sh predates
# R32-TRUST would run the production floor without signed ACP profiles (five engines lost): refused when asked for,
# and the default then leaves the stack's runtime as it is.
choose_runtime() {
  local sha="$1" runtime chosen=0
  mkdir -p "$HT1_HOME"
  if [[ -f "$RUNTIME_CHOICE" ]]; then runtime="$(cat "$RUNTIME_CHOICE")"; chosen=1; else runtime="$(default_runtime)"; fi
  if ! commit_run_sh_has "$sha" '^acp_profiles_env() {'; then
    ((chosen == 0)) || die "commit $sha predates --runtime (deployment/e2e/run.sh, R32-TRUST)"
    return 0
  fi
  export E2E_SANDBOX_RUNTIME="$runtime"
}

port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

pick_port() {
  local port=""
  [[ -f "$HT1_HOME/port" ]] && port="$(cat "$HT1_HOME/port")"
  if [[ -z "$port" ]] || ! port_free "$port"; then
    port="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  fi
  echo "$port" >"$HT1_HOME/port"
  echo "$port"
}

write_credentials() {
  local member="$WORK/member.json" url public
  url="http://127.0.0.1:$(envv E2E_APP_PORT)"
  (
    umask 077
    {
      echo "PipesHub human test stack ($PROJECT), commit $(cat "$HT1_HOME/sha")"
      echo "URL: $url  (through the SSH tunnel)"
      public="$(envv FRONTEND_PUBLIC_URL || true)"
      [[ -z "$public" || "$public" == "$url" ]] || echo "Public URL: $public"
      echo
      echo "Org admin:     $(envv E2E_ADMIN_EMAIL)"
      echo "  password:    $(envv E2E_ADMIN_PASSWORD)"
      python3 -c 'import json, sys; m = json.load(open(sys.argv[1])); print("Member:        " + m["email"]); print("  password:    " + m["password"])' "$member"
    } >"$CREDENTIALS"
  )
  chmod 600 "$CREDENTIALS"
}

tunnel_line() {
  local port; port="$(envv E2E_APP_PORT)"
  say "from your laptop:  ssh -N -L $port:127.0.0.1:$port $(id -un)@<this VM's address>"
  say "then open          http://127.0.0.1:$port   (accounts: $CREDENTIALS on this host)"
  local public; public="$(envv FRONTEND_PUBLIC_URL || true)"
  [[ -z "$public" || "$public" == "http://127.0.0.1:$port" ]] || say "public URL         $public"
}

cmd_up() {
  local sha="${1:?usage: up <sha> [options]}"
  shift
  parse_options "$@"
  [[ ! -f "$WORK/stack.env" ]] || die "a stack is up ($PROJECT); use refresh <sha> or down"
  require_signin_support "$sha"
  require_public_url_support "$sha"
  mkdir -p "$HT1_HOME/hf" "$E2E_STATE_DIR"
  choose_runtime "$sha"
  checkout "$sha"
  E2E_APP_PORT_WANT="$(pick_port)" bash "$SRC/deployment/e2e/run.sh" human-up
  write_credentials
  say "up at commit $(cat "$HT1_HOME/sha")"
  tunnel_line
}

cmd_refresh() {
  local sha="${1:?usage: refresh <sha> [options]}"
  shift
  parse_options "$@"
  [[ -f "$WORK/stack.env" ]] || die "no stack is up; use up <sha>"
  require_signin_support "$sha"
  require_public_url_support "$sha"
  choose_runtime "$sha"
  checkout "$sha"
  bash "$SRC/deployment/e2e/run.sh" rebuild "$RUN"
  write_credentials
  say "refreshed to commit $(cat "$HT1_HOME/sha")"
  tunnel_line
}

cmd_status() {
  [[ -f "$WORK/stack.env" ]] || { say "no stack is up"; return 0; }
  say "commit $(cat "$HT1_HOME/sha"), project $PROJECT, port $(envv E2E_APP_PORT)"
  say "sign-in add-on: $([[ "$(envv E2E_ENGINE_SIGNIN)" == 1 ]] && echo on || echo off)" \
    "(idle minutes: $(envv E2E_SIGNIN_IDLE_MINUTES | grep . || echo default)," \
    "max hours: $(envv E2E_SIGNIN_MAX_HOURS | grep . || echo default))"
  say "engine runtime: $(envv E2E_SANDBOX_RUNTIME | grep . || echo auto), ACP profiles: $(envv E2E_ACP_PROFILES | grep . || echo unsigned)"
  docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '  {{.Names}}\t{{.Status}}'
  local org ids
  org="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("orgId",""))' "$WORK/seed.json")"
  ids="$( { docker ps -q --filter "label=com.docker.compose.project=$PROJECT";
            docker ps -q --filter label=io.pipeshub.sandbox-broker.managed=1 --filter "label=orgId=$org"; } )"
  # shellcheck disable=SC2086
  [[ -n "$ids" ]] && docker stats --no-stream --format '  {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' $ids
  tunnel_line
}

cmd_smoke() {
  [[ -f "$WORK/stack.env" ]] || die "no stack is up"
  python3 "$SRC/deployment/e2e/smoke_human.py" "$WORK"
}

cmd_rotate_secret_key() {
  [[ -f "$WORK/stack.env" ]] || die "no stack is up"
  bash "$SRC/deployment/e2e/run.sh" rotate-secret-key "$RUN" "$@"
}

cmd_down() {
  [[ -d "$SRC" ]] || die "no checkout at $SRC"
  bash "$SRC/deployment/e2e/run.sh" down "$RUN"
  rm -f "$CREDENTIALS" "$HT1_HOME/sha"
  say "removed $PROJECT and its data; kept the checkout ($SRC) and the embedding model cache ($HT1_HOME/hf)"
}

case "${1:-}" in
  up) shift; cmd_up "$@" ;;
  refresh) shift; cmd_refresh "$@" ;;
  status) cmd_status ;;
  smoke) cmd_smoke ;;
  down) cmd_down ;;
  rotate-secret-key) shift; cmd_rotate_secret_key "$@" ;;
  *) die "usage: $0 up <sha> [options] | refresh <sha> [options] | status | smoke | rotate-secret-key [STEP...] | down" ;;
esac

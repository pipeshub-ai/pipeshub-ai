#!/usr/bin/env bash
# ==============================================================================
# PipesHub AI — one-command installer entrypoint
# ==============================================================================
# Two supported ways to run it:
#
#   1. From a cloned repository (developers / contributors):
#        ./install.sh [OPTIONS]
#
#   2. Standalone, without cloning (fastest for trying PipesHub):
#        curl -fsSL https://get.pipeshub.com/install | bash
#
# This wrapper deliberately contains no install logic of its own. The real
# installer lives at deployment/docker-compose/install.sh. This file only
# decides WHERE the deployment files come from:
#   - repo mode:        delegate to the in-tree installer.
#   - standalone mode:  download docker-compose.yml + the installer for a pinned
#                       release into ./pipeshub (override with PIPESHUB_DIR) and
#                       run them from there.
#
# Standalone mode installs prebuilt images only. Building from source (--build)
# requires a full git clone, so use option 1 for that.
#
# Optional environment overrides (standalone mode):
#   PIPESHUB_REF   branch, tag, or commit SHA to install (default: latest release, else main)
#   PIPESHUB_DIR   directory to download deployment files into (default: ./pipeshub)
# ==============================================================================
set -euo pipefail

REPO="pipeshub-ai/pipeshub-ai"
RAW_BASE="https://raw.githubusercontent.com"
API_BASE="https://api.github.com"
INNER_SUBPATH="deployment/docker-compose"

if [ -t 1 ]; then C_CYAN=$'\033[36m'; C_RED=$'\033[31m'; C_RESET=$'\033[0m'; else C_CYAN=""; C_RED=""; C_RESET=""; fi
note() { printf "%s>%s %s\n" "$C_CYAN" "$C_RESET" "$*"; }
err()  { printf "%sError:%s %s\n" "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# When invoked as `curl ... | bash`, BASH_SOURCE[0] is the shell name (or empty)
# and points to no real file, so we cannot resolve a script directory — that is
# the signal we are running standalone rather than from a checkout.
SELF_SRC="${BASH_SOURCE[0]:-}"
SELF_DIR=""
if [[ -n "$SELF_SRC" && "$SELF_SRC" != "bash" && "$SELF_SRC" != "sh" && -f "$SELF_SRC" ]]; then
  SELF_DIR="$(cd "$(dirname "$SELF_SRC")" && pwd)"
fi

# ── Repo mode ─────────────────────────────────────────────────────────────────
# This file is at the root of a checkout and the real installer is present.
if [[ -n "$SELF_DIR" && -f "$SELF_DIR/$INNER_SUBPATH/install.sh" ]]; then
  exec bash "$SELF_DIR/$INNER_SUBPATH/install.sh" "$@"
fi

# ── Standalone mode ───────────────────────────────────────────────────────────
note "PipesHub standalone installer (no local repository detected)."

if command -v curl >/dev/null 2>&1; then
  dl()    { curl -fsSL "$1" -o "$2"; }
  fetch() { curl -fsSL "$1"; }
elif command -v wget >/dev/null 2>&1; then
  dl()    { wget -qO "$2" "$1"; }
  fetch() { wget -qO- "$1"; }
else
  err "Need either curl or wget to download PipesHub. Install one and re-run."
fi

# Resolve the ref to install: explicit override wins; otherwise the latest
# published release tag; otherwise main.
REF="${PIPESHUB_REF:-}"
if [[ -z "$REF" ]]; then
  REF="$(fetch "$API_BASE/repos/$REPO/releases/latest" 2>/dev/null \
          | grep -m1 '"tag_name"' | cut -d'"' -f4 || true)"
fi
[[ -z "$REF" ]] && REF="main"
note "Installing from ref: $REF"

DEST="${PIPESHUB_DIR:-$PWD/pipeshub}"
mkdir -p "$DEST" || err "Cannot create install directory: $DEST"
note "Downloading deployment files into: $DEST"

base_url="$RAW_BASE/$REPO/$REF/$INNER_SUBPATH"
dl "$base_url/docker-compose.yml" "$DEST/docker-compose.yml" \
  || err "Failed to download docker-compose.yml (ref: $REF). Set PIPESHUB_REF to a valid tag/branch and retry."
dl "$base_url/install.sh" "$DEST/install.sh" \
  || err "Failed to download install.sh (ref: $REF). Set PIPESHUB_REF to a valid tag/branch and retry."
chmod +x "$DEST/install.sh"

note "Launching installer..."
# Reconnect stdin to the terminal so the installer's interactive prompts work
# even though our own stdin is the piped script body (curl | bash). Probe that
# /dev/tty is actually openable (it isn't in CI or a detached pipe, where the
# device node exists but has no controlling terminal); otherwise pass stdin
# through unchanged so callers can still drive it with --yes / env vars.
if (exec </dev/tty) 2>/dev/null; then
  exec bash "$DEST/install.sh" "$@" </dev/tty
else
  exec bash "$DEST/install.sh" "$@"
fi

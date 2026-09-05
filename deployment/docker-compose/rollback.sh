#!/usr/bin/env bash
# ==============================================================================
# Roll a PipesHub deployment back to a previous image tag.
# ==============================================================================
# Shipping daily is safe when a bad release is cheap to undo, not when every
# release is perfect. This is the undo, and it is deliberately boring: it moves
# IMAGE_TAG in .env and re-runs the installer's upgrade path, which is the same
# code path a forward upgrade uses and is therefore already exercised.
#
#   ./rollback.sh                 # back to the previously recorded tag
#   ./rollback.sh 0.6.0           # back to a specific version
#   ./rollback.sh --list          # what this deployment has run
#   ./rollback.sh --dry-run 0.6.0 # print what would happen
#
# What it does NOT do: restore data. A rollback undoes code, not migrations. If
# the release you are undoing migrated data irreversibly, restore from backup
# instead — this script tells you when that risk applies rather than pretending
# it does not exist.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${PIPESHUB_ENV_FILE:-$SCRIPT_DIR/.env}"
HISTORY_FILE="${PIPESHUB_HISTORY_FILE:-$SCRIPT_DIR/.pipeshub-image-history}"
INSTALLER="$SCRIPT_DIR/install.sh"
LOG_PREFIX="rollback"

log()  { echo "${LOG_PREFIX}: $*"; }
die()  { echo "${LOG_PREFIX}: $*" >&2; exit 1; }

DRY_RUN=false
TARGET=""
for arg in "$@"; do
  case "$arg" in
    --list)     LIST_ONLY=true ;;
    --dry-run)  DRY_RUN=true ;;
    -h|--help)  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)         die "unknown option: $arg" ;;
    *)          TARGET="$arg" ;;
  esac
done

[[ -f "$ENV_FILE" ]]  || die "no .env at $ENV_FILE — is this a PipesHub deployment directory?"
[[ -f "$INSTALLER" ]] || die "no install.sh at $INSTALLER"

env_val() { grep -E "^${1}=" "$ENV_FILE" | tail -1 | cut -d= -f2- | tr -d '\r"'; }

CURRENT="$(env_val IMAGE_TAG)"
[[ -n "$CURRENT" ]] || die "IMAGE_TAG is not set in $ENV_FILE"

# The history file is append-only and written by record_current below, so a
# deployment that has never been upgraded by these scripts still has a usable
# rollback target as soon as it is upgraded once.
record_current() {
  local tag="$1"
  [[ -f "$HISTORY_FILE" ]] || : > "$HISTORY_FILE"
  if [[ "$(tail -1 "$HISTORY_FILE" 2>/dev/null | cut -d' ' -f2 || true)" != "$tag" ]]; then
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$tag" >> "$HISTORY_FILE"
  fi
}
record_current "$CURRENT"

if [[ "${LIST_ONLY:-false}" == true ]]; then
  log "image history for this deployment (oldest first):"
  if [[ -s "$HISTORY_FILE" ]]; then
    nl -ba "$HISTORY_FILE" | sed 's/^/  /'
  else
    echo "  (none recorded)"
  fi
  log "currently running: ${CURRENT}"
  exit 0
fi

# Resolve the target: an explicit argument, else the entry before the current one.
if [[ -z "$TARGET" ]]; then
  TARGET="$(grep -v " ${CURRENT}\$" "$HISTORY_FILE" 2>/dev/null | tail -1 | cut -d' ' -f2 || true)"
  [[ -n "$TARGET" ]] || die "no previous tag recorded for this deployment. Pass one explicitly: ./rollback.sh 0.6.0"
fi

[[ "$TARGET" != "$CURRENT" ]] || die "already running ${CURRENT}; nothing to roll back to"

IMAGE="pipeshubai/pipeshub-ai:${TARGET}"

log "current : ${CURRENT}"
log "target  : ${TARGET}"

# Fail before touching the deployment if the target does not exist. Discovering
# a typo after .env has been rewritten is a far worse place to be.
if ! docker manifest inspect "$IMAGE" >/dev/null 2>&1; then
  die "${IMAGE} is not available in the registry. Check the tag with: ./rollback.sh --list"
fi
log "verified ${IMAGE} exists"

# Warn where a rollback cannot fully undo the release.
if [[ -n "$(env_val DATA_STORE)" ]]; then
  cat <<EOF

  ${LOG_PREFIX}: before you continue
  ---------------------------------------------------------------
  Rolling back changes the code, not the data. If ${CURRENT} ran a
  migration that rewrote existing records, the older image may not
  understand them, and the correct recovery is restoring a backup
  rather than rolling back.

  Safe to roll back:  a bug in request handling, UI, or a connector
  Restore instead:    anything that changed stored data on upgrade
  ---------------------------------------------------------------
EOF
fi

if [[ "$DRY_RUN" == true ]]; then
  log "dry run — would set IMAGE_TAG=${TARGET} in ${ENV_FILE} and run: ./install.sh --yes --upgrade"
  exit 0
fi

# Rewrite IMAGE_TAG in place, keeping a copy so a failed rollback is itself
# reversible.
cp "$ENV_FILE" "${ENV_FILE}.before-rollback"
if grep -qE '^IMAGE_TAG=' "$ENV_FILE"; then
  sed -i.bak -E "s|^IMAGE_TAG=.*|IMAGE_TAG=${TARGET}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
else
  printf 'IMAGE_TAG=%s\n' "$TARGET" >> "$ENV_FILE"
fi
log "set IMAGE_TAG=${TARGET} (previous .env saved as ${ENV_FILE}.before-rollback)"

log "re-running the installer's upgrade path"
if ! (cd "$SCRIPT_DIR" && bash ./install.sh --yes --upgrade); then
  log "upgrade path failed; restoring the previous .env"
  mv "${ENV_FILE}.before-rollback" "$ENV_FILE"
  die "rollback to ${TARGET} failed. .env restored; the stack may be in a mixed state — check 'docker compose ps'."
fi

record_current "$TARGET"
log "rolled back to ${TARGET}"
log "verify with: curl -fsS http://localhost:$(env_val APP_PORT)/api/v1/health/services"

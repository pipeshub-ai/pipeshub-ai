#!/usr/bin/env bash
# deploy-dev.sh
#
# Merges the current branch into `development` and pushes it, triggering
# the CI/CD pipeline to deploy to the dev environment.
#
# Usage:
#   ./scripts/deploy-dev.sh
#
# For AI agents (e.g. Cursor, Copilot Agent, Replit Agent):
#   "Run scripts/deploy-dev.sh to deploy the current branch to the dev environment."
#
# The script will pause and ask you to resolve any merge conflicts before
# continuing. After resolving, just re-run the script — it detects that a
# merge is already in progress and skips straight to the push.

set -euo pipefail

DEV_BRANCH="development"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

if [ "$CURRENT_BRANCH" = "$DEV_BRANCH" ]; then
  echo "Already on '$DEV_BRANCH'. Just pushing..."
  git push origin "$DEV_BRANCH"
  exit 0
fi

# ── Resume path: if a merge is already in progress (user resolved conflicts) ─
if [ -f "$(git rev-parse --git-dir)/MERGE_HEAD" ]; then
  echo "Resuming merge in progress..."
  echo "→ Committing resolved conflicts..."
  git commit --no-edit
  echo "→ Pushing '$DEV_BRANCH' to remote (triggers dev deployment)..."
  git push origin "$DEV_BRANCH"
  echo "→ Checking back to '$CURRENT_BRANCH'..."
  git checkout "$CURRENT_BRANCH"
  echo "✓ Done. Dev deployment triggered from branch '$CURRENT_BRANCH'."
  exit 0
fi

# ── Normal path ───────────────────────────────────────────────────────────────
echo "→ Pushing '$CURRENT_BRANCH' to remote..."
git push origin "$CURRENT_BRANCH"

echo "→ Checking out '$DEV_BRANCH'..."
git checkout "$DEV_BRANCH"
git pull origin "$DEV_BRANCH" --no-rebase

echo "→ Merging '$CURRENT_BRANCH' into '$DEV_BRANCH'..."
if ! git merge "$CURRENT_BRANCH" --no-edit; then
  echo ""
  echo "⚠ Merge conflicts detected in the following files:"
  git diff --name-only --diff-filter=U
  echo ""
  echo "Resolve the conflicts, then re-run this script:"
  echo "  ./scripts/deploy-dev.sh"
  echo ""
  echo "Or to abort and go back to your branch:"
  echo "  git merge --abort && git checkout $CURRENT_BRANCH"
  exit 1
fi

echo "→ Pushing '$DEV_BRANCH' to remote (triggers dev deployment)..."
git push origin "$DEV_BRANCH"

echo "→ Checking back to '$CURRENT_BRANCH'..."
git checkout "$CURRENT_BRANCH"

echo "✓ Done. Dev deployment triggered from branch '$CURRENT_BRANCH'."

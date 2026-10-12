#!/usr/bin/env bash
# Put the SDK test report on a pull request, and set the FLAG label and approval status.
#
# Usage: update_pull_request.sh <pull request number> <verdict> <summary.md>
#
# Reads GH_TOKEN, GITHUB_REPOSITORY, HEAD_SHA (the commit under test) and RUN_URL.
# The pull request has one report comment; each run edits it.
#   FLAG:  label breaks-previous-sdk, and a failed "SDK break approval" status until
#          the pull request has the label sdk-break-approved (then the status names who
#          added it). sdk-break-approval.yml updates the status when that label changes.
#   OK:    removes the label breaks-previous-sdk.
#   BLOCK: changes no label: the previous run did not run, so it says nothing new.
set -euo pipefail

PR=$1
VERDICT=$2
SUMMARY=$3
REPO=${GITHUB_REPOSITORY:?}
FLAG_LABEL=breaks-previous-sdk
APPROVAL_LABEL=sdk-break-approved

# report.py starts the summary with a hidden marker line; it finds the earlier comment.
marker=$(head -n 1 "$SUMMARY")
comment_id=$(gh api --paginate "repos/$REPO/issues/$PR/comments" \
  | jq -r --arg marker "$marker" \
    '.[] | select(.user.login == "github-actions[bot]" and (.body | startswith($marker))) | .id' \
  | tail -n 1)
if [ -n "$comment_id" ]; then
  gh api -X PATCH "repos/$REPO/issues/comments/$comment_id" -F "body=@$SUMMARY" > /dev/null
else
  gh api -X POST "repos/$REPO/issues/$PR/comments" -F "body=@$SUMMARY" > /dev/null
fi

labels=$(gh api "repos/$REPO/issues/$PR/labels" --jq '.[].name')
case "$VERDICT" in
  FLAG)
    gh label create "$FLAG_LABEL" --repo "$REPO" --color D93F0B \
      --description "Breaks the last released SDK" 2> /dev/null || true
    gh label create "$APPROVAL_LABEL" --repo "$REPO" --color 0E8A16 \
      --description "A person read the SDK break and accepts it" 2> /dev/null || true
    gh api -X POST "repos/$REPO/issues/$PR/labels" -f "labels[]=$FLAG_LABEL" > /dev/null

    if grep -qxF "$APPROVAL_LABEL" <<< "$labels"; then
      approver=$(gh api --paginate "repos/$REPO/issues/$PR/events" \
        | jq -r --arg label "$APPROVAL_LABEL" \
          '.[] | select(.event == "labeled" and .label.name == $label) | .actor.login' \
        | tail -n 1)
      state=success
      description="Approved by @${approver:-unknown}"
    else
      state=failure
      description="Breaks the last released SDK: read the report, then add the label $APPROVAL_LABEL"
    fi
    gh api "repos/$REPO/statuses/$HEAD_SHA" -f state="$state" -f context="SDK break approval" \
      -f description="$description" -f target_url="${RUN_URL:?}" > /dev/null
    ;;
  OK)
    if grep -qxF "$FLAG_LABEL" <<< "$labels"; then
      gh api -X DELETE "repos/$REPO/issues/$PR/labels/$FLAG_LABEL" > /dev/null
    fi
    ;;
esac

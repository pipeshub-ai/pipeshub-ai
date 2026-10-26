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
#          An approval covers the failures it was given for: when they differ from the
#          last report's, the label sdk-break-approved goes.
#   OK:    removes both labels.
#   BLOCK: changes no label: the previous run did not run, so it says nothing new.
set -euo pipefail

PR=$1
VERDICT=$2
SUMMARY=$3
REPO=${GITHUB_REPOSITORY:?}
FLAG_LABEL=breaks-previous-sdk
APPROVAL_LABEL=sdk-break-approved

# report.py starts the summary with a hidden marker line; it finds the earlier comment.
# The next hidden line lists the previous-run failures, which an approval covers.
marker=$(head -n 1 "$SUMMARY")
flag_cases=$(sed -n 2p "$SUMMARY")
previous_flag_cases=""
comment=$(gh api --paginate "repos/$REPO/issues/$PR/comments" \
  | jq -c --arg marker "$marker" \
    '.[] | select(.user.login == "github-actions[bot]" and (.body | startswith($marker))) | {id, body}' \
  | tail -n 1)
if [ -n "$comment" ]; then
  previous_flag_cases=$(jq -r '.body' <<< "$comment" | sed -n 2p)
  gh api -X PATCH "repos/$REPO/issues/comments/$(jq -r '.id' <<< "$comment")" \
    -F "body=@$SUMMARY" > /dev/null
else
  gh api -X POST "repos/$REPO/issues/$PR/comments" -F "body=@$SUMMARY" > /dev/null
fi

remove_label() {
  if grep -qxF "$1" <<< "$labels"; then
    gh api -X DELETE "repos/$REPO/issues/$PR/labels/$1" > /dev/null
    labels=$(grep -vxF "$1" <<< "$labels" || true)
  fi
}

labels=$(gh api "repos/$REPO/issues/$PR/labels" --jq '.[].name')
case "$VERDICT" in
  FLAG)
    gh label create "$FLAG_LABEL" --repo "$REPO" --color D93F0B \
      --description "Breaks the last released SDK" 2> /dev/null || true
    gh label create "$APPROVAL_LABEL" --repo "$REPO" --color 0E8A16 \
      --description "A person read the SDK break and accepts it" 2> /dev/null || true
    gh api -X POST "repos/$REPO/issues/$PR/labels" -f "labels[]=$FLAG_LABEL" > /dev/null

    if [ "$flag_cases" != "$previous_flag_cases" ]; then
      remove_label "$APPROVAL_LABEL"
    fi
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
    remove_label "$FLAG_LABEL"
    remove_label "$APPROVAL_LABEL"
    ;;
esac

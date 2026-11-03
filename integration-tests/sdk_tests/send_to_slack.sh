#!/usr/bin/env bash
# Post the SDK test report to Slack, as the integration tests do: the message, then in
# its thread the reports zip and the failed cases.
#
# Usage: send_to_slack.sh <slack.json> <reports zip>
#
# <slack.json> is what report.py writes. Reads SLACK_BOT_TOKEN and SLACK_CHANNEL_ID;
# without them it posts nothing.
set -euo pipefail

PAYLOADS=$1
ZIP=$2

if [ -z "${SLACK_BOT_TOKEN:-}" ] || [ -z "${SLACK_CHANNEL_ID:-}" ]; then
  echo "::notice::SLACK_BOT_TOKEN or SLACK_CHANNEL_ID is not set, so the report is not sent to Slack."
  exit 0
fi

# Prints Slack's answer; fails when Slack says the call was not ok.
check() {
  jq -e 'if .ok then . else error("Slack refused the call: \(.error)") end'
}

post_json() {
  curl -sS --fail-with-body -X POST "https://slack.com/api/$1" \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    -H "Content-Type: application/json; charset=utf-8" --data-binary @- | check
}

ts=$(jq --arg channel "$SLACK_CHANNEL_ID" '.message + {channel: $channel}' "$PAYLOADS" \
  | post_json chat.postMessage | jq -r '.ts')

if [ -f "$ZIP" ]; then
  upload=$(curl -sS --fail-with-body -X POST "https://slack.com/api/files.getUploadURLExternal" \
    -H "Authorization: Bearer ${SLACK_BOT_TOKEN}" \
    --data-urlencode "filename=$(basename "$ZIP")" -d "length=$(stat -c%s "$ZIP")" | check)
  curl -sS --fail-with-body -X POST "$(jq -r '.upload_url' <<< "$upload")" -F "file=@$ZIP" > /dev/null
  jq -n --arg id "$(jq -r '.file_id' <<< "$upload")" --arg channel "$SLACK_CHANNEL_ID" \
    --arg ts "$ts" --arg title "SDK test reports" \
    '{files: [{id: $id, title: $title}], channel_id: $channel, thread_ts: $ts}' \
    | post_json files.completeUploadExternal > /dev/null
fi

if jq -e '.thread' "$PAYLOADS" > /dev/null; then
  jq --arg channel "$SLACK_CHANNEL_ID" --arg ts "$ts" \
    '.thread + {channel: $channel, thread_ts: $ts}' "$PAYLOADS" \
    | post_json chat.postMessage > /dev/null
fi

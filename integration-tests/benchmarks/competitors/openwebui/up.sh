#!/usr/bin/env bash
# Bring up Open WebUI for the FRAMES comparison and print an API key.
#   integration-tests/benchmarks/competitors/openwebui/up.sh
# Reads Azure settings from integration-tests/.env.local. The API key is
# written to $OWUI_KEY_FILE (default ~/.frames-openwebui-key, mode 600).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
set -a; . "$here/../../../.env.local"; set +a
endpoint="${AZURE_OPENAI_ENDPOINT%/}"
export OWUI_AZURE_BASE_URL="$endpoint"
export OWUI_AZURE_V1_BASE_URL="$endpoint/openai/v1"
docker compose -f "$here/docker-compose.yml" up -d

base="http://localhost:8080"
until curl -sf "$base/health" >/dev/null; do sleep 3; done

email="frames@benchmark.local"
password="${OWUI_ADMIN_PASSWORD:-frames-benchmark}"
token="$(curl -sf -X POST "$base/api/v1/auths/signin" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$email\",\"password\":\"$password\"}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])' 2>/dev/null || true)"
if [ -z "$token" ]; then
  token="$(curl -sf -X POST "$base/api/v1/auths/signup" -H 'Content-Type: application/json' \
    -d "{\"name\":\"FRAMES\",\"email\":\"$email\",\"password\":\"$password\"}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')"
fi
key="$(curl -sf -X POST "$base/api/v1/auths/api_key" -H "Authorization: Bearer $token" | python3 -c 'import json,sys;print(json.load(sys.stdin)["api_key"])')"
key_file="${OWUI_KEY_FILE:-$HOME/.frames-openwebui-key}"
umask 077; printf '%s' "$key" > "$key_file"
echo "Open WebUI is up at $base; API key written to $key_file"
echo "export OPENWEBUI_API_KEY=\$(cat $key_file)"

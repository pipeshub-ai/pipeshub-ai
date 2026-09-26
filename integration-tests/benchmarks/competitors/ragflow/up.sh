#!/usr/bin/env bash
# Bring up RAGFlow v0.27.2 for the FRAMES comparison, register Azure OpenAI
# (answer and embedding models) and print an API key.
#   integration-tests/benchmarks/competitors/ragflow/up.sh [path/to/ragflow/checkout]
# Stop PipesHub's stack first: RAGFlow wants ~8 GB and uses ports 6379/9000.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="${1:-$HOME/opensource/rag-competitors/ragflow}"
set -a; . "$here/../../../.env.local"; set +a

# Elasticsearch 4 GB instead of 8 so it fits beside the harness; embeddings
# in batches of 64 instead of 16.
export MEM_LIMIT=4294967296 EMBEDDING_BATCH_SIZE=64 EXPOSE_MYSQL_PORT=13306
docker compose -p frames-ragflow --project-directory "$repo/docker" \
  -f "$repo/docker/docker-compose.yml" -f "$here/docker-compose.override.yml" up -d

base="http://localhost:9380"
until curl -sf "$base/api/v1/system/healthz" >/dev/null; do sleep 5; done

password="${RAGFLOW_ADMIN_PASSWORD:-frames-benchmark}"
encrypted="$(docker exec frames-ragflow-ragflow-cpu-1 python3 -c \
  "from api.utils.crypt import crypt; print(crypt('$password'))")"
key_file="${RAGFLOW_KEY_FILE:-$HOME/.frames-ragflow-key}"
"$here/../../../.venv/bin/python" - "$base" "$encrypted" "$key_file" <<'PY'
import json, os, sys
import requests

base, encrypted, key_file = sys.argv[1:]
email = "frames@benchmark.local"
requests.post(f"{base}/api/v1/users", json={"email": email, "nickname": "frames", "password": encrypted}, timeout=60)
login = requests.post(f"{base}/api/v1/auth/login", json={"email": email, "password": encrypted}, timeout=60)
login.raise_for_status()
auth = {"Authorization": login.headers["Authorization"]}

def call(method, path, **kw):
    r = requests.request(method, f"{base}/api/v1{path}", headers=auth, timeout=120, **kw)
    r.raise_for_status()
    body = r.json()
    if body.get("code") != 0:
        raise SystemExit(f"{method} {path}: {body}")
    return body.get("data")

def add_provider(name):
    r = requests.put(f"{base}/api/v1/providers", headers=auth, timeout=120, json={"provider_name": name})
    r.raise_for_status()
    body = r.json()
    # 102: already added by an earlier run.
    if body.get("code") not in (0, 102):
        raise SystemExit(f"PUT /providers {name}: {body}")

token = call("POST", "/system/tokens")
api_key = token["token"] if isinstance(token, dict) else token
os.umask(0o077)
open(key_file, "w").write(api_key)

# Azure OpenAI with both models. The key is JSON: RAGFlow's Azure chat model
# parses it for the api_version.
add_provider("Azure-OpenAI")
instances = call("GET", "/providers/Azure-OpenAI/instances") or []
if not any((i.get("instance_name") or i.get("name")) == "azure" for i in instances):
    call("POST", "/providers/Azure-OpenAI/instances", json={
        "instance_name": "azure",
        "base_url": os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/"),
        "region": "",
        "api_key": json.dumps({
            "api_key": os.environ["AZURE_OPENAI_API_KEY"],
            "api_version": os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
        }),
        "model_info": [
            # max_tokens caps how much retrieved text fits in the prompt.
            # is_tools: RAGFlow's reasoning mode falls back to one-shot
            # retrieval for a model not marked as tool-calling.
            {"model_type": ["chat"], "model_name": "gpt-5.6-luna", "max_tokens": 272000, "is_tools": True},
            {"model_type": ["embedding"], "model_name": "text-embedding-3-small", "max_tokens": 8191},
        ],
    })
# An instance registered before is_tools was set keeps its old model record.
call("PATCH", "/providers/Azure-OpenAI/instances/azure/models/gpt-5.6-luna",
     json={"max_tokens": 272000, "extra": {"max_tokens": 272000, "is_tools": True}})

# The RAG baselines' cross-encoder, served on the host by
# `python -m benchmarks.harness.systems.rag.rerank_server` (TEI's /rerank shape).
add_provider("HuggingFace")
instances = call("GET", "/providers/HuggingFace/instances") or []
if not any((i.get("instance_name") or i.get("name")) == "frames-rerank" for i in instances):
    call("POST", "/providers/HuggingFace/instances", json={
        "instance_name": "frames-rerank",
        "base_url": os.environ.get("FRAMES_RERANK_URL", "http://host.docker.internal:8787"),
        "region": "",
        "api_key": "unused",
        "model_info": [{"model_type": ["rerank"], "model_name": "cross-encoder/ms-marco-MiniLM-L-6-v2", "max_tokens": 512}],
    })
print(f"RAGFlow is up at {base}; API key written to {key_file}")
PY
echo "export RAGFLOW_API_KEY=\$(cat $key_file)"

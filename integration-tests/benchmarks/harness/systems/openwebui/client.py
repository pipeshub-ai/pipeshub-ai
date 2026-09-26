"""HTTP calls to an Open WebUI instance (v0.11), authenticated with an API key.

Only the endpoints the benchmark needs: a knowledge base, file upload into
it, file processing status, and one non-streaming chat completion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from benchmarks.harness.retry import http_retry, raise_for_transient

# Terminal states of `/api/v1/files/{id}/process/status`.
FILE_DONE = "completed"
FILE_FAILED = "failed"


@dataclass(frozen=True)
class InstanceSettings:
    """The answer-shaping settings Open WebUI holds instance-wide, in memory:
    a restart reloads them from the environment. An empty template means
    Open WebUI's built-in default."""

    query_generation: bool = False
    rag_template: str = ""
    # Chunks retrieved, then how many the reranker keeps; no reranker by default.
    top_k: int = 50
    top_k_reranker: int = 50
    reranking_engine: str = ""
    reranking_model: str = ""
    external_reranker_url: str = ""


# InstanceSettings field -> key in /api/v1/retrieval/config (read and update).
_RETRIEVAL_KEYS = {
    "rag_template": "RAG_TEMPLATE",
    "top_k": "TOP_K",
    "top_k_reranker": "TOP_K_RERANKER",
    "reranking_engine": "RAG_RERANKING_ENGINE",
    "reranking_model": "RAG_RERANKING_MODEL",
    "external_reranker_url": "RAG_EXTERNAL_RERANKER_URL",
}


@dataclass(frozen=True)
class ChatResult:
    body: dict[str, Any]
    elapsed_ms: int


class OpenWebUIClient:
    def __init__(self, base_url: str, api_key: str, *, timeout_s: float = 600.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout_s = timeout_s

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:  # noqa: ANN401
        resp = requests.request(
            method, f"{self._base_url}{path}", headers=self._headers, timeout=self._timeout_s, **kwargs,
        )
        raise_for_transient(resp)
        resp.raise_for_status()
        return resp

    @http_retry()
    def knowledge_id(self, name: str) -> str | None:
        resp = self._request("GET", "/api/v1/knowledge/")
        payload = resp.json()
        items = payload.get("items", payload) if isinstance(payload, dict) else payload
        for item in items or []:
            if item.get("name") == name:
                return str(item["id"])
        return None

    @http_retry()
    def create_knowledge(self, name: str, description: str) -> str:
        resp = self._request("POST", "/api/v1/knowledge/create", json={"name": name, "description": description})
        return str(resp.json()["id"])

    @http_retry()
    def upload_file(self, filename: str, content: bytes, mime: str, knowledge_id: str) -> str:
        """Upload one file; the server processes it in the background and adds
        it to the knowledge base named in the metadata."""
        resp = self._request(
            "POST", "/api/v1/files/",
            files={"file": (filename, content, mime)},
            data={"metadata": json.dumps({"knowledge_id": knowledge_id})},
        )
        return str(resp.json()["id"])

    @http_retry()
    def knowledge_files(self, knowledge_id: str) -> dict[str, list[str]]:
        """File name -> ids of the files linked to the knowledge base."""
        files: dict[str, list[str]] = {}
        page, limit = 1, 500
        while True:
            body = self._request(
                "GET", f"/api/v1/knowledge/{knowledge_id}/files", params={"page": page, "limit": limit},
            ).json()
            items = body.get("items") or []
            for item in items:
                files.setdefault(str(item.get("filename", "")), []).append(str(item["id"]))
            if len(items) < limit:
                return files
            page += 1

    @http_retry()
    def file_status(self, file_id: str) -> str:
        resp = self._request("GET", f"/api/v1/files/{file_id}/process/status")
        return str(resp.json().get("status", ""))

    @http_retry()
    def file_state(self, file_id: str) -> tuple[str, float]:
        """Processing status and the epoch second the file record last changed."""
        body = self._request("GET", f"/api/v1/files/{file_id}").json()
        return str((body.get("data") or {}).get("status", "")), float(body.get("updated_at") or 0)

    @http_retry()
    def instance_settings(self) -> InstanceSettings:
        tasks = self._request("GET", "/api/v1/tasks/config").json()
        retrieval = self._request("GET", "/api/v1/retrieval/config").json()
        defaults = InstanceSettings()
        values = {
            name: type(getattr(defaults, name))(retrieval.get(key) or getattr(defaults, name))
            for name, key in _RETRIEVAL_KEYS.items()
        }
        return InstanceSettings(query_generation=bool(tasks.get("ENABLE_RETRIEVAL_QUERY_GENERATION")), **values)

    @http_retry()
    def apply_settings(self, wanted: InstanceSettings) -> InstanceSettings:
        """Writes only what differs and returns what the server then holds.
        The task update takes the whole task config; the retrieval update
        keeps every field it is not sent."""
        held = self.instance_settings()
        if held.query_generation != wanted.query_generation:
            tasks = self._request("GET", "/api/v1/tasks/config").json()
            tasks["ENABLE_RETRIEVAL_QUERY_GENERATION"] = wanted.query_generation
            self._request("POST", "/api/v1/tasks/config/update", json=tasks)
        changed = {
            key: getattr(wanted, name) for name, key in _RETRIEVAL_KEYS.items()
            if getattr(held, name) != getattr(wanted, name)
        }
        if changed:
            self._request("POST", "/api/v1/retrieval/config/update", json=changed)
        return self.instance_settings()

    @http_retry(attempts=3)
    def retrieval_config(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/retrieval/config").json()

    @http_retry(attempts=3)
    def start_ui_chat(self, body: dict[str, Any]) -> str:
        """Start a chat the way the web UI does; the answer is produced in the
        background and saved to the chat. Returns the chat id."""
        return str(self._request("POST", "/api/chat/completions", json=body).json()["chat_id"])

    @http_retry()
    def saved_chat(self, chat_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/chats/{chat_id}").json()

    def delete_chat(self, chat_id: str) -> None:
        self._request("DELETE", f"/api/v1/chats/{chat_id}")

    @http_retry(attempts=3)
    def chat(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/chat/completions", json=body).json()

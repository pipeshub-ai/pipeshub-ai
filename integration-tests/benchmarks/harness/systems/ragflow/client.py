"""HTTP calls to a RAGFlow instance (v0.27 REST API), authenticated with an
API key. Every response is `{"code": 0, "data": ...}` on success."""

from __future__ import annotations

from typing import Any

import requests

from benchmarks.harness.retry import http_retry, raise_for_transient

# Document run states as the API names them (`common/constants.py::TaskStatus`).
RUN_DONE = "DONE"
RUN_FAILED = frozenset({"FAIL", "CANCEL"})


class RagflowApiError(RuntimeError):
    """RAGFlow answered with a non-zero `code`."""


class RagflowClient:
    def __init__(self, base_url: str, api_key: str, *, timeout_s: float = 900.0) -> None:
        self._base = base_url.rstrip("/") + "/api/v1"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout_s = timeout_s

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:  # noqa: ANN401
        resp = requests.request(method, f"{self._base}{path}", headers=self._headers, timeout=self._timeout_s, **kwargs)
        raise_for_transient(resp)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RagflowApiError(f"{method} {path}: {body.get('code')} {body.get('message')}")
        return body.get("data")

    def _find(self, path: str, name: str) -> str | None:
        """The id of the item named `name`. Listed and matched here: a
        `name=` filter that matches nothing is answered as a permission
        error, not an empty list."""
        page = 1
        while True:
            data = self._call("GET", path, params={"page": page, "page_size": 100})
            if isinstance(data, list):
                items = data
            else:  # `/chats` wraps its page as {"chats": [...], "total": n}
                items = next((v for v in (data or {}).values() if isinstance(v, list)), [])
            for item in items:
                if item.get("name") == name:
                    return str(item["id"])
            if len(items) < 100:
                return None
            page += 1

    @http_retry()
    def dataset_id(self, name: str) -> str | None:
        return self._find("/datasets", name)

    @http_retry()
    def create_dataset(self, body: dict[str, Any]) -> str:
        return str(self._call("POST", "/datasets", json=body)["id"])

    @http_retry()
    def upload(self, dataset_id: str, files: list[tuple[str, bytes, str]]) -> dict[str, str]:
        """Upload files in one request; returns file name -> document id."""
        docs = self._call(
            "POST", f"/datasets/{dataset_id}/documents",
            files=[("file", (name, content, mime)) for name, content, mime in files],
        )
        return {str(d["name"]): str(d["id"]) for d in docs or []}

    @http_retry()
    def parse(self, dataset_id: str, document_ids: list[str]) -> None:
        self._call("POST", f"/datasets/{dataset_id}/documents/parse", json={"document_ids": document_ids})

    @http_retry()
    def documents(self, dataset_id: str, page: int, page_size: int) -> tuple[list[dict[str, Any]], int]:
        data = self._call(
            "GET", f"/datasets/{dataset_id}/documents", params={"page": page, "page_size": page_size},
        )
        return list(data.get("docs") or []), int(data.get("total") or 0)

    @http_retry()
    def chat_id(self, name: str) -> str | None:
        return self._find("/chats", name)

    @http_retry()
    def create_chat(self, body: dict[str, Any]) -> str:
        return str(self._call("POST", "/chats", json=body)["id"])

    @http_retry()
    def get_chat(self, chat_id: str) -> dict[str, Any]:
        return dict(self._call("GET", f"/chats/{chat_id}") or {})

    @http_retry()
    def update_chat(self, chat_id: str, body: dict[str, Any]) -> None:
        self._call("PATCH", f"/chats/{chat_id}", json=body)

    @http_retry(attempts=3)
    def complete(self, chat_id: str, question: str, reasoning: str | None = None) -> dict[str, Any]:
        """One question in a fresh session. `store_history_messages=False`
        keeps the session out of RAGFlow's history; it then needs the whole
        conversation, which is the one message. `reasoning` ("1".."4": low,
        medium, high, ultra) runs RAGFlow's agentic research loop instead of
        one retrieval; the level is read from the request, not the chat."""
        body: dict[str, Any] = {
            "chat_id": chat_id, "stream": False,
            "messages": [{"role": "user", "content": question}],
            "store_history_messages": False, "pass_all_history_messages": True,
        }
        if reasoning:
            body["reasoning"] = reasoning
        return self._call("POST", "/chat/completions", json=body) or {}

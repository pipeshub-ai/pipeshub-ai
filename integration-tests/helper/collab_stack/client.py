"""HTTP client for the Node API under test, with SSE support."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from helper.collab_stack.identity import Actor, Directory

REQUEST_TIMEOUT_S = 60


@dataclass
class SseEvent:
    event: str
    data: Any
    raw: str


def parse_sse(text: str) -> list[SseEvent]:
    events: list[SseEvent] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name, data_lines = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
        raw = "\n".join(data_lines)
        try:
            data: Any = json.loads(raw)
        except ValueError:
            data = raw
        events.append(SseEvent(name, data, raw))
    return events


class Api:
    """Calls the API as an actor (session JWT), as a raw bearer token, or anonymously."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = requests.Session()

    def headers(self, who: Actor | None = None, token: str | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
        out: dict[str, str] = {}
        if token is None and who is not None:
            token = Directory.session_token(who)
        if token:
            out["Authorization"] = f"Bearer {token}"
        out.update(extra or {})
        return out

    def call(
        self,
        method: str,
        path: str,
        who: Actor | None = None,
        *,
        token: str | None = None,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: Any = None,
    ) -> requests.Response:
        return self._http.request(
            method.upper(),
            f"{self.base_url}{path}",
            headers=self.headers(who, token, headers),
            json=json_body,
            params=params,
            files=files,
            timeout=REQUEST_TIMEOUT_S,
        )

    def get(self, path: str, who: Actor | None = None, **kw: Any) -> requests.Response:
        return self.call("GET", path, who, **kw)

    def post(self, path: str, who: Actor | None = None, **kw: Any) -> requests.Response:
        return self.call("POST", path, who, **kw)

    def put(self, path: str, who: Actor | None = None, **kw: Any) -> requests.Response:
        return self.call("PUT", path, who, **kw)

    def patch(self, path: str, who: Actor | None = None, **kw: Any) -> requests.Response:
        return self.call("PATCH", path, who, **kw)

    def delete(self, path: str, who: Actor | None = None, **kw: Any) -> requests.Response:
        return self.call("DELETE", path, who, **kw)

    def stream(
        self,
        path: str,
        who: Actor | None = None,
        *,
        token: str | None = None,
        json_body: Any = None,
        method: str = "POST",
        headers: dict[str, str] | None = None,
    ) -> StreamCall:
        call = StreamCall(self, method, path, self.headers(who, token, headers), json_body)
        call.start()
        return call


@dataclass
class StreamCall:
    """A streaming request read on a background thread, so a test can act while it is open."""

    api: Api
    method: str
    path: str
    headers: dict[str, str]
    body: Any
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    error: Exception | None = None
    _resp: requests.Response | None = None
    _thread: threading.Thread | None = None
    _headers_ready: threading.Event = field(default_factory=threading.Event)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._headers_ready.wait(REQUEST_TIMEOUT_S):
            raise TimeoutError(f"no response headers for {self.method} {self.path}")
        if self.error:
            raise self.error

    def _run(self) -> None:
        try:
            self._resp = requests.request(
                self.method,
                f"{self.api.base_url}{self.path}",
                headers=self.headers,
                json=self.body,
                stream=True,
                timeout=(10, REQUEST_TIMEOUT_S),
            )
            self.status = self._resp.status_code
            self.response_headers = dict(self._resp.headers)
            self._headers_ready.set()
            for chunk in self._resp.iter_content(chunk_size=None):
                with self._lock:
                    self.text += chunk.decode(errors="replace")
        except Exception as exc:  # noqa: BLE001 - surfaced through .error
            self.error = exc
        finally:
            self._headers_ready.set()

    @property
    def events(self) -> list[SseEvent]:
        with self._lock:
            return parse_sse(self.text)

    def wait_for(self, event: str, timeout: float = 20) -> SseEvent:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for e in self.events:
                if e.event == event:
                    return e
            if self._thread and not self._thread.is_alive():
                break
            time.sleep(0.05)
        raise TimeoutError(f"no {event!r} event within {timeout}s; got {[e.event for e in self.events]}")

    def event_named(self, event: str) -> SseEvent | None:
        return next((e for e in self.events if e.event == event), None)

    def finish(self, timeout: float = 30) -> StreamCall:
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                raise TimeoutError(f"stream {self.path} did not finish within {timeout}s")
        return self

    def abort(self) -> None:
        """Drop the connection from the client side, as a closed tab does."""
        if self._resp is not None:
            self._resp.close()
        if self._thread:
            self._thread.join(5)

    @property
    def conversation_id(self) -> str | None:
        created = next((e for e in self.events if e.event == "CUSTOM" and e.data.get("name") == "conversation_created"), None)
        return created.data["value"]["conversationId"] if created else None

    @property
    def result(self) -> dict | None:
        """The conversation payload of the root RUN_FINISHED, once the turn is saved."""
        finished = next((e for e in reversed(self.events) if e.event == "RUN_FINISHED" and isinstance(e.data, dict) and "result" in e.data), None)
        return finished.data["result"] if finished else None

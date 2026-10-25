"""A real LiteLLM proxy with a database, in Docker, in front of a fake provider.

The proxy is the system whose behaviour identity forwarding depends on (customer
budgets, tag budgets, team model access, the exact refusal bodies), so these
tests run it unmocked. The provider behind it is a local fake that answers every
chat request with a fixed usage, so spend is predictable and nothing is billed.

Needs a Docker daemon and network access to pull the images; run with ``-m docker``.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator

LITELLM_IMAGE = "ghcr.io/berriai/litellm-database"
# The version the feature is verified against. Tag budgets on the
# ``x-litellm-tags`` header need a release with BerriAI/litellm#27573.
DEFAULT_LITELLM_VERSIONS = ("v1.106.0",)
POSTGRES_IMAGE = "postgres:16.4-alpine"
MASTER_KEY = "sk-master-pipeshub-test"
TOKEN_COST = 0.01
PROMPT_TOKENS = 10
COMPLETION_TOKENS = 10
CALL_COST = TOKEN_COST * (PROMPT_TOKENS + COMPLETION_TOKENS)
# A cold container spends minutes compiling Python and applying ~200 migrations
# before it answers; a warmed image (see ``_warmed_image``) skips the compile.
STARTUP_TIMEOUT_SECONDS = 900
WARM_IMAGE_REPOSITORY = "pipeshub-litellm-test"


def litellm_versions() -> tuple[str, ...]:
    raw = os.environ.get("LITELLM_TEST_VERSIONS", "")
    versions = tuple(v.strip() for v in raw.split(",") if v.strip())
    return versions or DEFAULT_LITELLM_VERSIONS


def docker_ready() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "docker is not installed"
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return False, "no reachable Docker daemon"
    return True, ""


def _warmed_image(version: str) -> str:
    """The image to start: the one warmed by an earlier run, else the upstream one."""
    warmed = f"{WARM_IMAGE_REPOSITORY}:{version}"
    found = _docker("image", "inspect", warmed, check=False)
    return warmed if found.returncode == 0 else f"{LITELLM_IMAGE}:{version}"


def _remember_warmed(version: str, container: str) -> None:
    warmed = f"{WARM_IMAGE_REPOSITORY}:{version}"
    if _docker("image", "inspect", warmed, check=False).returncode != 0:
        _docker("commit", container, warmed, check=False)


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True, timeout=300)


class FakeProvider:
    """An OpenAI-compatible endpoint that records what it was asked and answers with fixed usage."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        provider = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                provider.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                payload = json.dumps(
                    {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body.get("model", "fake"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "ok"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": PROMPT_TOKENS,
                            "completion_tokens": COMPLETION_TOKENS,
                            "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@dataclass
class LiteLLMStack:
    version: str
    base_url: str
    provider: FakeProvider
    master_key: str = MASTER_KEY
    containers: list[str] = field(default_factory=list)

    @property
    def admin_headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.master_key}"}

    def admin(self, method: str, path: str, **kwargs: Any) -> httpx.Response:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
        response = httpx.request(
            method, f"{self.base_url}{path}", headers=self.admin_headers, timeout=30, **kwargs
        )
        if response.status_code >= 400:
            raise AssertionError(f"{method} {path} -> {response.status_code}: {response.text[:500]}")
        return response

    def make_budget(self, max_budget: float) -> str:
        budget_id = f"budget-{uuid.uuid4().hex[:8]}"
        self.admin("POST", "/budget/new", json={"budget_id": budget_id, "max_budget": max_budget})
        return budget_id

    def make_customer(self, user_id: str, max_budget: float) -> None:
        self.admin(
            "POST",
            "/customer/new",
            json={"user_id": user_id, "budget_id": self.make_budget(max_budget)},
        )

    def customer_spend(self, user_id: str) -> float:
        """Spend of a customer; zero until LiteLLM has created it on first sight."""
        response = httpx.get(
            f"{self.base_url}/customer/info",
            params={"end_user_id": user_id},
            headers=self.admin_headers,
            timeout=30,
        )
        if response.status_code == 404:
            return 0.0
        response.raise_for_status()
        return float(response.json().get("spend") or 0.0)

    def wait_for(self, predicate: Any, timeout: float = 45.0) -> bool:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(1.0)
        return False


def _config_yaml(provider_port: int, extra_general: str) -> str:
    model = f"""
  - model_name: {{name}}
    litellm_params:
      model: openai/fake-upstream
      api_base: http://host.docker.internal:{provider_port}/v1
      api_key: sk-upstream-not-real
    model_info:
      input_cost_per_token: {TOKEN_COST}
      output_cost_per_token: {TOKEN_COST}"""
    return (
        "model_list:"
        + model.format(name="fake-chat")
        + model.format(name="fake-restricted")
        + f"""
general_settings:
  master_key: {MASTER_KEY}
  proxy_batch_write_at: 1
{extra_general}
"""
    )


@contextlib.contextmanager
def litellm_stack(version: str, *, extra_general: str = "", extra_env: dict[str, str] | None = None) -> Iterator[LiteLLMStack]:
    """Postgres, LiteLLM and a fake provider, torn down on exit."""
    suffix = secrets.token_hex(4)
    network = f"pipeshub-litellm-test-{suffix}"
    postgres = f"pipeshub-litellm-pg-{suffix}"
    proxy = f"pipeshub-litellm-{suffix}"
    workdir = Path(tempfile.mkdtemp(prefix="litellm-test-"))
    provider = FakeProvider()
    provider.start()
    created: list[str] = []
    try:
        _docker("network", "create", network)
        _docker(
            "run", "-d", "--name", postgres, "--network", network,
            "-e", "POSTGRES_PASSWORD=pw", "-e", "POSTGRES_DB=litellm", POSTGRES_IMAGE,
        )
        created.append(postgres)
        config = workdir / "config.yaml"
        config.write_text(_config_yaml(provider.port, extra_general))
        env = {
            "DATABASE_URL": f"postgresql://postgres:pw@{postgres}:5432/litellm",
            "LITELLM_MASTER_KEY": MASTER_KEY,
            "STORE_PROMPTS_IN_SPEND_LOGS": "false",
            **(extra_env or {}),
        }
        env_args = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
        _docker(
            "run", "-d", "--name", proxy, "--network", network,
            "--add-host", "host.docker.internal:host-gateway",
            "-p", "127.0.0.1::4000",
            "-v", f"{config}:/app/config.yaml:ro",
            *env_args,
            _warmed_image(version),
            "--config", "/app/config.yaml", "--port", "4000",
        )
        created.append(proxy)
        port = _docker("port", proxy, "4000/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
        base_url = f"http://127.0.0.1:{port}"
        _wait_until_ready(base_url, proxy)
        _remember_warmed(version, proxy)
        yield LiteLLMStack(version, base_url, provider, containers=created)
    finally:
        for name in reversed(created):
            _docker("rm", "-f", name, check=False)
        _docker("network", "rm", network, check=False)
        provider.stop()
        shutil.rmtree(workdir, ignore_errors=True)


def _wait_until_ready(base_url: str, container: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"{base_url}/health/readiness", headers={"authorization": f"Bearer {MASTER_KEY}"}, timeout=5
            )
            if response.status_code == 200 and response.json().get("db") == "connected":
                return
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(2.0)
    logs = _docker("logs", "--tail", "60", container, check=False)
    raise RuntimeError(f"LiteLLM did not become ready:\n{logs.stdout}{logs.stderr}")

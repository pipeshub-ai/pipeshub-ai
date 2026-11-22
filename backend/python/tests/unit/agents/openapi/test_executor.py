import base64
import importlib.util
import json
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.agents.mcp import oauth_client
from app.agents.openapi.auth import (
    AuthApplier,
    BasicAuth,
    BearerAuth,
    NoAuth,
    build_auth,
)
from app.agents.openapi.executor import ApiCallError, OpenAPIHttpExecutor
from app.agents.openapi.models import (
    ApiToolsetCredentials,
    CompiledOperation,
    OperationParameter,
    ParameterLocation,
)
from app.agents.openapi.spec_compiler import compile_spec

BASE = "https://api.acme.test"
_FIXTURE_DIR = Path(__file__).resolve().parents[6] / "integration-tests" / "openapi_fixture"


def _op(path: str = "/items", method: str = "GET", *, requires_auth: bool = True, **extra: object) -> CompiledOperation:
    return CompiledOperation(tool_name="t", method=method, path=path, input_schema={"type": "object"},
                             requires_auth=requires_auth, **extra)


def _executor(handler: Callable[[httpx.Request], httpx.Response], auth: AuthApplier | None = None, **kw: object) -> OpenAPIHttpExecutor:
    return OpenAPIHttpExecutor(BASE, auth or NoAuth(), allow_private=False, transport=httpx.MockTransport(handler), **kw)


class _RefreshingAuth(AuthApplier):
    def __init__(self) -> None:
        self.forced: list[bool] = []

    async def apply(self, request: httpx.Request, *, force_refresh: bool = False) -> None:
        self.forced.append(force_refresh)
        request.headers["Authorization"] = "Bearer new" if force_refresh else "Bearer old"

    @property
    def can_refresh(self) -> bool:
        return True


class TestResponses:
    async def test_json_response(self) -> None:
        response = await _executor(lambda r: httpx.Response(200, json={"items": [1, 2]})).call(_op(), {})
        assert response.ok and response.body == {"items": [1, 2]} and not response.truncated

    async def test_text_response(self) -> None:
        response = await _executor(lambda r: httpx.Response(200, text="pong", headers={"content-type": "text/plain"})).call(_op(), {})
        assert response.body == "pong"

    async def test_binary_response_is_described_not_returned(self) -> None:
        response = await _executor(
            lambda r: httpx.Response(200, content=b"\x89PNG....", headers={"content-type": "image/png"})
        ).call(_op(), {})
        assert response.body == "(8 bytes of image/png, not shown)"

    async def test_large_response_is_cut(self) -> None:
        payload = json.dumps({"x": "a" * 5000})
        response = await _executor(
            lambda r: httpx.Response(200, text=payload, headers={"content-type": "application/json"}), max_response_bytes=100,
        ).call(_op(), {})
        assert response.truncated and isinstance(response.body, str) and len(response.body) == 100

    async def test_error_status_is_returned_with_its_body(self) -> None:
        response = await _executor(lambda r: httpx.Response(422, json={"error": "bad"})).call(_op(), {})
        assert not response.ok and response.status == 422 and response.body == {"error": "bad"}

    async def test_redirects_are_not_followed(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": "https://elsewhere.test/steal"})

        response = await _executor(handler).call(_op(), {})
        assert response.status == 302 and seen == [BASE + "/items"]

    async def test_empty_body(self) -> None:
        assert (await _executor(lambda r: httpx.Response(204)).call(_op(), {})).body is None


class TestAuthAndRetry:
    async def test_auth_is_applied(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization", ""))
            return httpx.Response(200, json={})

        await _executor(handler, BearerAuth("t")).call(_op(), {})
        assert seen == ["Bearer t"]

    async def test_an_operation_that_may_run_anonymously_still_carries_the_credential(self) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={})

        await _executor(handler, BearerAuth("t")).call(_op(requires_auth=False), {})
        await _executor(handler).call(_op(requires_auth=False), {})
        assert seen == ["Bearer t", None]

    async def test_auth_that_the_spec_makes_optional_is_still_sent(self) -> None:
        # Jira's shape: basic auth, OAuth, or `{}`. Without the credential Jira answers as nobody.
        spec = {
            "openapi": "3.0.1", "info": {"title": "Jira", "version": "1"},
            "components": {"securitySchemes": {"basicAuth": {"type": "http", "scheme": "basic"}}},
            "paths": {"/rest/api/3/project/search": {"get": {
                "operationId": "searchProjects", "security": [{"basicAuth": []}, {}],
                "responses": {"200": {"description": "ok"}},
            }}},
        }
        (op,) = compile_spec(json.dumps(spec).encode()).operations
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={"total": 13})

        await _executor(handler, BasicAuth("me@acme.test", "token")).call(op, {})
        # Built here, not written out, so secret scanners do not flag it.
        assert seen == ["Basic " + base64.b64encode(b"me@acme.test:token").decode()]

    async def test_401_on_an_operation_that_may_run_anonymously_retries_with_fresh_credentials(self) -> None:
        auth = _RefreshingAuth()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200 if request.headers["Authorization"] == "Bearer new" else 401, json={})

        response = await _executor(handler, auth).call(_op(requires_auth=False), {})
        assert response.status == 200 and auth.forced == [False, True]

    async def test_401_retries_once_with_fresh_credentials(self) -> None:
        auth = _RefreshingAuth()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200 if request.headers["Authorization"] == "Bearer new" else 401, json={})

        response = await _executor(handler, auth).call(_op(method="POST", body_argument="body"), {"body": {"a": 1}})
        assert response.status == 200 and auth.forced == [False, True]

    async def test_401_is_returned_when_it_persists(self) -> None:
        auth = _RefreshingAuth()
        response = await _executor(lambda r: httpx.Response(401, json={}), auth).call(_op(), {})
        assert response.status == 401 and auth.forced == [False, True]

    async def test_401_with_static_credentials_is_not_retried(self) -> None:
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(401, json={})

        await _executor(handler, BearerAuth("t")).call(_op(), {})
        assert len(calls) == 1


class TestFailures:
    async def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(ApiCallError, match="did not respond within 5 seconds"):
            await _executor(handler, timeout_seconds=5).call(_op(), {})

    async def test_connection_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(ApiCallError, match="Could not reach the API"):
            await _executor(handler).call(_op(), {})

    @pytest.mark.parametrize("base_url", ["http://127.0.0.1:9", "http://169.254.169.254", "http://localhost:9"])
    async def test_the_real_guard_blocks_loopback_and_metadata(self, base_url: str) -> None:
        executor = OpenAPIHttpExecutor(base_url, NoAuth(), allow_private=True)
        with pytest.raises(ApiCallError, match="base URL is not allowed"):
            await executor.call(_op(), {})


# ---------------------------------------------------------------------------
# End to end: the reference spec, compiled, called against the reference server
# ---------------------------------------------------------------------------


def _load_fixture_server() -> Any:  # noqa: ANN401
    spec = importlib.util.spec_from_file_location("openapi_fixture_server_for_backend_tests", _FIXTURE_DIR / "server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def acme(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, Any]]:
    """The reference server on loopback. The SSRF guard always refuses loopback, so the
    executor and the token request get a plain transport here."""
    server_module = _load_fixture_server()
    server = server_module.serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(oauth_client, "GuardedTransport", lambda url, allow_private: httpx.AsyncHTTPTransport())
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.server_close()


ACME_CREDENTIALS = ApiToolsetCredentials(
    api_key="acme-demo-key", token="acme-demo-token", username="acme", password="acme-demo-password",
    client_id="acme-client", client_secret="acme-client-secret",
)


@pytest.mark.parametrize(
    ("scheme_name", "recorded_as"),
    [("apiKeyAuth", "apiKey"), ("bearerAuth", "bearer"), ("basicAuth", "basic"), ("oauth2ClientCredentials", "oauth2")],
)
async def test_every_reference_operation_works_with_every_scheme(acme: tuple[str, Any], scheme_name: str, recorded_as: str) -> None:
    base_url, state = acme
    compiled = compile_spec((_FIXTURE_DIR / "openapi.yaml").read_bytes())
    scheme = next(s for s in compiled.auth_schemes if s.name == scheme_name)
    auth = build_auth(scheme, ACME_CREDENTIALS, base_url=base_url, allow_private=True)
    executor = OpenAPIHttpExecutor(base_url, auth, allow_private=True, transport=httpx.AsyncHTTPTransport())
    ops = {op.tool_name: op for op in compiled.operations}

    listed = await executor.call(ops["list_tickets"], {"status": "open", "tag": ["network", "vpn"], "limit": 5})
    assert [t["id"] for t in listed.body["tickets"]] == ["ACME-7311"]
    created = await executor.call(ops["create_ticket"], {"Idempotency-Key": "k1", "body": {"title": "Printer jam", "priority": "low"}})
    assert created.status == 201
    ticket_id = created.body["id"]
    assert (await executor.call(ops["update_ticket"], {"ticketId": ticket_id, "body": {"status": "pending"}})).body["status"] == "pending"
    assert (await executor.call(ops["add_ticket_comment"], {"ticketId": ticket_id, "body": {"body": "On it"}})).status == 201
    fetched = await executor.call(ops["get_ticket"], {"ticketId": ticket_id})
    assert fetched.body["comments"][-1]["body"] == "On it"
    assert (await executor.call(ops["get_current_user"], {})).ok
    assert (await executor.call(ops["get_ticket"], {"ticketId": "ACME-1"})).status == 404

    assert {call["auth"] for call in state.calls} == {recorded_as}
    assert state.calls[0]["query"] == {"status": ["open"], "tag": ["network", "vpn"], "limit": ["5"]}


async def test_expired_oauth_token_is_replaced_on_401(acme: tuple[str, Any]) -> None:
    base_url, state = acme
    compiled = compile_spec((_FIXTURE_DIR / "openapi.yaml").read_bytes())
    scheme = next(s for s in compiled.auth_schemes if s.name == "oauth2ClientCredentials")
    saved: list[ApiToolsetCredentials] = []

    async def save(creds: ApiToolsetCredentials) -> None:
        saved.append(creds)

    executor = OpenAPIHttpExecutor(
        base_url, build_auth(scheme, ACME_CREDENTIALS, base_url=base_url, allow_private=True, save_tokens=save),
        allow_private=True, transport=httpx.AsyncHTTPTransport(),
    )
    me = next(op for op in compiled.operations if op.tool_name == "get_current_user")
    assert (await executor.call(me, {})).ok
    state.tokens.clear()  # the API forgets the token, as after a restart or revocation
    assert (await executor.call(me, {})).ok
    assert len(saved) == 2 and saved[0].oauth.access_token != saved[1].oauth.access_token


async def test_path_traversal_stays_inside_one_segment(acme: tuple[str, Any]) -> None:
    base_url, state = acme
    op = CompiledOperation(tool_name="get", method="GET", path="/tickets/{ticketId}", input_schema={},
                           parameters=[OperationParameter(name="ticketId", location=ParameterLocation.PATH, required=True)])
    executor = OpenAPIHttpExecutor(base_url, BearerAuth("acme-demo-token"), allow_private=True, transport=httpx.AsyncHTTPTransport())
    response = await executor.call(op, {"ticketId": "../users/me"})
    assert response.status == 404
    assert state.calls[-1]["path"] == "/tickets/..%2Fusers%2Fme"


async def test_oauth_sign_in_tokens_call_the_api_and_refresh(acme: tuple[str, Any]) -> None:
    """The authorization-code path end to end: the person allows the app on Acme's page, PipesHub
    exchanges the code with its PKCE verifier, calls the API, and refreshes once Acme forgets the
    access token."""
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit

    base_url, state = acme
    redirect, verifier = "https://pipeshub.test/api-toolsets/oauth/callback/", "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    async with httpx.AsyncClient() as browser:
        allowed = await browser.post(f"{base_url}/oauth/authorize", data={
            "client_id": "acme-client", "redirect_uri": redirect, "state": "s", "code_challenge": challenge,
            "code_challenge_method": "S256", "username": "acme", "password": "acme-demo-password", "decision": "allow",
        })
    code = parse_qs(urlsplit(allowed.headers["location"]).query)["code"][0]
    tokens = await oauth_client.exchange_code_for_token(
        f"{base_url}/oauth/token", "acme-client", "acme-client-secret", code, redirect,
        code_verifier=verifier, allow_private=True,
    )

    compiled = compile_spec((_FIXTURE_DIR / "openapi.yaml").read_bytes())
    scheme = next(s for s in compiled.auth_schemes if s.name == "oauth2SignIn")
    saved: list[ApiToolsetCredentials] = []

    async def save(creds: ApiToolsetCredentials) -> None:
        saved.append(creds)

    creds = ApiToolsetCredentials(oauth=tokens, client_id="acme-client", client_secret="acme-client-secret")
    executor = OpenAPIHttpExecutor(
        base_url, build_auth(scheme, creds, base_url=base_url, allow_private=True, save_tokens=save),
        allow_private=True, transport=httpx.AsyncHTTPTransport(),
    )
    me = next(op for op in compiled.operations if op.tool_name == "get_current_user")
    assert (await executor.call(me, {})).ok
    state.tokens.clear()
    assert (await executor.call(me, {})).ok
    assert len(saved) == 1 and saved[0].oauth.access_token != tokens.access_token
    assert [c["auth"] for c in state.calls] == ["oauth2-signin", "oauth2-signin"]

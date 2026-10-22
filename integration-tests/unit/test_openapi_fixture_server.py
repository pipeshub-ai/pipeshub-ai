"""The openapi-fixture server does what its openapi.yaml says.

The spec is the reference users copy, and the API Toolsets tests trust it, so this
checks both directions: every operation in the spec exists on the server and every
server route is in the spec, and real responses validate against the documented
schemas. Runs the real server in a thread; no stack needed.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from helper.openapi_fixture import (
    DEMO_API_KEY,
    DEMO_BASIC,
    DEMO_BEARER_TOKEN,
    DEMO_CLIENT,
    OpenApiFixture,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

_DIR = Path(__file__).resolve().parents[1] / "openapi_fixture"
_spec = importlib.util.spec_from_file_location("openapi_fixture_server", _DIR / "server.py")
assert _spec and _spec.loader
server_module = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server_module
_spec.loader.exec_module(server_module)

SPEC: dict[str, Any] = yaml.safe_load((_DIR / "openapi.yaml").read_text())
HTTP_METHODS = {"get", "put", "post", "patch", "delete"}
_REGISTRY = Registry().with_resource("urn:spec", Resource.from_contents(SPEC, default_specification=DRAFT202012))
API_KEY = {"X-API-Key": DEMO_API_KEY}


@pytest.fixture
def fixture() -> Iterator[OpenApiFixture]:
    server = server_module.serve("127.0.0.1", 0, public_url="https://acme.example.test/")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield OpenApiFixture(test_url=url, connector_url=url)
    finally:
        server.shutdown()
        server.server_close()


def _operations() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method.upper(), path, op)
        for path, item in SPEC["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS
    ]


def _assert_matches_spec(resp: requests.Response, method: str, path: str) -> None:
    """The status is documented for this operation and the body fits its schema."""
    responses = SPEC["paths"][path][method.lower()]["responses"]
    documented = responses.get(str(resp.status_code))
    assert documented is not None, f"{method} {path} returned undocumented {resp.status_code}"
    if "$ref" in documented:
        documented = SPEC["components"]["responses"][documented["$ref"].rsplit("/", 1)[-1]]
    schema = documented["content"]["application/json"]["schema"]
    if "$ref" in schema:
        schema = {"$ref": "urn:spec" + schema["$ref"]}
    Draft202012Validator(schema, registry=_REGISTRY).validate(resp.json())


def _collect_refs(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from _collect_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _collect_refs(value)


# ---------------------------------------------------------------------------
# The spec itself
# ---------------------------------------------------------------------------


def test_spec_and_server_list_the_same_operations() -> None:
    in_spec = {(method, path) for method, path, _ in _operations()}
    on_server = {(method, path) for method, path, _ in server_module.ROUTES}
    assert in_spec == on_server


def test_the_samples_admins_download_are_this_spec() -> None:
    """The Add API action panel offers this spec as YAML and JSON from the frontend's public files."""
    samples = Path(__file__).resolve().parents[2] / "frontend" / "public" / "samples"
    assert (samples / "api-action-sample.yaml").read_text() == (_DIR / "openapi.yaml").read_text()
    assert json.loads((samples / "api-action-sample.json").read_text()) == SPEC


def test_every_operation_is_usable_as_a_tool() -> None:
    ids = [op.get("operationId") for _, _, op in _operations()]
    assert all(ids), "every operation needs an operationId: it becomes the tool name"
    assert len(ids) == len(set(ids))
    assert all(op.get("summary") for _, _, op in _operations())


def test_every_ref_points_inside_the_file() -> None:
    for ref in _collect_refs(SPEC):
        assert ref.startswith("#/"), ref
        node: Any = SPEC
        for part in ref[2:].split("/"):
            node = node[part]


def test_the_server_serves_its_own_spec_with_its_public_sign_in_address(fixture: OpenApiFixture) -> None:
    served = fixture.spec().decode()
    assert "authorizationUrl: https://acme.example.test/oauth/authorize" in served
    assert served.replace("https://acme.example.test/oauth/authorize", "/oauth/authorize") == (_DIR / "openapi.yaml").read_text()


# ---------------------------------------------------------------------------
# Auth: every scheme in components.securitySchemes is accepted
# ---------------------------------------------------------------------------


def test_requests_without_credentials_are_refused(fixture: OpenApiFixture) -> None:
    resp = requests.get(f"{fixture.test_url}/tickets", timeout=5)
    assert resp.status_code == 401
    _assert_matches_spec(resp, "GET", "/tickets")
    assert fixture.calls() == []


@pytest.mark.parametrize(
    ("kwargs", "scheme"),
    [
        ({"headers": {"X-API-Key": DEMO_API_KEY}}, "apiKey"),
        ({"headers": {"Authorization": f"Bearer {DEMO_BEARER_TOKEN}"}}, "bearer"),
        ({"auth": DEMO_BASIC}, "basic"),
    ],
)
def test_static_credentials_are_accepted(fixture: OpenApiFixture, kwargs: dict[str, Any], scheme: str) -> None:
    resp = requests.get(f"{fixture.test_url}/users/me", timeout=5, **kwargs)
    assert resp.status_code == 200
    _assert_matches_spec(resp, "GET", "/users/me")
    assert fixture.calls()[-1]["auth"] == scheme


def test_wrong_credentials_are_refused(fixture: OpenApiFixture) -> None:
    for kwargs in ({"headers": {"X-API-Key": "nope"}}, {"auth": ("acme", "wrong")}, {"headers": {"Authorization": "Bearer nope"}}):
        assert requests.get(f"{fixture.test_url}/users/me", timeout=5, **kwargs).status_code == 401


def _client_credentials_token(fixture: OpenApiFixture, **kwargs: object) -> requests.Response:
    return requests.post(f"{fixture.test_url}/oauth/token", timeout=5, **kwargs)


def test_oauth_client_credentials_token_is_accepted_until_expired(fixture: OpenApiFixture) -> None:
    token_resp = _client_credentials_token(
        fixture, data={"grant_type": "client_credentials", "client_id": DEMO_CLIENT[0], "client_secret": DEMO_CLIENT[1]}
    )
    assert token_resp.status_code == 200
    token = token_resp.json()
    assert token["token_type"] == "Bearer" and token["expires_in"] > 0
    headers = {"Authorization": f"Bearer {token['access_token']}"}

    assert requests.get(f"{fixture.test_url}/users/me", headers=headers, timeout=5).status_code == 200
    assert fixture.calls()[-1]["auth"] == "oauth2"

    fixture.expire_tokens()
    assert requests.get(f"{fixture.test_url}/users/me", headers=headers, timeout=5).status_code == 401


def test_oauth_client_can_authenticate_with_basic_header(fixture: OpenApiFixture) -> None:
    resp = _client_credentials_token(fixture, data={"grant_type": "client_credentials"}, auth=DEMO_CLIENT)
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("data", "status"),
    [
        ({"grant_type": "client_credentials", "client_id": DEMO_CLIENT[0], "client_secret": "wrong"}, 401),
        ({"grant_type": "password", "client_id": DEMO_CLIENT[0], "client_secret": DEMO_CLIENT[1]}, 400),
    ],
)
def test_oauth_token_errors(fixture: OpenApiFixture, data: dict[str, str], status: int) -> None:
    assert _client_credentials_token(fixture, data=data).status_code == status


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------


def test_list_tickets_filters_and_limits(fixture: OpenApiFixture) -> None:
    base = f"{fixture.test_url}/tickets"

    every = requests.get(base, headers=API_KEY, timeout=5)
    _assert_matches_spec(every, "GET", "/tickets")
    assert every.json()["total"] == len(server_module.SEED_TICKETS)

    mine = requests.get(base, params={"assignee": "noor.rahimi", "status": "open"}, headers=API_KEY, timeout=5).json()
    assert [t["id"] for t in mine["tickets"]] == ["ACME-7311", "ACME-7310"]

    tagged = requests.get(base, params=[("tag", "network"), ("tag", "vpn")], headers=API_KEY, timeout=5).json()
    assert [t["id"] for t in tagged["tickets"]] == ["ACME-7311"]

    limited = requests.get(base, params={"limit": 1}, headers=API_KEY, timeout=5).json()
    assert len(limited["tickets"]) == 1 and limited["total"] == len(server_module.SEED_TICKETS)


@pytest.mark.parametrize("params", [{"status": "lost"}, {"limit": 0}, {"limit": "many"}])
def test_list_tickets_rejects_bad_query(fixture: OpenApiFixture, params: dict[str, Any]) -> None:
    resp = requests.get(f"{fixture.test_url}/tickets", params=params, headers=API_KEY, timeout=5)
    assert resp.status_code == 422
    _assert_matches_spec(resp, "GET", "/tickets")


def test_get_ticket_and_missing_ticket(fixture: OpenApiFixture) -> None:
    found = requests.get(f"{fixture.test_url}/tickets/ACME-7311", headers=API_KEY, timeout=5)
    _assert_matches_spec(found, "GET", "/tickets/{ticketId}")
    assert found.json()["comments"][0]["body"].startswith("Reproduced.")

    missing = requests.get(f"{fixture.test_url}/tickets/ACME-1", headers=API_KEY, timeout=5)
    assert missing.status_code == 404
    _assert_matches_spec(missing, "GET", "/tickets/{ticketId}")


def test_create_ticket_is_idempotent_with_a_key(fixture: OpenApiFixture) -> None:
    url = f"{fixture.test_url}/tickets"
    headers = {**API_KEY, "Idempotency-Key": "run-42"}
    body = {"title": "Printer on floor 3 jams", "priority": "low", "tags": ["hardware"]}

    first = requests.post(url, json=body, headers=headers, timeout=5)
    assert first.status_code == 201
    _assert_matches_spec(first, "POST", "/tickets")
    again = requests.post(url, json=body, headers=headers, timeout=5)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"] == "ACME-7314"
    assert requests.get(url, headers=API_KEY, timeout=5).json()["total"] == len(server_module.SEED_TICKETS) + 1


@pytest.mark.parametrize("body", [{}, {"title": "  "}, {"title": "x", "priority": "asap"}, {"title": "x", "tags": "one"}, ["title"]])
def test_create_ticket_rejects_bad_body(fixture: OpenApiFixture, body: object) -> None:
    resp = requests.post(f"{fixture.test_url}/tickets", json=body, headers=API_KEY, timeout=5)
    assert resp.status_code == 422
    _assert_matches_spec(resp, "POST", "/tickets")


def test_update_ticket(fixture: OpenApiFixture) -> None:
    url = f"{fixture.test_url}/tickets/ACME-7312"
    resp = requests.patch(url, json={"status": "closed", "assignee": None}, headers=API_KEY, timeout=5)
    _assert_matches_spec(resp, "PATCH", "/tickets/{ticketId}")
    assert resp.json()["status"] == "closed" and resp.json()["assignee"] is None

    for bad in ({}, {"status": "gone"}, {"owner": "x"}, {"assignee": 7}):
        rejected = requests.patch(url, json=bad, headers=API_KEY, timeout=5)
        assert rejected.status_code == 422, bad
        _assert_matches_spec(rejected, "PATCH", "/tickets/{ticketId}")


def test_add_comment(fixture: OpenApiFixture) -> None:
    url = f"{fixture.test_url}/tickets/ACME-7313/comments"
    resp = requests.post(url, json={"body": "Lisbon team has access now."}, headers=API_KEY, timeout=5)
    assert resp.status_code == 201
    _assert_matches_spec(resp, "POST", "/tickets/{ticketId}/comments")
    ticket = requests.get(f"{fixture.test_url}/tickets/ACME-7313", headers=API_KEY, timeout=5).json()
    assert ticket["comments"][-1]["body"] == "Lisbon team has access now."


def test_unknown_routes_and_methods(fixture: OpenApiFixture) -> None:
    assert requests.get(f"{fixture.test_url}/projects", headers=API_KEY, timeout=5).status_code == 404
    assert requests.delete(f"{fixture.test_url}/tickets/ACME-7311", headers=API_KEY, timeout=5).status_code == 405


def test_invalid_json_body_is_a_400(fixture: OpenApiFixture) -> None:
    resp = requests.post(
        f"{fixture.test_url}/tickets", data=b"{not json", headers={**API_KEY, "Content-Type": "application/json"}, timeout=5
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Fixture controls
# ---------------------------------------------------------------------------


def test_call_log_records_each_api_call(fixture: OpenApiFixture) -> None:
    requests.get(f"{fixture.test_url}/tickets", params=[("tag", "a"), ("tag", "b")], headers=API_KEY, timeout=5)
    requests.post(f"{fixture.test_url}/tickets/ACME-7311/comments", json={"body": "hi"}, headers=API_KEY, timeout=5)

    calls = fixture.calls()
    assert [(c["method"], c["path"]) for c in calls] == [("GET", "/tickets"), ("POST", "/tickets/ACME-7311/comments")]
    assert calls[0]["query"] == {"tag": ["a", "b"]}
    assert calls[1]["body"] == {"body": "hi"}


def test_reset_restores_seed_data_and_clears_calls(fixture: OpenApiFixture) -> None:
    requests.patch(f"{fixture.test_url}/tickets/ACME-7311", json={"status": "closed"}, headers=API_KEY, timeout=5)
    fixture.reset()

    assert fixture.calls() == []
    ticket = requests.get(f"{fixture.test_url}/tickets/ACME-7311", headers=API_KEY, timeout=5).json()
    assert ticket["status"] == "open"


def test_health(fixture: OpenApiFixture) -> None:
    assert fixture.healthy()


# ---------------------------------------------------------------------------
# No credentials, and the OAuth sign-in (authorization code with PKCE)
# ---------------------------------------------------------------------------


def test_announcements_need_no_credentials(fixture: OpenApiFixture) -> None:
    resp = requests.get(f"{fixture.test_url}/announcements", timeout=5)
    assert resp.status_code == 200
    _assert_matches_spec(resp, "GET", "/announcements")
    assert fixture.calls()[-1]["auth"] == "none"


REDIRECT = "https://pipeshub.test/api-toolsets/oauth/callback/"
VERIFIER = "a-verifier-that-is-long-enough-for-pkce-0123456789"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def _sign_in(fixture: OpenApiFixture, *, password: str = DEMO_BASIC[1], decision: str = "allow") -> requests.Response:
    return requests.post(f"{fixture.test_url}/oauth/authorize", allow_redirects=False, timeout=5, data={
        "client_id": DEMO_CLIENT[0], "redirect_uri": REDIRECT, "state": "s-1", "code_challenge": _challenge(VERIFIER),
        "code_challenge_method": "S256", "username": DEMO_BASIC[0], "password": password, "decision": decision,
    })


def _exchange(fixture: OpenApiFixture, code: str, verifier: str = VERIFIER) -> requests.Response:
    return requests.post(f"{fixture.test_url}/oauth/token", timeout=5, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier,
        "client_id": DEMO_CLIENT[0], "client_secret": DEMO_CLIENT[1],
    })


def _code(resp: requests.Response) -> str:
    assert resp.status_code == 302, resp.text
    query = parse_qs(urlsplit(resp.headers["Location"]).query)
    assert query["state"] == ["s-1"]
    return query["code"][0]


def test_the_sign_in_page_asks_the_person(fixture: OpenApiFixture) -> None:
    resp = requests.get(f"{fixture.test_url}/oauth/authorize", timeout=5, params={
        "response_type": "code", "client_id": DEMO_CLIENT[0], "redirect_uri": REDIRECT, "state": "s-1", "scope": "tickets:read",
    })
    assert resp.status_code == 200 and "text/html" in resp.headers["Content-Type"]
    assert "Sign in to Acme Helpdesk" in resp.text and 'value="s-1"' in resp.text


def test_an_unknown_app_gets_no_sign_in_page(fixture: OpenApiFixture) -> None:
    resp = requests.get(f"{fixture.test_url}/oauth/authorize", timeout=5,
                        params={"response_type": "code", "client_id": "someone-else", "redirect_uri": REDIRECT})
    assert resp.status_code == 400


def test_deny_and_a_wrong_password(fixture: OpenApiFixture) -> None:
    denied = _sign_in(fixture, decision="deny")
    assert denied.status_code == 302 and "error=access_denied" in denied.headers["Location"]
    wrong = _sign_in(fixture, password="nope")
    assert wrong.status_code == 401 and "Wrong username or password" in wrong.text


def test_sign_in_code_exchange_and_refresh(fixture: OpenApiFixture) -> None:
    tokens = _exchange(fixture, _code(_sign_in(fixture)))
    assert tokens.status_code == 200, tokens.text
    issued = tokens.json()
    headers = {"Authorization": f"Bearer {issued['access_token']}"}
    assert requests.get(f"{fixture.test_url}/users/me", headers=headers, timeout=5).status_code == 200
    assert fixture.calls()[-1]["auth"] == "oauth2-signin"

    fixture.expire_tokens()
    assert requests.get(f"{fixture.test_url}/users/me", headers=headers, timeout=5).status_code == 401
    refreshed = requests.post(f"{fixture.test_url}/oauth/token", timeout=5, data={
        "grant_type": "refresh_token", "refresh_token": issued["refresh_token"],
    }, auth=DEMO_CLIENT)
    assert refreshed.status_code == 200
    new_headers = {"Authorization": f"Bearer {refreshed.json()['access_token']}"}
    assert requests.get(f"{fixture.test_url}/users/me", headers=new_headers, timeout=5).status_code == 200


def test_a_code_is_single_use_and_bound_to_its_verifier(fixture: OpenApiFixture) -> None:
    code = _code(_sign_in(fixture))
    assert _exchange(fixture, code, verifier="a-different-verifier-that-is-long-enough-000000").json() == {"error": "invalid_grant"}
    # A failed exchange used the code up too.
    assert _exchange(fixture, code).status_code == 400
    other = _code(_sign_in(fixture))
    assert _exchange(fixture, other).status_code == 200
    assert _exchange(fixture, other).status_code == 400


def test_an_unknown_refresh_token_is_refused(fixture: OpenApiFixture) -> None:
    resp = requests.post(f"{fixture.test_url}/oauth/token", timeout=5, auth=DEMO_CLIENT,
                         data={"grant_type": "refresh_token", "refresh_token": "made-up"})
    assert resp.status_code == 400 and resp.json() == {"error": "invalid_grant"}


def test_the_home_page_lists_the_spec_and_routes(fixture: OpenApiFixture) -> None:
    resp = requests.get(f"{fixture.test_url}/", timeout=5)
    assert resp.status_code == 200 and "text/html" in resp.headers["Content-Type"]
    assert 'href="/openapi.yaml"' in resp.text and "/tickets/{ticketId}" in resp.text

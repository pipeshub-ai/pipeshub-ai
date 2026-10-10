"""Constants and helpers for the strict OpenAPI audit of /api/v1/knowledgeBase."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

import requests
import strict_openapi
from jsonschema import Draft7Validator, FormatChecker

from helper.clients.kb_client import KBClient
from helper.local_auth import obtain_user_session_token
from helper.pipeshub_client import PipeshubClient
from helper.second_user import SecondUser
from helper.web_fixtures import WebFixtures, html_page

KB_BASE = "/api/v1/knowledgeBase"

# Record, knowledge base and record group ids are graph keys (UUIDs). The Node
# validators only require a non-empty string, so there is no "malformed" id the
# gateway rejects by shape: both of these reach the connector service.
MISSING_RECORD_ID = "00000000-0000-4000-8000-000000000000"
MISSING_RECORD_GROUP_ID = "00000000-0000-4000-8000-000000000001"
MALFORMED_ID = "not-a-graph-id"
# Decoded by Express to "a%b": guardPathParams refuses "%" in an id it pastes into a service URL.
UNSAFE_ID = "a%25b"
UNSAFE_ID_MESSAGE = "This address contains an ID that isn't valid"

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}

HTML_REFUSED_MESSAGE = "HTML tags, scripts, and XSS content are not allowed"

OAUTH_CLIENTS_PATH = "/api/v1/oauth-clients"
OAUTH_TOKEN_PATH = "/api/v1/oauth2/token"
# A scope none of the knowledge base routes accept.
UNRELATED_SCOPE = "org:read"

PLATFORM_SETTINGS_PATH = "/api/v1/configurationManager/platform/settings"
SOFT_DELETE_FLAG = "ENABLE_SOFT_DELETE"
SOFT_DELETE_OFF_MESSAGE = "Restoring deleted items is turned off in this workspace"

# MAX_RESTORE_RECORD_IDS in the Node validator and the connector service.
MAX_RESTORE_RECORD_IDS = 100

DEMO_STATUS_FIELDS = frozenset(
    {"hasDemo", "include", "chosen", "realData", "offForEveryone", "demoConnectorIds"}
)

RECORD_VISIBLE_TIMEOUT_SEC = 60.0
RECORD_VISIBLE_INTERVAL_SEC = 2.0

SeedRecord = Callable[..., str]
MakeKb = Callable[..., str]


def unique_name(prefix: str = "spec-audit") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def upload_text_record(
    kb_client: KBClient, kb_id: str, file_name: str | None = None, content: bytes | None = None
) -> str:
    """Upload one small text file to a knowledge base and return its record id."""
    file_name = file_name or f"{unique_name()}.txt"
    uploaded = kb_client.upload_file(
        kb_id, file_name, content or f"Seeded by the knowledge base spec audit: {file_name}".encode()
    )
    record = uploaded["records"][0]
    record_id = record.get("recordId") or record.get("_key") or record.get("id")
    if not record_id:
        raise AssertionError(f"upload answered without a record id: {uploaded}")
    return str(record_id)


def wait_for_record(kb_client: KBClient, record_id: str) -> None:
    """Block until GET /record/:recordId answers 200; the upload stream can finish first."""
    deadline = time.monotonic() + RECORD_VISIBLE_TIMEOUT_SEC
    status = 0
    while time.monotonic() < deadline:
        status = kb_client.get(f"/record/{record_id}").status_code
        if status == 200:
            return
        time.sleep(RECORD_VISIBLE_INTERVAL_SEC)
    raise AssertionError(f"record {record_id} never became readable, last status {status}")


def request_as(
    user: SecondUser, method: str, path: str = "", **kwargs: Any
) -> requests.Response:
    """Call a knowledge base route as the non-admin member; path is relative to the router."""
    kwargs.setdefault("timeout", user.timeout)
    headers = dict(user.headers)
    # requests must write the multipart boundary itself.
    if kwargs.get("files"):
        headers.pop("Content-Type", None)
    return requests.request(
        method, f"{user.base_url}{KB_BASE}{path}", headers=headers, **kwargs
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@contextmanager
def oauth_token_with_scopes(base_url: str, scopes: list[str], timeout: int = 60) -> Iterator[str]:
    """A client-credentials token limited to ``scopes``, from an OAuth app that is deleted on exit.

    The suite's own token carries every scope, and a session JWT is never scope-checked, so this is
    the only caller that can be refused for a missing scope.
    """
    admin = bearer(obtain_user_session_token(base_url, timeout))
    created = requests.post(
        f"{base_url}{OAUTH_CLIENTS_PATH}",
        headers=admin,
        json={
            "name": unique_name("spec-audit-kb-scope"),
            "allowedGrantTypes": ["client_credentials"],
            "allowedScopes": scopes,
        },
        timeout=timeout,
    )
    assert created.status_code == 201, f"creating an OAuth app failed: {created.status_code} {created.text[:300]}"
    app = created.json()["app"]
    try:
        issued = requests.post(
            f"{base_url}{OAUTH_TOKEN_PATH}",
            json={
                "grant_type": "client_credentials",
                "client_id": app["clientId"],
                "client_secret": app["clientSecret"],
            },
            timeout=timeout,
        )
        assert issued.status_code == 200, f"token request failed: {issued.status_code}"
        yield issued.json()["access_token"]
    finally:
        requests.delete(f"{base_url}{OAUTH_CLIENTS_PATH}/{app['id']}", headers=admin, timeout=timeout)


def _write_soft_delete_flag(client: PipeshubClient, value: bool | None) -> bool | None:
    """Set the flag (``None`` removes it) and return what it was.

    Read and written in one go because the POST replaces every platform setting, and other suites
    change other flags while this runs.
    """
    current = client.request("GET", PLATFORM_SETTINGS_PATH)
    assert current.status_code == 200, f"reading platform settings: {current.status_code} {current.text[:300]}"
    settings = current.json()
    flags = dict(settings.get("featureFlags") or {})
    before = flags.get(SOFT_DELETE_FLAG)
    if before is value:
        return before
    if value is None:
        flags.pop(SOFT_DELETE_FLAG, None)
    else:
        flags[SOFT_DELETE_FLAG] = value
    saved = client.request(
        "POST",
        PLATFORM_SETTINGS_PATH,
        json={"fileUploadMaxSizeBytes": settings["fileUploadMaxSizeBytes"], "featureFlags": flags},
    )
    assert saved.status_code == 200, f"saving platform settings: {saved.status_code} {saved.text[:300]}"
    return before


@contextmanager
def soft_delete_set_to(client: PipeshubClient, enabled: bool) -> Iterator[None]:
    """Hold "Move Deleted Records to the Trash" at ``enabled`` for the block, then put it back.

    The flag is org-wide and the connector service reads it on every delete and restore, so keep
    the block short: other suites delete records while it is on.
    """
    before = _write_soft_delete_flag(client, enabled)
    try:
        yield
    finally:
        _write_soft_delete_flag(client, before)


def _write_upload_max_size(client: PipeshubClient, value: int) -> int:
    """Set fileUploadMaxSizeBytes, keeping every flag as it is now, and return what it was."""
    current = client.request("GET", PLATFORM_SETTINGS_PATH)
    assert current.status_code == 200, f"reading platform settings: {current.status_code} {current.text[:300]}"
    settings = current.json()
    before = int(settings["fileUploadMaxSizeBytes"])
    if before != value:
        saved = client.request(
            "POST",
            PLATFORM_SETTINGS_PATH,
            json={"fileUploadMaxSizeBytes": value, "featureFlags": dict(settings.get("featureFlags") or {})},
        )
        assert saved.status_code == 200, f"saving platform settings: {saved.status_code} {saved.text[:300]}"
    return before


@contextmanager
def upload_max_size_set_to(client: PipeshubClient, value: int) -> Iterator[int]:
    """Hold the org's upload size limit at ``value`` for the block and yield the previous one.

    Org-wide: raise it rather than lower it, so other suites' uploads keep working meanwhile.
    """
    before = _write_upload_max_size(client, value)
    try:
        yield before
    finally:
        _write_upload_max_size(client, before)


WEB_PAGE_TITLE = "Spec audit web page"
RECORD_GROUP_VISIBLE_TIMEOUT_SEC = 120.0


@dataclass
class WebRecordGroup:
    connector_id: str
    record_group_id: str
    record_id: str


@contextmanager
def web_record_group(client: PipeshubClient, kb_client: KBClient, scope: str = "team") -> Iterator[WebRecordGroup]:
    """A Web connector synced from one page on the web-fixtures service, so the org has a record group.

    A knowledge base is stored as an app, so a record group only comes from a connector sync. The
    connector, and with it the record group, is deleted on exit.
    """
    fixtures = WebFixtures()
    fixtures.check_available()
    page = f"spec-audit/kb-{uuid.uuid4().hex[:10]}.html"
    fixtures.put(page, html_page(WEB_PAGE_TITLE, "spec audit"), "text/html")
    url = fixtures.url_for_connector(page)
    connector_id = client.create_connector(
        "Web",
        unique_name("spec-audit-web"),
        scope=scope,
        config={"sync": {"url": url, "type": "single", "depth": 1, "max_pages": 1}},
    ).connector_id
    try:
        client.toggle_sync(connector_id, True)
        deadline = time.monotonic() + RECORD_GROUP_VISIBLE_TIMEOUT_SEC
        group_id, record_id = "", ""
        while time.monotonic() < deadline and not record_id:
            listed = kb_client.get(
                "/knowledge-hub/nodes", params={"nodeTypes": "recordGroup", "connectorIds": connector_id}
            )
            assert listed.status_code == 200, listed.text[:300]
            groups = listed.json()["items"]
            if groups:
                group_id = str(groups[0]["id"])
                children = kb_client.get(f"/knowledge-hub/nodes/recordGroup/{group_id}")
                assert children.status_code == 200, children.text[:300]
                record_id = next((str(i["id"]) for i in children.json()["items"] if i["nodeType"] == "record"), "")
            if not record_id:
                time.sleep(RECORD_VISIBLE_INTERVAL_SEC)
        assert record_id, f"the Web connector {connector_id} synced no page within {RECORD_GROUP_VISIBLE_TIMEOUT_SEC}s"
        yield WebRecordGroup(connector_id, group_id, record_id)
    finally:
        client.delete_connector(connector_id)
        fixtures.delete(page)


UPLOAD_EVENT_SCHEMAS = {
    "file:succeeded": "UploadSucceededFileDetail",
    "file:failed": "UploadFailedFileDetail",
    "done": "UploadDoneSummary",
}


def sse_events(text: str) -> list[tuple[str, Any]]:
    """The ``event:``/``data:`` frames of a text/event-stream body, comments skipped."""
    events: list[tuple[str, Any]] = []
    for frame in text.split("\n\n"):
        name, data = None, []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data.append(line[len("data:"):].lstrip())
        if name is not None:
            events.append((name, json.loads("\n".join(data))))
    return events


def upload_event_problems(event: str, data: Any) -> list[str]:
    """What the spec's schema for this upload stream event refuses in ``data``, or leaves out of it.

    The strict gate does not read text/event-stream bodies, so the upload tests check each frame here.
    """
    name = UPLOAD_EVENT_SCHEMAS.get(event)
    if name is None:
        return [f"event {event!r} has no schema in the spec"]
    doc, registry = strict_openapi._spec()
    pointer = f"#/components/schemas/{name}"
    problems = [f"{event}{e}" for e in strict_openapi._schema_errors(registry, pointer, data)]
    strict_openapi._undocumented(doc, data, {"$ref": f"{strict_openapi.SPEC_URI}{pointer}"}, event, problems)
    return problems


def multipart_spec_problems(method: str, route: str, fields: dict[str, Any]) -> list[str]:
    """What the spec's multipart/form-data schema of an operation refuses in ``fields``.

    The strict gate does not read multipart bodies, so this is how a test ties a refusal of a
    multipart field to the spec. Text parts are read as the documented type would read them.
    """
    doc, registry = strict_openapi._spec()
    found = strict_openapi.find_operation(doc, method, route)
    assert found is not None, f"{method} {route} is not in the spec"
    spec_path, operation = found
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    properties = schema.get("properties") or {}
    coerced: dict[str, Any] = {}
    for key, value in fields.items():
        types = set(strict_openapi._as_list((properties.get(key) or {}).get("type")))
        if isinstance(value, str) and types & {"array", "object"}:
            # A structured part travels as JSON text; text that does not parse stays a string.
            try:
                coerced[key] = json.loads(value)
            except ValueError:
                coerced[key] = value
        elif isinstance(value, str):
            coerced[key] = strict_openapi._coerce(value, types)
        else:
            coerced[key] = value
    pointer = (
        f"#/paths/{strict_openapi._escape(spec_path)}/{method.lower()}/requestBody/content/multipart~1form-data/schema"
    )
    return strict_openapi._schema_errors(registry, pointer, coerced)


def path_parameter_spec_problems(method: str, route: str, name: str, value: str) -> list[str]:
    """What the spec's schema of a path parameter refuses in ``value``, formats included.

    The strict gate compares only body and query refusals, so a test ties a path-parameter refusal
    to the spec here.
    """
    doc, _ = strict_openapi._spec()
    found = strict_openapi.find_operation(doc, method, route)
    assert found is not None, f"{method} {route} is not in the spec"
    spec_path, operation = found
    parameters = (doc["paths"][spec_path].get("parameters") or []) + (operation.get("parameters") or [])
    for parameter in parameters:
        if "$ref" in parameter:
            parameter = strict_openapi._deref(doc, parameter["$ref"])
        if parameter.get("in") == "path" and parameter.get("name") == name:
            validator = Draft7Validator(parameter.get("schema") or {}, format_checker=FormatChecker())
            return [error.message[:300] for error in validator.iter_errors(value)]
    raise AssertionError(f"{method} {spec_path} documents no path parameter {name!r}")


def body_spec_refusals(resp: requests.Response, route: str) -> list[str]:
    """What the spec's JSON body schema refuses in the request of ``resp``; fields it does not list are not refusals.

    Inside ``outside_request_contract`` the gate reads only the response; this is how a test proves
    that the spec, like the API, does not refuse a body because it carries unknown fields.
    """
    doc, registry = strict_openapi._spec()
    method = (resp.request.method or "").lower()
    found = strict_openapi.find_operation(doc, method, route)
    assert found is not None, f"{method.upper()} {route} is not in the spec"
    spec_path, operation = found
    body = resp.request.body.encode() if isinstance(resp.request.body, str) else resp.request.body
    refused, _ = strict_openapi._body_problems(
        doc, registry, spec_path, method, operation, resp.request.headers.get("Content-Type", ""), body or b""
    )
    return refused

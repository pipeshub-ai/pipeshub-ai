"""Behaviour tests for the Lumos agent tools.

Each test drives a tool the way the agent does and checks what Lumos would
receive and what the agent is told back. See ``lumos_tool_fakes`` for what is
real and what is faked. Request shapes follow Lumos's published OpenAPI
document (https://api.lumos.com/openapi.json).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from lumos_tool_fakes import (
    API_KEY,
    FakeLumosApi,
    LumosCall,
    build_lumos_tool,
    lumos_error,
    lumos_page,
    result,
    validation_error,
)

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool

if TYPE_CHECKING:
    from app.agents.actions.lumos.lumos import Lumos

PENDING = {
    "errors": "Lumos failures reach the agent as a raw status line and response body",
    "statuses": "several status filters are sent comma-joined, which Lumos rejects",
    "path": "an id containing '/' is not escaped, so the call lands on another endpoint",
    "objects": "access_condition and request_config are declared as text but Lumos needs objects",
    "not_json": "a successful reply that is not JSON is reported as a failure",
    "pages": "a page that is not the last one does not say more results exist",
}


def pending(key: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(strict=True, reason=PENDING[key])


@pytest.fixture
def api() -> FakeLumosApi:
    return FakeLumosApi()


@pytest.fixture
def lumos(api: FakeLumosApi) -> Lumos:
    return build_lumos_tool(api)


def assert_safe_error(payload: dict[str, Any]) -> str:
    """The error is plain text the agent can relay: no secrets, no raw status line or response dump."""
    message = payload["error"]
    assert isinstance(message, str) and message
    for leaked in (API_KEY, "Bearer", "HTTP 4", "HTTP 5", "Traceback", "<html", "{"):
        assert leaked not in message, f"{leaked!r} leaked into: {message}"
    return message


def single_query(call: LumosCall) -> dict[str, str]:
    return {k: v[0] for k, v in call.query.items()}


def as_agent_tool(lumos: Lumos, name: str) -> BoundMethodTool:
    """The tool as the agent executor sees it, with its declared parameter types."""
    method = getattr(lumos, name)
    return BoundMethodTool(method, getattr(method, TOOL_META_ATTR))


# ---------------------------------------------------------------------------
# Reads: the right endpoint, the right filters, the signed-in toolset's key
# ---------------------------------------------------------------------------

READS = [
    ("list_platforms", {"name_search": "sales", "page": 2, "size": 10}, "/apps",
     {"name_search": "sales", "page": "2", "size": "10"}),
    ("get_platform", {"app_id": "app-1"}, "/apps/app-1", {}),
    ("list_users", {"search_term": "ada"}, "/users", {"search_term": "ada", "page": "1", "size": "25"}),
    ("get_user", {"user_id": "u-1"}, "/users/u-1", {}),
    ("get_user_accounts", {"user_id": "u-1", "page": 3}, "/users/u-1/accounts", {"page": "3", "size": "25"}),
    ("get_user_roles", {"user_id": "u-1"}, "/users/u-1/roles", {}),
    ("list_groups", {"name": "eng", "app_id": "app-1"}, "/groups",
     {"name": "eng", "app_id": "app-1", "page": "1", "size": "25"}),
    ("get_group", {"group_id": "g-1"}, "/groups/g-1", {}),
    ("get_group_members", {"group_id": "g-1"}, "/groups/g-1/users", {"page": "1", "size": "25"}),
    ("list_permissions", {"app_id": "app-1", "search_term": "admin"}, "/appstore/requestable_permissions",
     {"app_id": "app-1", "search_term": "admin", "page": "1", "size": "25"}),
    ("list_app_permissions", {"app_id": "app-1"}, "/appstore/apps/app-1/requestable_permissions",
     {"page": "1", "size": "25"}),
    ("list_access_requests", {"user_id": "u-1"}, "/appstore/access_requests",
     {"user_id": "u-1", "page": "1", "size": "25"}),
    ("get_access_request", {"request_id": "r-1"}, "/appstore/access_requests/r-1", {}),
    ("list_access_policies", {"name": "vpn"}, "/access_policies", {"name": "vpn", "page": "1", "size": "25"}),
    ("get_access_policy", {"access_policy_id": "p-1"}, "/access_policies/p-1", {}),
    ("get_requestable_permission", {"permission_id": "perm-1", "include_inherited_configs": True},
     "/appstore/requestable_permissions/perm-1", {"include_inherited_configs": "true"}),
]


class TestReads:
    @pytest.mark.parametrize(("tool", "args", "path", "query"), READS, ids=[r[0] for r in READS])
    async def test_each_read_calls_its_endpoint_with_the_filters_given(self, lumos, api, tool, args, path, query) -> None:
        api.on("GET", path, {"id": "x"})

        ok, data = result(await getattr(lumos, tool)(**args))

        assert ok is True, data
        [call] = api.calls
        assert (call.method, call.path) == ("GET", path)
        assert single_query(call) == query
        assert call.headers["authorization"] == f"Bearer {API_KEY}"
        assert data["data"] == {"id": "x"}

    async def test_a_full_last_page_is_returned_as_is(self, lumos, api) -> None:
        api.on("GET", "/users", lumos_page([{"id": "u-1"}, {"id": "u-2"}], size=25))

        ok, data = result(await lumos.list_users())

        assert ok is True
        assert [u["id"] for u in data["data"]["items"]] == ["u-1", "u-2"]
        assert "next_page" not in data

    @pending("pages")
    async def test_a_page_that_is_not_the_last_says_how_to_get_the_rest(self, lumos, api) -> None:
        items = [{"id": f"u-{i}"} for i in range(25)]
        api.on("GET", "/users", lumos_page(items, page=1, size=25, total=90))

        ok, data = result(await lumos.list_users())

        assert ok is True
        assert data["next_page"] == 2
        assert "90" in data["message"] and "page=2" in data["message"]

    async def test_several_status_filters_are_sent_as_repeated_parameters(self, lumos, api) -> None:
        # Lumos declares ``statuses`` as an exploded array: one ``statuses=`` per value.
        api.on("GET", "/appstore/access_requests", lumos_page([]))

        ok, _ = result(await lumos.list_access_requests(statuses=["PENDING", "APPROVED"]))

        assert ok is True
        assert api.calls[0].query["statuses"] == ["PENDING", "APPROVED"]

    async def test_an_id_containing_a_slash_stays_one_path_segment(self, lumos, api) -> None:
        api.on("GET", "/users/u-1%2Froles", {"id": "u-1/roles"})

        ok, _ = result(await lumos.get_user(user_id="u-1/roles"))

        assert ok is True
        assert [c.path for c in api.calls] == ["/users/u-1%2Froles"]


# ---------------------------------------------------------------------------
# Writes: confirmation gate, request bodies, success only when Lumos agreed
# ---------------------------------------------------------------------------

POLICY_APPS = [{"app_id": "app-1", "requestable_permission_ids": ["perm-1"]}]

WRITES = [
    ("create_access_request", {"app_id": "app-1"}),
    ("cancel_access_request", {"request_id": "r-1"}),
    ("create_access_policy", {"name": "VPN", "business_justification": "remote work", "apps": POLICY_APPS}),
    ("update_access_policy", {"access_policy_id": "p-1", "name": "VPN", "business_justification": "x", "apps": POLICY_APPS}),
    ("delete_access_policy", {"access_policy_id": "p-1"}),
    ("create_requestable_permission", {"app_id": "app-1", "label": "Admin"}),
    ("update_requestable_permission", {"permission_id": "perm-1", "label": "Admin"}),
    ("delete_requestable_permission", {"permission_id": "perm-1"}),
    ("add_user_role", {"user_id": "u-1", "role_name": "Admin"}),
    ("remove_user_role", {"user_id": "u-1", "role_name": "Admin"}),
]


class TestWrites:
    @pytest.mark.parametrize(("tool", "args"), WRITES, ids=[w[0] for w in WRITES])
    async def test_nothing_changes_without_confirmation(self, lumos, api, tool, args) -> None:
        ok, data = result(await getattr(lumos, tool)(**args))

        assert ok is False
        assert data["error"] == "confirmation_required"
        assert "confirm=true" in data["details"]
        assert api.calls == []

    async def test_create_access_request_sends_the_request_body(self, lumos, api) -> None:
        api.on("POST", "/appstore/access_request", httpx.Response(201, json=[{"id": "r-9", "status": "PENDING"}]))

        ok, data = result(await lumos.create_access_request(
            app_id="app-1", target_user_id="u-2", business_justification="on-call",
            expiration_in_seconds=3600, access_length="1 day", requestable_permission_ids=["perm-1"], confirm=True,
        ))

        assert ok is True
        assert api.calls[0].body == {
            "app_id": "app-1", "target_user_id": "u-2", "business_justification": "on-call",
            "expiration_in_seconds": 3600, "access_length": "1 day", "requestable_permission_ids": ["perm-1"],
        }
        assert data["data"] == [{"id": "r-9", "status": "PENDING"}]

    @pytest.mark.xfail(strict=True, reason=(
        "Left alone: create_access_request exposes requester_user_id, which Lumos lets an org-admin API key use "
        "to file a request as somebody else. Removing it changes what the toolset can do."
    ))
    async def test_an_access_request_is_never_filed_as_someone_the_model_names(self, lumos, api) -> None:
        api.on("POST", "/appstore/access_request", httpx.Response(201, json=[{"id": "r-9"}]))

        await lumos.create_access_request(app_id="app-1", requester_user_id="u-ceo", confirm=True)

        assert "requester_user_id" not in (api.calls[0].body if api.calls else {})

    @pytest.mark.parametrize(("tool", "args", "method", "path"), [
        ("cancel_access_request", {"request_id": "r-1", "reason": "dup"}, "DELETE", "/appstore/access_requests/r-1"),
        ("delete_access_policy", {"access_policy_id": "p-1"}, "DELETE", "/access_policies/p-1"),
        ("delete_requestable_permission", {"permission_id": "perm-1"}, "DELETE",
         "/appstore/requestable_permissions/perm-1"),
        ("remove_user_role", {"user_id": "u-1", "role_name": "Admin"}, "DELETE", "/users/u-1/roles/Admin"),
    ])
    async def test_a_no_content_reply_is_a_success(self, lumos, api, tool, args, method, path) -> None:
        api.on(method, path, httpx.Response(204))

        ok, data = result(await getattr(lumos, tool)(**args, confirm=True))

        assert ok is True
        assert data["data"] == {}
        assert [(c.method, c.path) for c in api.calls] == [(method, path)]

    async def test_cancel_sends_the_reason_as_a_query_parameter(self, lumos, api) -> None:
        api.on("DELETE", "/appstore/access_requests/r-1", httpx.Response(204))

        await lumos.cancel_access_request(request_id="r-1", reason="duplicate", confirm=True)

        assert single_query(api.calls[0]) == {"reason": "duplicate"}

    async def test_add_user_role_posts_to_the_user_and_role(self, lumos, api) -> None:
        api.on("POST", "/users/u-1/roles/App%20Admin", httpx.Response(201, json={"ok": True}))

        ok, _ = result(await lumos.add_user_role(user_id="u-1", role_name="App Admin", confirm=True))

        assert ok is True
        assert [(c.method, c.path) for c in api.calls] == [("POST", "/users/u-1/roles/App%20Admin")]

    async def test_a_role_name_containing_a_slash_does_not_reach_another_endpoint(self, lumos, api) -> None:
        api.on("DELETE", "/users/u-1/roles/Read%2FWrite", httpx.Response(204))

        ok, _ = result(await lumos.remove_user_role(user_id="u-1", role_name="Read/Write", confirm=True))

        assert ok is True
        assert [c.path for c in api.calls] == ["/users/u-1/roles/Read%2FWrite"]

    async def test_create_access_policy_sends_the_condition_as_an_object(self, lumos, api) -> None:
        condition = {"equals": {"field": "department", "value": "Engineering"}}
        api.on("POST", "/access_policies", {"id": "p-2"})

        ok, _ = result(await lumos.create_access_policy(
            name="Eng VPN", business_justification="remote work", apps=POLICY_APPS,
            access_condition=condition, confirm=True,
        ))

        assert ok is True
        assert api.calls[0].body == {
            "name": "Eng VPN", "business_justification": "remote work", "apps": POLICY_APPS,
            "access_condition": condition,
        }

    @pending("objects")
    @pytest.mark.parametrize(("tool", "field"), [
        ("create_access_policy", "access_condition"),
        ("update_access_policy", "access_condition"),
        ("create_requestable_permission", "request_config"),
        ("update_requestable_permission", "request_config"),
    ])
    def test_the_model_is_told_to_send_an_object_where_lumos_expects_one(self, lumos, tool, field) -> None:
        schema = as_agent_tool(lumos, tool).to_schema().input_schema
        assert schema["properties"][field]["type"] == "object"

    @pending("objects")
    async def test_a_condition_given_as_json_text_is_sent_as_an_object(self, lumos, api) -> None:
        api.on("PUT", "/access_policies/p-1", {"id": "p-1"})

        ok, _ = result(await lumos.update_access_policy(
            access_policy_id="p-1", name="VPN", business_justification="x", apps=POLICY_APPS,
            access_condition='{"in": {"field": "team", "values": ["sre"]}}', is_everyone_condition=False, confirm=True,
        ))

        assert ok is True
        body = api.calls[0].body
        assert body["access_condition"] == {"in": {"field": "team", "values": ["sre"]}}
        assert body["is_everyone_condition"] is False

    @pending("objects")
    async def test_a_condition_that_is_not_json_is_refused_before_calling_lumos(self, lumos, api) -> None:
        ok, data = result(await lumos.create_access_policy(
            name="VPN", business_justification="x", apps=POLICY_APPS, access_condition="department is eng", confirm=True,
        ))

        assert ok is False
        assert "access_condition" in assert_safe_error(data)
        assert api.calls == []

    async def test_create_requestable_permission_sends_config_as_an_object(self, lumos, api) -> None:
        config = {"allowed_groups": {"type": "ALL_GROUPS"}}
        api.on("POST", "/appstore/requestable_permissions", httpx.Response(201, json={"id": "perm-2"}))

        ok, _ = result(await lumos.create_requestable_permission(
            app_id="app-1", label="Admin", include_inherited_configs=True, request_config=config, confirm=True,
        ))

        assert ok is True
        call = api.calls[0]
        assert single_query(call) == {"include_inherited_configs": "true"}
        assert call.body == {"app_id": "app-1", "label": "Admin", "request_config": config}

    @pending("objects")
    async def test_update_requestable_permission_patches_only_what_was_given(self, lumos, api) -> None:
        api.on("PATCH", "/appstore/requestable_permissions/perm-1", {"id": "perm-1"})

        ok, _ = result(await lumos.update_requestable_permission(
            permission_id="perm-1", label="Admins", request_config='{"request_approval_config": {}}', confirm=True,
        ))

        assert ok is True
        assert api.calls[0].body == {"label": "Admins", "request_config": {"request_approval_config": {}}}

    async def test_a_failed_mutation_is_not_reported_as_done(self, lumos, api) -> None:
        api.on("POST", "/users/u-1/roles/Admin", lumos_error(403, "Forbidden"))

        ok, data = result(await lumos.add_user_role(user_id="u-1", role_name="Admin", confirm=True))

        assert ok is False
        assert "Added" not in data["error"]

    async def test_a_created_item_is_not_reported_as_failed_when_the_reply_is_not_json(self, lumos, api) -> None:
        api.on("POST", "/appstore/access_request", httpx.Response(201, content=b"Created"))

        ok, data = result(await lumos.create_access_request(app_id="app-1", confirm=True))

        assert ok is True
        assert "Created" in data["message"]


# ---------------------------------------------------------------------------
# Failures: plain language, a next step, nothing secret
# ---------------------------------------------------------------------------


class TestFailures:
    async def test_rate_limit_tells_the_agent_how_long_to_wait(self, lumos, api) -> None:
        api.on("GET", "/users", lumos_error(429, "Too Many Requests", {"Retry-After": "30"}))

        ok, data = result(await lumos.list_users())

        assert ok is False
        message = assert_safe_error(data)
        assert "30 seconds" in message and "try again" in message.lower()

    async def test_rate_limit_without_a_wait_still_says_to_wait(self, lumos, api) -> None:
        api.on("GET", "/users", lumos_error(429, "Too Many Requests"))

        _, data = result(await lumos.list_users())

        assert "wait" in assert_safe_error(data).lower()

    @pytest.mark.parametrize(("status", "expected"), [
        (401, "API key"),
        (403, "permission"),
        (404, "could not find"),
        (500, "try again"),
        (503, "try again"),
    ])
    async def test_http_failures_are_explained_with_a_next_step(self, lumos, api, status, expected) -> None:
        api.on("GET", "/users/u-1", lumos_error(status, f"raw detail Bearer {API_KEY}"))

        ok, data = result(await lumos.get_user(user_id="u-1"))

        assert ok is False
        assert expected.lower() in assert_safe_error(data).lower()

    async def test_a_rejected_argument_is_named_so_the_agent_can_fix_it(self, lumos, api) -> None:
        api.on("GET", "/users", validation_error("size", "Input should be less than or equal to 100"))

        ok, data = result(await lumos.list_users(size=500))

        assert ok is False
        message = assert_safe_error(data)
        assert "size" in message and "less than or equal to 100" in message

    async def test_an_html_error_page_is_not_passed_to_the_agent(self, lumos, api) -> None:
        api.on("GET", "/apps", httpx.Response(502, content=b"<html><body>Bad gateway</body></html>"))

        ok, data = result(await lumos.list_platforms())

        assert ok is False
        assert_safe_error(data)

    async def test_an_unreachable_lumos_is_a_plain_failure(self, lumos, api) -> None:
        api.on("GET", "/apps", httpx.ConnectError("[Errno -2] Name or service not known"))

        ok, data = result(await lumos.list_platforms())

        assert ok is False
        message = assert_safe_error(data)
        assert "reach Lumos" in message and "Errno" not in message

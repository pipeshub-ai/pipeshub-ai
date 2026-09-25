"""
Unit tests for app.agents.actions.microsoft.teams.teams

The Teams toolset runs end to end through the real TeamsDataSource and the
real msgraph SDK request builders. Only the Kiota request adapter (the layer
that would send HTTP) is replaced, so every test sees the exact method, path,
query and JSON body Microsoft Graph would receive, and responses are parsed
into real SDK models before the tool serializes them back for the agent.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from kiota_abstractions.request_adapter import RequestAdapter
from kiota_serialization_json.json_parse_node_factory import JsonParseNodeFactory
from kiota_serialization_json.json_serialization_writer_factory import (
    JsonSerializationWriterFactory,
)
from msgraph import GraphServiceClient
from msgraph.generated.models.o_data_errors.o_data_error import ODataError

from app.agents.actions.microsoft.teams.teams import Teams, _build_recurrence_body
from app.sources.client.microsoft.microsoft import MSGraphClient

AUTH_MESSAGE_FRAGMENT = "not authenticated"


def graph_error(status: int, code: str, message: str) -> ODataError:
    """Build the ODataError the real HTTP adapter raises for a Graph 4xx/5xx."""
    raw = json.dumps({"error": {"code": code, "message": message}}).encode()
    error = JsonParseNodeFactory().get_root_parse_node("application/json", raw).get_object_value(ODataError)
    error.response_status_code = status
    return error


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    body: Any
    headers: dict[str, set[str]]


@dataclass
class _Route:
    method: str
    pattern: re.Pattern
    responses: list[object]
    repeat_last: bool = True


@dataclass
class FakeGraphAdapter(RequestAdapter):
    """Stands in for the Kiota HTTP adapter: records requests, replays canned JSON."""

    base_url: str = ""
    requests: list[RecordedRequest] = field(default_factory=list)
    routes: list[_Route] = field(default_factory=list)

    def on(self, method: str, path_regex: str, *responses: object) -> "FakeGraphAdapter":
        self.routes.append(_Route(method, re.compile(rf"^{path_regex}$"), list(responses)))
        return self

    def calls(self, method: str | None = None, path_regex: str | None = None) -> list[RecordedRequest]:
        return [
            r for r in self.requests
            if (method is None or r.method == method)
            and (path_regex is None or re.fullmatch(path_regex, r.path))
        ]

    def writes(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method in {"POST", "PATCH", "PUT", "DELETE"}]

    def get_serialization_writer_factory(self) -> JsonSerializationWriterFactory:
        return JsonSerializationWriterFactory()

    def enable_backing_store(self, backing_store_factory) -> None:
        return None

    async def convert_to_native_async(self, request_info) -> object:
        return request_info

    def _record(self, request_info) -> RecordedRequest:
        parsed = urlparse(request_info.url)
        path = unquote(parsed.path)
        if path.startswith("/v1.0"):
            path = path[len("/v1.0"):]
        # The SDK's UrlReplaceHandler middleware performs this rewrite in production.
        if path == "/users/me-token-to-replace" or path.startswith("/users/me-token-to-replace/"):
            path = "/me" + path[len("/users/me-token-to-replace"):]
        body = json.loads(request_info.content) if request_info.content else None
        recorded = RecordedRequest(
            method=request_info.http_method.value,
            path=path,
            query=parse_qs(parsed.query),
            body=body,
            headers=dict(request_info.headers.get_all()),
        )
        self.requests.append(recorded)
        return recorded

    def _respond(self, request_info) -> object:
        recorded = self._record(request_info)
        for route in self.routes:
            if route.method == recorded.method and route.pattern.match(recorded.path):
                response = route.responses.pop(0) if len(route.responses) > 1 else route.responses[0]
                if isinstance(response, BaseException):
                    raise response
                return response
        raise graph_error(404, "NotFound", f"no fake route for {recorded.method} {recorded.path}")

    async def send_async(self, request_info, parsable_factory, error_map) -> object:
        response = self._respond(request_info)
        if response is None:
            return None
        raw = json.dumps(response).encode()
        node = JsonParseNodeFactory().get_root_parse_node("application/json", raw)
        return node.get_object_value(parsable_factory)

    async def send_collection_async(self, request_info, parsable_factory, error_map) -> object:
        response = self._respond(request_info)
        raw = json.dumps(response).encode()
        node = JsonParseNodeFactory().get_root_parse_node("application/json", raw)
        return node.get_collection_of_object_values(parsable_factory)

    async def send_collection_of_primitive_async(self, request_info, response_type, error_map) -> object:
        return self._respond(request_info)

    async def send_primitive_async(self, request_info, response_type, error_map) -> object:
        return self._respond(request_info)

    async def send_no_response_content_async(self, request_info, error_map) -> None:
        self._respond(request_info)


class _GraphCredentials:
    def __init__(self, graph: GraphServiceClient) -> None:
        self._graph = graph

    def get_ms_graph_service_client(self) -> GraphServiceClient:
        return self._graph


@pytest.fixture
def graph() -> FakeGraphAdapter:
    return FakeGraphAdapter()


@pytest.fixture
def teams(graph: FakeGraphAdapter) -> Teams:
    client = MSGraphClient(_GraphCredentials(GraphServiceClient(request_adapter=graph)))
    toolset = Teams(client)
    assert toolset.client is not None
    return toolset


def ok(result: tuple[bool, str]) -> dict:
    success, payload = result
    assert success is True, f"expected success, got: {payload}"
    return json.loads(payload)


def err(result: tuple[bool, str]) -> str:
    success, payload = result
    assert success is False, f"expected failure, got success: {payload}"
    return json.loads(payload)["error"]


USERS_PATH = r"/users"
SAM_PATEL = {"id": "u-sam", "displayName": "Sam Patel", "mail": "sam@contoso.com", "userPrincipalName": "sam@contoso.com"}
SAMANTHA = {"id": "u-samantha", "displayName": "Samantha Lee", "mail": "samantha@contoso.com", "userPrincipalName": "samantha@contoso.com"}
ME = {"id": "u-me", "displayName": "Me Myself", "mail": "me@contoso.com"}


# ===========================================================================
# Construction and authentication
# ===========================================================================


class TestConstructionAndAuth:
    def test_missing_client_reports_not_authenticated(self) -> None:
        toolset = Teams(None)
        assert toolset.client is None

    def test_state_dict_passed_positionally_is_kept(self) -> None:
        toolset = Teams({"chat": "state"})
        assert toolset.client is None
        assert toolset.state == {"chat": "state"}

    def test_client_without_graph_sdk_leaves_toolset_unauthenticated(self) -> None:
        class _NotGraph:
            def get_client(self) -> "_NotGraph":
                return self

            def get_ms_graph_service_client(self) -> object:
                return object()

        assert Teams(_NotGraph()).client is None

    @pytest.mark.asyncio
    async def test_tool_call_without_client_tells_agent_to_authenticate(self) -> None:
        message = err(await Teams(None).get_teams())
        assert AUTH_MESSAGE_FRAGMENT in message
        assert "Settings > Toolsets" in message

    @pytest.mark.asyncio
    async def test_graph_unauthorized_error_maps_to_authenticate_message(self, teams, graph) -> None:
        graph.on("GET", r"/me/joinedTeams", graph_error(401, "InvalidAuthenticationToken", "Unauthorized"))
        # The datasource turns the SDK exception into an error string; the tool surfaces it.
        assert "Unauthorized" in err(await teams.get_teams())


# ===========================================================================
# Teams
# ===========================================================================


class TestGetTeams:
    @pytest.mark.asyncio
    async def test_lists_joined_teams_with_default_limit_of_20(self, teams, graph) -> None:
        graph.on("GET", r"/me/joinedTeams", {"value": [{"id": f"t{i}", "displayName": f"Team {i}"} for i in range(30)]})
        data = ok(await teams.get_teams())
        assert data["count"] == 20
        assert data["data"]["results"][0] == {"id": "t0", "displayName": "Team 0"}
        assert graph.calls("GET", r"/me/joinedTeams")

    @pytest.mark.asyncio
    async def test_limit_is_capped_at_100(self, teams, graph) -> None:
        graph.on("GET", r"/me/joinedTeams", {"value": [{"id": f"t{i}"} for i in range(150)]})
        assert ok(await teams.get_teams(top=500))["count"] == 100

    @pytest.mark.asyncio
    async def test_api_error_is_returned_not_raised(self, teams, graph) -> None:
        graph.on("GET", r"/me/joinedTeams", graph_error(403, "Forbidden", "Missing Team.ReadBasic.All"))
        assert "Missing Team.ReadBasic.All" in err(await teams.get_teams())


class TestGetTeam:
    @pytest.mark.asyncio
    async def test_gets_team_by_id(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t-1", {"id": "t-1", "displayName": "Eng"})
        assert ok(await teams.get_team("t-1"))["displayName"] == "Eng"
        assert [r.path for r in graph.requests] == ["/teams/t-1"]

    @pytest.mark.asyncio
    async def test_unknown_team_returns_graph_error(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t-x", graph_error(404, "NotFound", "No team found with Group Id t-x"))
        assert "No team found" in err(await teams.get_team("t-x"))


class TestCreateTeam:
    @pytest.mark.asyncio
    async def test_posts_standard_template_and_returns_created_id(self, teams, graph) -> None:
        graph.on("POST", r"/teams", {"id": "t-new", "displayName": "Launch"})
        data = ok(await teams.create_team("Launch", description="Launch team"))
        post = graph.calls("POST", r"/teams")[0]
        assert post.body["displayName"] == "Launch"
        assert post.body["description"] == "Launch team"
        assert post.body["template@odata.bind"].endswith("teamsTemplates('standard')")
        assert data["team_id"] == "t-new"
        assert data["provisioning_status"] == "completed"

    @pytest.mark.asyncio
    async def test_accepted_without_body_polls_joined_teams_for_new_id(self, teams, graph) -> None:
        graph.on("POST", r"/teams", None)
        graph.on("GET", r"/me/joinedTeams", {"value": []}, {"value": [{"id": "t-new", "displayName": " launch "}]})
        with patch("app.agents.actions.microsoft.teams.teams.asyncio.sleep", new=AsyncMock()):
            data = ok(await teams.create_team("Launch"))
        assert data["team_id"] == "t-new"
        assert len(graph.calls("GET", r"/me/joinedTeams")) == 2

    @pytest.mark.asyncio
    async def test_still_provisioning_is_reported_as_accepted_with_next_step(self, teams, graph) -> None:
        graph.on("POST", r"/teams", None)
        graph.on("GET", r"/me/joinedTeams", {"value": []})
        with patch("app.agents.actions.microsoft.teams.teams.asyncio.sleep", new=AsyncMock()) as sleep:
            data = ok(await teams.create_team("Launch"))
        assert data["provisioning_status"] == "accepted"
        assert "get_teams" in data["next_step"]
        assert len(graph.calls("GET", r"/me/joinedTeams")) == 5
        assert sleep.await_count == 4

    @pytest.mark.asyncio
    async def test_api_error_is_returned(self, teams, graph) -> None:
        graph.on("POST", r"/teams", graph_error(400, "BadRequest", "Display name is too long"))
        assert "Display name is too long" in err(await teams.create_team("x" * 300))


# ===========================================================================
# Members
# ===========================================================================


CHANNELS = {
    "value": [
        {"id": "c-std", "displayName": "General", "membershipType": "standard"},
        {"id": "c-priv", "displayName": "Leads", "membershipType": "private"},
        {"id": "c-shared", "displayName": "Partners", "membershipType": "shared"},
    ]
}


class TestGetMembers:
    @pytest.mark.asyncio
    async def test_team_members_without_channel(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/members", {"value": [{"id": "m1", "displayName": "Ann"}]})
        data = ok(await teams.get_members("t1"))
        assert data["members"][0]["id"] == "m1"
        assert "channel_id" not in data

    @pytest.mark.asyncio
    async def test_standard_channel_uses_team_membership(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        graph.on("GET", r"/teams/t1/members", {"value": [{"id": "m1"}]})
        data = ok(await teams.get_members("t1", channel_id="c-std"))
        assert data["membership_scope"] == "team"
        assert data["membership_type"] == "standard"
        assert not graph.calls("GET", r"/teams/t1/channels/c-std/members")

    @pytest.mark.asyncio
    async def test_private_channel_lists_channel_members(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        graph.on("GET", r"/teams/t1/channels/c-priv/members", {"value": [{"id": "m9"}]})
        data = ok(await teams.get_members("t1", channel_id="c-priv"))
        assert data["membership_scope"] == "channel"
        assert data["members"] == [{"id": "m9"}]

    @pytest.mark.asyncio
    async def test_unknown_channel_is_a_clear_error(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        assert "was not found in team" in err(await teams.get_members("t1", channel_id="nope"))

    @pytest.mark.asyncio
    async def test_limit_is_capped_at_500(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/members", {"value": [{"id": f"m{i}"} for i in range(600)]})
        assert ok(await teams.get_members("t1", top=10_000))["count"] == 500


class TestAddMember:
    @pytest.mark.asyncio
    async def test_adds_member_to_team_with_graph_binding(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/members", {"id": "mem-1"})
        data = ok(await teams.add_member("t1", "u-1"))
        post = graph.calls("POST")[0]
        assert post.body["roles"] == []
        assert post.body["user@odata.bind"] == "https://graph.microsoft.com/v1.0/users('u-1')"
        assert post.body["@odata.type"] == "#microsoft.graph.aadUserConversationMember"
        assert data["user_id"] == "u-1"

    @pytest.mark.asyncio
    async def test_owner_role_and_quote_in_user_id_are_encoded(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/members", {"id": "mem-1"})
        ok(await teams.add_member("t1", "o'neil@contoso.com", role=" Owner "))
        post = graph.calls("POST")[0]
        assert post.body["roles"] == ["owner"]
        assert post.body["user@odata.bind"].endswith("users('o''neil@contoso.com')")

    @pytest.mark.asyncio
    async def test_private_channel_adds_directly_to_channel(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        graph.on("POST", r"/teams/t1/channels/c-priv/members", {"id": "mem-2"})
        data = ok(await teams.add_member("t1", "u-1", channel_id="c-priv"))
        assert data["message"] == "User added to private channel"
        assert [w.path for w in graph.writes()] == ["/teams/t1/channels/c-priv/members"]

    @pytest.mark.asyncio
    async def test_standard_channel_adds_to_team(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        graph.on("POST", r"/teams/t1/members", {"id": "mem-3"})
        ok(await teams.add_member("t1", "u-1", channel_id="c-std"))
        assert [w.path for w in graph.writes()] == ["/teams/t1/members"]

    @pytest.mark.asyncio
    async def test_shared_channel_is_refused_without_writing(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        assert "Unsupported channel membership type: shared" in err(
            await teams.add_member("t1", "u-1", channel_id="c-shared")
        )
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_unknown_channel_is_refused_without_writing(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        assert "was not found" in err(await teams.add_member("t1", "u-1", channel_id="missing"))
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_channel_lookup_failure_is_refused_without_writing(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", graph_error(403, "Forbidden", "No access to team"))
        assert "No access to team" in err(await teams.add_member("t1", "u-1", channel_id="c-priv"))
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_api_error_on_add_is_returned(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/members", graph_error(400, "BadRequest", "User is already a member"))
        assert "already a member" in err(await teams.add_member("t1", "u-1"))


# ===========================================================================
# Channels
# ===========================================================================


class TestChannels:
    @pytest.mark.asyncio
    async def test_get_channels_caps_limit_at_200(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", {"value": [{"id": f"c{i}"} for i in range(250)]})
        assert len(ok(await teams.get_channels("t1", top=999))["data"]["results"]) == 200

    @pytest.mark.asyncio
    async def test_create_channel_posts_name_type_and_description(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels", {"id": "c-new", "displayName": "Ops"})
        data = ok(await teams.create_channel("t1", "Ops", description="On-call", channel_type=" Private "))
        post = graph.calls("POST", r"/teams/t1/channels")[0]
        assert post.body == {"displayName": "Ops", "description": "On-call", "membershipType": "private"}
        assert data["channel_id"] == "c-new"

    @pytest.mark.asyncio
    async def test_create_channel_unknown_type_falls_back_to_standard(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels", {"id": "c-new"})
        ok(await teams.create_channel("t1", "Ops", channel_type="bogus"))
        assert graph.calls("POST")[0].body["membershipType"] == "standard"

    @pytest.mark.asyncio
    async def test_update_channel_without_fields_makes_no_request(self, teams, graph) -> None:
        assert err(await teams.update_channel("t1", "c1")) == "No fields provided to update"
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_update_channel_patches_only_given_fields(self, teams, graph) -> None:
        graph.on("PATCH", r"/teams/t1/channels/c1", None)
        data = ok(await teams.update_channel("t1", "c1", display_name="Renamed"))
        patch_call = graph.calls("PATCH")[0]
        assert patch_call.body == {"displayName": "Renamed"}
        assert data["channel_id"] == "c1"

    @pytest.mark.asyncio
    async def test_update_channel_can_clear_description(self, teams, graph) -> None:
        graph.on("PATCH", r"/teams/t1/channels/c1", None)
        ok(await teams.update_channel("t1", "c1", description=""))
        assert graph.calls("PATCH")[0].body == {"description": ""}

    @pytest.mark.asyncio
    async def test_update_channel_api_error_is_returned(self, teams, graph) -> None:
        graph.on("PATCH", r"/teams/t1/channels/c1", graph_error(403, "Forbidden", "Only owners can rename"))
        assert "Only owners can rename" in err(await teams.update_channel("t1", "c1", display_name="x"))

    @pytest.mark.asyncio
    async def test_user_channels_scoped_to_team_tags_team_id(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        data = ok(await teams.get_user_channels(team_id="t1", top=2))
        assert data["count"] == 2
        assert all(c["team_id"] == "t1" for c in data["channels"])

    @pytest.mark.asyncio
    async def test_user_channels_across_joined_teams_skips_failing_team(self, teams, graph) -> None:
        graph.on("GET", r"/me/joinedTeams", {"value": [{"id": "t1"}, {"id": "t2"}]})
        graph.on("GET", r"/teams/t1/channels", {"value": [{"id": "c1", "displayName": "General"}]})
        graph.on("GET", r"/teams/t2/channels", graph_error(403, "Forbidden", "nope"))
        data = ok(await teams.get_user_channels())
        assert [(c["id"], c["team_id"]) for c in data["channels"]] == [("c1", "t1")]


# ===========================================================================
# Channel messages
# ===========================================================================


class TestChannelMessages:
    @pytest.mark.asyncio
    async def test_send_channel_message_posts_body_content(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels/c1/messages", {"id": "msg-1"})
        data = ok(await teams.send_channel_message("t1", "c1", "Deploy done"))
        assert graph.calls("POST")[0].body == {"body": {"content": "Deploy done"}}
        assert data["result"]["id"] == "msg-1"

    @pytest.mark.asyncio
    async def test_send_channel_message_api_error_is_returned(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels/c1/messages", graph_error(403, "Forbidden", "Missing ChannelMessage.Send"))
        assert "Missing ChannelMessage.Send" in err(await teams.send_channel_message("t1", "c1", "hi"))

    @pytest.mark.asyncio
    async def test_reply_posts_to_thread_replies(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels/c1/messages/m1/replies", {"id": "r1"})
        data = ok(await teams.reply_to_message("t1", "c1", "m1", "Thanks"))
        assert graph.calls("POST")[0].body == {"body": {"content": "Thanks"}}
        assert data["parent_message_id"] == "m1"

    @pytest.mark.asyncio
    async def test_get_channel_messages_caps_limit_at_100(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels/c1/messages", {"value": [{"id": f"m{i}"} for i in range(120)]})
        data = ok(await teams.get_channel_messages("t1", "c1", top=1000))
        assert data["count"] == 100

    @pytest.mark.asyncio
    async def test_get_thread_replies(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels/c1/messages/m1/replies", {"value": [{"id": "r1"}, {"id": "r2"}]})
        data = ok(await teams.get_thread_replies("t1", "c1", "m1", top=1))
        assert data["replies"] == [{"id": "r1"}]

    @pytest.mark.asyncio
    async def test_permalink_comes_from_web_url(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels/c1/messages/m1", {"id": "m1", "webUrl": "https://teams.microsoft.com/l/message/m1"})
        assert ok(await teams.get_message_permalink("t1", "c1", "m1"))["permalink"].endswith("/m1")

    @pytest.mark.asyncio
    async def test_search_messages_in_one_channel_is_case_insensitive(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels/c1/messages", {"value": [
            {"id": "m1", "body": {"content": "Release is BLOCKED on QA"}},
            {"id": "m2", "body": {"content": "lunch?"}},
        ]})
        data = ok(await teams.search_messages("blocked", team_id="t1", channel_id="c1"))
        assert [m["id"] for m in data["results"]] == ["m1"]
        assert data["results"][0]["channel_id"] == "c1"

    @pytest.mark.asyncio
    async def test_search_messages_scans_every_channel_of_team(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", {"value": [{"id": "c1"}, {"id": "c2"}]})
        graph.on("GET", r"/teams/t1/channels/c1/messages", {"value": [{"id": "m1", "body": {"content": "incident"}}]})
        graph.on("GET", r"/teams/t1/channels/c2/messages", {"value": [{"id": "m2", "body": {"content": "Incident review"}}]})
        data = ok(await teams.search_messages("incident", team_id="t1"))
        assert sorted(m["id"] for m in data["results"]) == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_search_messages_blank_query_is_refused(self, teams, graph) -> None:
        assert err(await teams.search_messages("   ", team_id="t1", channel_id="c1")) == "query is required"
        assert graph.requests == []


class TestSendToMultipleChannels:
    @pytest.mark.asyncio
    async def test_sends_to_each_channel(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels/c\d/messages", {"id": "msg"})
        data = ok(await teams.send_message_to_multiple_channels("t1", ["c1", "c2"], "Heads up"))
        assert [w.path for w in graph.writes()] == ["/teams/t1/channels/c1/messages", "/teams/t1/channels/c2/messages"]
        assert data["result"]["count"] == 2

    @pytest.mark.asyncio
    async def test_partial_failure_is_reported_per_channel(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels/c1/messages", {"id": "msg"})
        graph.on("POST", r"/teams/t1/channels/c2/messages", graph_error(404, "NotFound", "Channel not found"))
        success, payload = await teams.send_message_to_multiple_channels("t1", ["c1", "c2"], "Heads up")
        data = json.loads(payload)
        assert success is False
        assert data["error"] == "One or more channel sends failed"
        by_channel = {r["channel_id"]: r for r in data["result"]["results"]}
        assert by_channel["c1"]["success"] is True
        assert "Channel not found" in by_channel["c2"]["error"]

    @pytest.mark.asyncio
    async def test_json_string_channel_list_is_parsed(self, teams, graph) -> None:
        # The tool schema declares channel_ids as a string holding a JSON array.
        graph.on("POST", r"/teams/t1/channels/c\d/messages", {"id": "msg"})
        ok(await teams.send_message_to_multiple_channels("t1", '["c1", "c2"]', "Heads up"))
        assert [w.path for w in graph.writes()] == ["/teams/t1/channels/c1/messages", "/teams/t1/channels/c2/messages"]

    @pytest.mark.asyncio
    async def test_empty_channel_list_is_refused_not_reported_as_sent(self, teams, graph) -> None:
        message = err(await teams.send_message_to_multiple_channels("t1", [], "Heads up"))
        assert "channel_ids" in message
        assert graph.requests == []


class TestReactions:
    @pytest.mark.asyncio
    async def test_add_reaction_sends_emoji_for_friendly_name(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels/c1/messages/m1/setReaction", None)
        data = ok(await teams.add_reaction("t1", "c1", "m1", " Like "))
        assert graph.calls("POST")[0].body == {"reactionType": "👍"}
        assert data["reaction_type"] == "like"

    @pytest.mark.asyncio
    async def test_add_reaction_blank_is_refused(self, teams, graph) -> None:
        assert err(await teams.add_reaction("t1", "c1", "m1", "  ")) == "reaction_type is required"
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_remove_reaction_calls_unset_reaction(self, teams, graph) -> None:
        graph.on("POST", r"/teams/t1/channels/c1/messages/m1/unsetReaction", None)
        ok(await teams.remove_reaction("t1", "c1", "m1", "heart"))
        assert graph.calls("POST")[0].body == {"reactionType": "❤️"}

    @pytest.mark.asyncio
    async def test_get_reactions(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels/c1/messages/m1", {
            "id": "m1",
            "reactions": [{"reactionType": "👍", "user": {"user": {"id": "u1"}}}],
        })
        data = ok(await teams.get_reactions("t1", "c1", "m1"))
        assert data["count"] == 1


class TestUpdateMessage:
    @pytest.mark.asyncio
    async def test_team_without_channel_is_refused(self, teams, graph) -> None:
        assert "Provide both team_id and channel_id" in err(await teams.update_message("m1", "new", team_id="t1"))
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_channel_message_is_patched(self, teams, graph) -> None:
        graph.on("PATCH", r"/teams/t1/channels/c1/messages/m1", None)
        data = ok(await teams.update_message("m1", "fixed typo", team_id="t1", channel_id="c1"))
        assert graph.calls("PATCH")[0].body == {"body": {"content": "fixed typo"}}
        assert data["team_id"] == "t1"

    @pytest.mark.asyncio
    async def test_chat_message_is_patched(self, teams, graph) -> None:
        graph.on("PATCH", r"/chats/chat-1/messages/m1", None)
        data = ok(await teams.update_message("m1", "fixed", chat_id="chat-1"))
        assert data["chat_id"] == "chat-1"

    @pytest.mark.asyncio
    async def test_no_location_finds_chat_holding_message(self, teams, graph) -> None:
        graph.on("GET", r"/me/chats", {"value": [{"id": "chat-a"}, {"id": "chat-b"}]})
        graph.on("GET", r"/chats/chat-a/messages", {"value": [{"id": "other"}]})
        graph.on("GET", r"/chats/chat-b/messages", {"value": [{"id": "m1"}]})
        graph.on("PATCH", r"/chats/chat-b/messages/m1", None)
        assert ok(await teams.update_message("m1", "fixed"))["result"]["chat_id"] == "chat-b"
        assert [w.path for w in graph.writes()] == ["/chats/chat-b/messages/m1"]

    @pytest.mark.asyncio
    async def test_no_location_and_message_not_found_is_refused(self, teams, graph) -> None:
        graph.on("GET", r"/me/chats", {"value": [{"id": "chat-a"}]})
        graph.on("GET", r"/chats/chat-a/messages", {"value": []})
        assert "Unable to resolve chat_id" in err(await teams.update_message("m1", "fixed"))
        assert graph.writes() == []


# ===========================================================================
# Chats
# ===========================================================================


class TestChats:
    @pytest.mark.asyncio
    async def test_create_group_chat_posts_members_and_topic(self, teams, graph) -> None:
        graph.on("POST", r"/chats", {"id": "chat-new", "chatType": "group"})
        data = ok(await teams.create_chat("group", ["u-me", " u-2 "], topic="Launch"))
        post = graph.calls("POST", r"/chats")[0]
        assert post.body["chatType"] == "group"
        assert post.body["topic"] == "Launch"
        bindings = [m["user@odata.bind"] for m in post.body["members"]]
        assert bindings == [
            "https://graph.microsoft.com/v1.0/users('u-me')",
            "https://graph.microsoft.com/v1.0/users('u-2')",
        ]
        assert all(m["roles"] == ["owner"] for m in post.body["members"])
        assert data["chat_id"] == "chat-new"

    @pytest.mark.asyncio
    async def test_one_on_one_chat_drops_topic(self, teams, graph) -> None:
        graph.on("POST", r"/chats", {"id": "chat-new"})
        ok(await teams.create_chat("oneOnOne", ["u-me", "u-2"], topic="ignored"))
        body = graph.calls("POST", r"/chats")[0].body
        assert body["chatType"] == "oneOnOne"
        assert "topic" not in body

    @pytest.mark.asyncio
    async def test_json_string_member_list_is_parsed(self, teams, graph) -> None:
        graph.on("POST", r"/chats", {"id": "chat-new"})
        ok(await teams.create_chat("group", '["u-me", "u-2"]'))
        assert len(graph.calls("POST", r"/chats")[0].body["members"]) == 2

    @pytest.mark.asyncio
    async def test_no_members_is_refused_without_writing(self, teams, graph) -> None:
        assert "member_user_ids" in err(await teams.create_chat("group", ["  "]))
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_get_chat(self, teams, graph) -> None:
        graph.on("GET", r"/me/chats/chat-1", {"id": "chat-1", "topic": "Launch"})
        assert ok(await teams.get_chat("chat-1"))["topic"] == "Launch"


# ===========================================================================
# Users
# ===========================================================================


def _users_page(users: list[dict], next_link: str | None = None) -> dict:
    page: dict[str, Any] = {"value": users}
    if next_link:
        page["@odata.nextLink"] = next_link
    return page


class TestGetUserInfo:
    @pytest.mark.asyncio
    async def test_email_resolves_directly_and_returns_profile(self, teams, graph) -> None:
        graph.on("GET", r"/users/sam@contoso.com", {**SAM_PATEL, "jobTitle": "SRE"})
        graph.on("GET", r"/users/u-sam", {**SAM_PATEL, "jobTitle": "SRE"})
        data = ok(await teams.get_user_info("sam@contoso.com"))
        assert data["id"] == "u-sam"
        assert data["job_title"] == "SRE"
        assert data["data"]["results"][0]["mail"] == "sam@contoso.com"
        assert "$select" in graph.requests[0].query

    @pytest.mark.asyncio
    async def test_ambiguous_name_across_pages_lists_every_match(self, teams, graph) -> None:
        graph.on("GET", r"/users/Sam", graph_error(404, "Request_ResourceNotFound", "Resource 'Sam' does not exist"))
        graph.on(
            "GET", r"/users",
            _users_page([SAM_PATEL, ME], next_link="https://graph.microsoft.com/v1.0/users?$skiptoken=p2"),
            _users_page([SAMANTHA]),
        )
        message = err(await teams.get_user_info("Sam"))
        assert "Multiple users found matching 'Sam'" in message
        assert "[ID: u-sam]" in message and "[ID: u-samantha]" in message
        assert graph.calls("GET", r"/users")[1].query["$skiptoken"] == ["p2"]

    @pytest.mark.asyncio
    async def test_exact_display_name_beats_partial_matches(self, teams, graph) -> None:
        graph.on("GET", r"/users/Sam Patel", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([SAMANTHA, SAM_PATEL]))
        graph.on("GET", r"/users/u-sam", SAM_PATEL)
        assert ok(await teams.get_user_info("Sam Patel"))["id"] == "u-sam"

    @pytest.mark.asyncio
    async def test_pagination_stops_when_next_link_repeats(self, teams, graph) -> None:
        loop_link = "https://graph.microsoft.com/v1.0/users?$skiptoken=same"
        graph.on("GET", r"/users/Nobody", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([ME], next_link=loop_link))
        err(await teams.get_user_info("Nobody"))
        assert len(graph.calls("GET", r"/users")) == 2

    @pytest.mark.asyncio
    async def test_name_containing_a_directory_name_is_not_that_person(self, teams, graph) -> None:
        graph.on("GET", r"/users/Joanna", graph_error(404, "Request_ResourceNotFound", "Resource 'Joanna' does not exist"))
        graph.on("GET", r"/users", _users_page([{"id": "u-ann", "displayName": "Ann", "mail": "ann@contoso.com"}]))
        assert "does not exist" in err(await teams.get_user_info("Joanna"))
        assert not graph.calls("GET", r"/users/u-ann")

    @pytest.mark.asyncio
    async def test_unknown_user_returns_graph_error(self, teams, graph) -> None:
        graph.on("GET", r"/users/.*", graph_error(404, "Request_ResourceNotFound", "Resource 'ghost' does not exist"))
        graph.on("GET", r"/users", _users_page([ME]))
        assert "does not exist" in err(await teams.get_user_info("ghost"))


class TestGetUsersList:
    @pytest.mark.asyncio
    async def test_limit_reads_one_page_and_slices(self, teams, graph) -> None:
        graph.on("GET", r"/users", _users_page([SAM_PATEL, SAMANTHA, ME], next_link="https://graph.microsoft.com/v1.0/users?$skiptoken=p2"))
        data = ok(await teams.get_users_list(limit=2))
        assert data["count"] == 2
        assert len(graph.requests) == 1
        assert graph.requests[0].query["$top"] == ["100"]

    @pytest.mark.asyncio
    async def test_without_limit_follows_next_links(self, teams, graph) -> None:
        graph.on(
            "GET", r"/users",
            _users_page([SAM_PATEL], next_link="https://graph.microsoft.com/v1.0/users?$skiptoken=p2"),
            _users_page([SAMANTHA]),
        )
        data = ok(await teams.get_users_list())
        assert [u["id"] for u in data["members"]] == ["u-sam", "u-samantha"]

    @pytest.mark.asyncio
    async def test_first_page_failure_is_an_error(self, teams, graph) -> None:
        graph.on("GET", r"/users", graph_error(403, "Authorization_RequestDenied", "Insufficient privileges"))
        assert "Insufficient privileges" in err(await teams.get_users_list())


class TestSendUserMessage:
    @pytest.mark.asyncio
    async def test_existing_one_on_one_chat_is_reused(self, teams, graph) -> None:
        graph.on("GET", r"/users/(sam@contoso.com|u-sam)", SAM_PATEL)
        graph.on("GET", r"/me/chats", {"value": [{"id": "chat-g", "chatType": "group"}, {"id": "chat-1", "chatType": "oneOnOne"}]})
        graph.on("GET", r"/chats/chat-1/members", {"value": [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "userId": "u-sam"}]})
        graph.on("POST", r"/chats/chat-1/messages", {"id": "msg-1"})
        ok(await teams.send_user_message("sam@contoso.com", "Standup moved"))
        assert [w.path for w in graph.writes()] == ["/chats/chat-1/messages"]
        assert graph.writes()[0].body == {"body": {"content": "Standup moved"}}

    @pytest.mark.asyncio
    async def test_new_chat_is_created_when_none_exists(self, teams, graph) -> None:
        graph.on("GET", r"/users/(sam@contoso.com|u-sam)", SAM_PATEL)
        graph.on("GET", r"/me/chats", {"value": []})
        graph.on("GET", r"/me", ME)
        graph.on("POST", r"/chats", {"id": "chat-new"})
        graph.on("POST", r"/chats/chat-new/messages", {"id": "msg-1"})
        ok(await teams.send_user_message("sam@contoso.com", "hi"))
        create = graph.calls("POST", r"/chats")[0]
        assert [m["user@odata.bind"] for m in create.body["members"]] == [
            "https://graph.microsoft.com/v1.0/users('u-me')",
            "https://graph.microsoft.com/v1.0/users('u-sam')",
        ]

    @pytest.mark.asyncio
    async def test_exact_name_reaches_that_person_not_a_longer_match(self, teams, graph) -> None:
        just_sam = {"id": "u-just-sam", "displayName": "Sam", "mail": "sam.k@contoso.com"}
        graph.on("GET", r"/users/Sam", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([SAMANTHA, just_sam]))
        graph.on("GET", r"/users/u-just-sam", just_sam)
        graph.on("GET", r"/me/chats", {"value": [{"id": "chat-a", "chatType": "oneOnOne"}, {"id": "chat-b", "chatType": "oneOnOne"}]})
        graph.on("GET", r"/chats/chat-a/members", {"value": [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "userId": "u-samantha"}]})
        graph.on("GET", r"/chats/chat-b/members", {"value": [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "userId": "u-just-sam"}]})
        graph.on("POST", r"/chats/chat-[ab]/messages", {"id": "msg-1"})
        ok(await teams.send_user_message("Sam", "Your review is due"))
        assert [w.path for w in graph.writes()] == ["/chats/chat-b/messages"]

    @pytest.mark.asyncio
    async def test_person_on_second_directory_page_gets_the_message(self, teams, graph) -> None:
        zoe = {"id": "u-zoe", "displayName": "Zoe Park", "mail": "zoe@contoso.com", "userType": "Member"}
        first_page = _users_page([ME, SAMANTHA], next_link="https://graph.microsoft.com/v1.0/users?$skiptoken=p2")
        graph.on("GET", r"/users/Zoe Park", graph_error(404, "Request_ResourceNotFound", "not found"))
        # Any later listing gets the first page again, as an unpaged GET /users would.
        graph.on("GET", r"/users", first_page, _users_page([zoe]), first_page)
        graph.on("GET", r"/users/u-zoe", zoe)
        graph.on("GET", r"/me/chats", {"value": [{"id": "chat-z", "chatType": "oneOnOne"}]})
        graph.on("GET", r"/chats/chat-z/members", {"value": [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "userId": "u-zoe"}]})
        graph.on("POST", r"/chats/chat-z/messages", {"id": "msg-1"})
        ok(await teams.send_user_message("Zoe Park", "Welcome aboard"))
        assert [w.path for w in graph.writes()] == ["/chats/chat-z/messages"]
        assert len(graph.calls("GET", r"/users")) == 2

    @pytest.mark.asyncio
    async def test_ambiguous_name_sends_nothing(self, teams, graph) -> None:
        # "Sam" partially matches two people; the message must not go to whichever is listed first.
        graph.on("GET", r"/users/Sam", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([SAMANTHA, SAM_PATEL]))
        message = err(await teams.send_user_message("Sam", "Your review is due"))
        assert "No Teams user is named exactly 'Sam'" in message
        assert "Samantha Lee" in message and "Sam Patel" in message
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_longer_name_than_anyone_in_directory_sends_nothing(self, teams, graph) -> None:
        # "sam" is contained in "samuel"; that must not make Sam the recipient.
        graph.on("GET", r"/users/Samuel", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([SAM_PATEL, {"id": "u-sam2", "displayName": "Sam", "mail": "s@contoso.com"}]))
        assert "No Teams user matches 'Samuel'" in err(await teams.send_user_message("Samuel", "hi"))
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_name_made_of_hex_letters_does_not_match_an_object_id(self, teams, graph) -> None:
        graph.on("GET", r"/users/Deb", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([{"id": "8f0deb12-0000-4000-8000-000000000001", "displayName": "Zed Quinn"}]))
        assert "No Teams user matches 'Deb'" in err(await teams.send_user_message("Deb", "hi"))
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_single_partial_match_is_offered_for_confirmation_not_messaged(self, teams, graph) -> None:
        graph.on("GET", r"/users/Sam", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([SAMANTHA, ME]))
        message = err(await teams.send_user_message("Sam", "Your review is due"))
        assert "No Teams user is named exactly 'Sam'" in message
        assert "Samantha Lee (samantha@contoso.com) [ID: u-samantha]" in message
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_unknown_user_sends_nothing(self, teams, graph) -> None:
        graph.on("GET", r"/users/.*", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([ME]))
        assert "No Teams user matches 'Zed'" in err(await teams.send_user_message("Zed", "hi"))
        assert graph.writes() == []


class TestGetUserConversations:
    @pytest.mark.asyncio
    async def test_missing_user_is_a_clear_error(self, teams, graph) -> None:
        message = err(await teams.get_user_conversations())
        assert "user_identifier" in message
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_ambiguous_name_reads_no_chat(self, teams, graph) -> None:
        graph.on("GET", r"/users/Sam", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", _users_page([SAMANTHA, SAM_PATEL]))
        assert "No Teams user is named exactly 'Sam'" in err(await teams.get_user_conversations("Sam"))
        assert not graph.calls("GET", r"/me/chats")

    @pytest.mark.asyncio
    async def test_person_on_second_directory_page_has_their_chat_read(self, teams, graph) -> None:
        zoe = {"id": "u-zoe", "displayName": "Zoe Park", "mail": "zoe@contoso.com"}
        first_page = _users_page([ME], next_link="https://graph.microsoft.com/v1.0/users?$skiptoken=p2")
        graph.on("GET", r"/users/Zoe Park", graph_error(404, "Request_ResourceNotFound", "not found"))
        graph.on("GET", r"/users", first_page, _users_page([zoe]), first_page)
        graph.on("GET", r"/users/u-zoe", zoe)
        graph.on("GET", r"/me/chats", {"value": [{"id": "chat-z", "chatType": "oneOnOne"}]})
        graph.on("GET", r"/chats/chat-z/members", {"value": [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "userId": "u-zoe"}]})
        graph.on("GET", r"/chats/chat-z/messages", {"value": []})
        data = ok(await teams.get_user_conversations("Zoe Park"))
        assert data["count"] == 0
        assert len(graph.calls("GET", r"/users")) == 2

    @pytest.mark.asyncio
    async def test_returns_recent_messages_in_time_window(self, teams, graph) -> None:
        now = datetime.now(timezone.utc)
        graph.on("GET", r"/users/(sam@contoso.com|u-sam)", SAM_PATEL)
        graph.on("GET", r"/me/chats", {"value": [{"id": "chat-1", "chatType": "oneOnOne"}]})
        graph.on("GET", r"/chats/chat-1/members", {"value": [{"@odata.type": "#microsoft.graph.aadUserConversationMember", "userId": "u-sam"}]})
        graph.on("GET", r"/chats/chat-1/messages", {"value": [
            {"id": "old", "createdDateTime": (now - timedelta(days=3)).isoformat(), "from": {"user": {"displayName": "Sam Patel"}}, "body": {"content": "old"}},
            {"id": "new", "createdDateTime": (now - timedelta(minutes=5)).isoformat(), "from": {"user": {"displayName": "Sam Patel"}}, "body": {"content": "new"}},
            {"id": "sys", "createdDateTime": (now - timedelta(minutes=1)).isoformat(), "body": {"content": "system event"}},
        ]})
        data = ok(await teams.get_user_conversations("sam@contoso.com", days=1))
        assert [m["message_id"] for m in data["messages"]] == ["new"]
        assert data["messages"][0]["sender"] == "Sam Patel"


# ===========================================================================
# Calendar and meetings
# ===========================================================================


class TestGetMeetings:
    @pytest.mark.asyncio
    async def test_start_without_end_is_refused(self, teams, graph) -> None:
        assert "must be provided together" in err(await teams.get_meetings(start_datetime="2026-03-01T00:00:00Z"))
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_end_before_start_is_refused(self, teams, graph) -> None:
        assert "end_datetime must be after start_datetime" in err(
            await teams.get_meetings(start_datetime="2026-03-02T00:00:00Z", end_datetime="2026-03-01T00:00:00Z")
        )
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_calendar_view_query_and_recurring_filter(self, teams, graph) -> None:
        graph.on("GET", r"/me/calendarView", {"value": [
            {"id": "e1", "subject": "Standup", "type": "occurrence", "isOnlineMeeting": True,
             "onlineMeeting": {"joinUrl": "https://teams/join/1"}},
            {"id": "e2", "subject": "1:1", "type": "singleInstance"},
        ]})
        data = ok(await teams.get_meetings(
            start_datetime="2026-03-01T00:00:00Z", end_datetime="2026-03-31T00:00:00Z",
            meeting_type=" Recurring ", top=5000,
        ))
        query = graph.requests[0].query
        assert query["startDateTime"] == ["2026-03-01T00:00:00+00:00"]
        assert query["$top"] == ["1000"]
        assert [m["subject"] for m in data["meetings"]] == ["Standup"]
        assert data["meetings"][0]["meeting_id"]

    @pytest.mark.asyncio
    async def test_api_error_is_returned(self, teams, graph) -> None:
        graph.on("GET", r"/me/calendarView", graph_error(403, "ErrorAccessDenied", "Access is denied"))
        assert "Access is denied" in err(await teams.get_meetings())


class TestSearchCalendarEventsInRange:
    @pytest.mark.asyncio
    async def test_blank_keyword_is_refused(self, teams, graph) -> None:
        assert err(await teams.search_calendar_events_in_range("  ", "2026-03-01", "2026-03-31")) == "keyword cannot be empty."
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_filters_subjects_and_sends_timezone_preference(self, teams, graph) -> None:
        graph.on("GET", r"/me/calendar/calendarView", {"value": [
            {"id": "e1", "subject": "Sprint Planning"},
            {"id": "e2", "subject": "Lunch"},
        ]})
        data = ok(await teams.search_calendar_events_in_range(
            "sprint", "2026-03-01T00:00:00Z", "2026-03-31T23:59:59Z", timezone="India Standard Time",
        ))
        assert [e["subject"] for e in data["results"]] == ["Sprint Planning"]
        request = graph.requests[0]
        assert request.query["startDateTime"] == ["2026-03-01T00:00:00Z"]
        assert request.headers["prefer"] == {'outlook.timezone="India Standard Time"'}

    @pytest.mark.asyncio
    async def test_keyword_with_apostrophe_still_matches(self, teams, graph) -> None:
        # Subjects are filtered in Python, not in an OData $filter, so there is nothing to escape.
        graph.on("GET", r"/me/calendar/calendarView", {"value": [{"id": "e1", "subject": "Sync with O'Brien"}]})
        data = ok(await teams.search_calendar_events_in_range("O'Brien", "2026-03-01", "2026-03-31"))
        assert data["count"] == 1
        assert data["keyword"] == "O'Brien"


class TestMeetingTranscripts:
    TRANSCRIPT_LINES = "\n".join([
        json.dumps({"speakerName": "Ann", "spokenText": "Let's start"}),
        "not json",
        json.dumps({"speakerName": "Bo", "spokenText": ""}),
        json.dumps({"spokenText": "Anonymous remark"}),
    ])

    @pytest.mark.asyncio
    async def test_no_identifier_is_refused(self, teams, graph) -> None:
        assert "Could not resolve to a Teams online meeting ID" in err(await teams.get_my_meeting_transcripts())
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_join_url_is_decoded_and_transcript_entries_parsed(self, teams, graph) -> None:
        graph.on("GET", r"/me/onlineMeetings", {"value": [{"id": "om-1"}]})
        graph.on("GET", r"/me/onlineMeetings/om-1/transcripts", {"value": [{"id": "tr-1", "createdDateTime": "2026-03-01T10:00:00Z"}]})
        graph.on("GET", r"/me/onlineMeetings/om-1/transcripts/tr-1/metadataContent", self.TRANSCRIPT_LINES.encode())
        data = ok(await teams.get_my_meeting_transcripts(join_url="https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0?o'k"))
        assert graph.requests[0].query["$filter"] == ["joinWebUrl eq 'https://teams.microsoft.com/l/meetup-join/19:meeting_x@thread.v2/0?o''k'"]
        transcript = data["transcripts"][0]
        assert transcript["transcript_id"] == "tr-1"
        assert transcript["entries"] == [
            {"timestamp": "", "speaker": "Ann", "text": "Let's start"},
            {"timestamp": "", "speaker": "Unknown", "text": "Anonymous remark"},
        ]

    @pytest.mark.asyncio
    async def test_event_id_is_resolved_through_its_join_url(self, teams, graph) -> None:
        graph.on("GET", r"/me/calendar/events/ev-1", {"id": "ev-1", "onlineMeeting": {"joinUrl": "https://teams/join/1"}})
        graph.on("GET", r"/me/onlineMeetings", {"value": [{"id": "om-1"}]})
        graph.on("GET", r"/me/onlineMeetings/om-1/transcripts", {"value": []})
        data = ok(await teams.get_my_meeting_transcripts(event_id="ev-1"))
        assert data == {"message": "No transcripts available for this meeting", "transcripts": []}

    @pytest.mark.asyncio
    async def test_event_without_online_meeting_is_refused(self, teams, graph) -> None:
        graph.on("GET", r"/me/calendar/events/ev-1", {"id": "ev-1", "subject": "In person"})
        assert "Could not resolve" in err(await teams.get_my_meeting_transcripts(event_id="ev-1"))

    @pytest.mark.asyncio
    async def test_transcript_list_error_is_returned(self, teams, graph) -> None:
        graph.on("GET", r"/me/onlineMeetings/om-1/transcripts", graph_error(403, "Forbidden", "Transcripts need OnlineMeetingTranscript.Read.All"))
        assert "OnlineMeetingTranscript.Read.All" in err(await teams.get_my_meeting_transcripts(meeting_id="om-1"))


class TestMeetingPeople:
    @pytest.mark.asyncio
    async def test_people_attended_reads_every_report(self, teams, graph) -> None:
        graph.on("GET", r"/me/onlineMeetings/om-1/attendanceReports", {"value": [{"id": "rep-1"}]})
        graph.on("GET", r"/me/onlineMeetings/om-1/attendanceReports/rep-1/attendanceRecords", {"value": [
            {"id": "rec-1", "emailAddress": "ann@contoso.com", "identity": {"displayName": "Ann"}, "totalAttendanceInSeconds": 1800},
        ]})
        data = ok(await teams.get_people_attended(meeting_id="om-1"))
        person = data["people"][0]
        assert person["display_name"] == "Ann"
        assert person["email_address"] == "ann@contoso.com"
        assert person["attendance_report_id"] == "rep-1"

    @pytest.mark.asyncio
    async def test_people_attended_without_identifier_is_refused(self, teams, graph) -> None:
        assert "Could not resolve" in err(await teams.get_people_attended())
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_people_invited_come_from_event_attendees(self, teams, graph) -> None:
        graph.on("GET", r"/me/events", {"value": [{"id": "ev-1", "attendees": [
            {"emailAddress": {"name": "Ann", "address": "ann@contoso.com"}, "type": "required", "status": {"response": "accepted"}},
        ]}]})
        data = ok(await teams.get_people_invited("ev-1"))
        assert data["people"][0]["address"] == "ann@contoso.com"


class TestCreateEvent:
    @pytest.mark.asyncio
    async def test_maps_fields_to_graph_event(self, teams, graph) -> None:
        graph.on("POST", r"/me/calendar/events", {"id": "ev-new", "subject": "Planning"})
        data = ok(await teams.create_event(
            "Planning", "2026-03-02T10:00:00", "2026-03-02T11:00:00", timezone="India Standard Time",
            body="Agenda", location="Room 4", attendees=["ann@contoso.com", "  "], is_online_meeting=True,
        ))
        body = graph.calls("POST")[0].body
        assert body["subject"] == "Planning"
        assert body["start"] == {"dateTime": "2026-03-02T10:00:00", "timeZone": "India Standard Time"}
        assert body["body"] == {"content": "Agenda", "contentType": "text"}
        assert body["location"] == {"displayName": "Room 4"}
        assert [(a["emailAddress"]["address"], a["type"]) for a in body["attendees"]] == [("ann@contoso.com", "required")]
        assert body["isOnlineMeeting"] is True
        assert data["event_id"] == "ev-new"

    @pytest.mark.asyncio
    async def test_json_string_attendees_become_one_attendee_each(self, teams, graph) -> None:
        # The tool schema declares attendees as a string holding a JSON array.
        graph.on("POST", r"/me/calendar/events", {"id": "ev-new"})
        ok(await teams.create_event("Sync", "2026-03-02T10:00:00", "2026-03-02T11:00:00",
                                    attendees='["ann@contoso.com", "bo@contoso.com"]'))
        addresses = [a["emailAddress"]["address"] for a in graph.calls("POST")[0].body["attendees"]]
        assert addresses == ["ann@contoso.com", "bo@contoso.com"]

    @pytest.mark.asyncio
    async def test_comma_separated_attendees_are_split(self, teams, graph) -> None:
        graph.on("POST", r"/me/calendar/events", {"id": "ev-new"})
        ok(await teams.create_event("Sync", "2026-03-02T10:00:00", "2026-03-02T11:00:00",
                                    attendees="ann@contoso.com, bo@contoso.com"))
        addresses = [a["emailAddress"]["address"] for a in graph.calls("POST")[0].body["attendees"]]
        assert addresses == ["ann@contoso.com", "bo@contoso.com"]

    @pytest.mark.asyncio
    async def test_malformed_attendee_json_is_refused(self, teams, graph) -> None:
        message = err(await teams.create_event("Sync", "2026-03-02T10:00:00", "2026-03-02T11:00:00",
                                               attendees='["ann@contoso.com", '))
        assert message.startswith("attendees must be a list of email addresses")
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_recurrence_that_is_not_an_object_is_refused(self, teams, graph) -> None:
        message = err(await teams.create_event("Standup", "2026-03-02T09:00:00", "2026-03-02T09:15:00",
                                               recurrence="every weekday"))
        assert message.startswith("recurrence must be an object with 'pattern' and 'range' keys")
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_weekly_recurrence_is_sent(self, teams, graph) -> None:
        graph.on("POST", r"/me/calendar/events", {"id": "ev-new"})
        ok(await teams.create_event("Standup", "2026-03-02T09:00:00", "2026-03-02T09:15:00", recurrence={
            "pattern": {"type": "weekly", "interval": 1, "daysOfWeek": ["Monday", "Wednesday"]},
            "range": {"type": "endDate", "startDate": "2026-03-02", "endDate": "2026-12-31"},
        }))
        recurrence = graph.calls("POST")[0].body["recurrence"]
        assert recurrence["pattern"]["type"] == "weekly"
        assert recurrence["pattern"]["daysOfWeek"] == ["monday", "wednesday"]
        assert recurrence["range"] == {"endDate": "2026-12-31", "startDate": "2026-03-02", "type": "endDate"}

    @pytest.mark.asyncio
    async def test_padded_pattern_type_is_sent_in_graph_spelling(self, teams, graph) -> None:
        # The datasource lowercases without stripping and turns anything it doesn't know into daily.
        graph.on("POST", r"/me/calendar/events", {"id": "ev-new"})
        ok(await teams.create_event("Standup", "2026-03-02T09:00:00", "2026-03-02T09:15:00", recurrence={
            "pattern": {"type": " weekly ", "interval": 1, "daysOfWeek": ["Monday"]},
            "range": {"type": "noEnd", "startDate": "2026-03-02"},
        }))
        assert graph.calls("POST")[0].body["recurrence"]["pattern"]["type"] == "weekly"

    @pytest.mark.asyncio
    async def test_padded_dates_are_normalised_and_the_event_is_built(self, teams, graph) -> None:
        graph.on("POST", r"/me/calendar/events", {"id": "ev-new"})
        ok(await teams.create_event("Review", "2026-03-02T09:00:00", "2026-03-02T10:00:00", recurrence={
            "pattern": {"type": "absoluteMonthly ", "interval": 1, "dayOfMonth": 2},
            "range": {"type": "endDate", "startDate": " 2026-03-02 ", "endDate": "2026-12-02 "},
        }))
        recurrence = graph.calls("POST")[0].body["recurrence"]
        assert recurrence["pattern"]["type"] == "absoluteMonthly"
        assert (recurrence["range"]["startDate"], recurrence["range"]["endDate"]) == ("2026-03-02", "2026-12-02")

    @pytest.mark.asyncio
    async def test_json_string_recurrence_is_parsed(self, teams, graph) -> None:
        graph.on("POST", r"/me/calendar/events", {"id": "ev-new"})
        ok(await teams.create_event("Standup", "2026-03-02T09:00:00", "2026-03-02T09:15:00", recurrence=json.dumps({
            "pattern": {"type": "daily", "interval": 1},
            "range": {"type": "numbered", "startDate": "2026-03-02", "numberOfOccurrences": 10},
        })))
        assert graph.calls("POST")[0].body["recurrence"]["range"]["numberOfOccurrences"] == 10

    @pytest.mark.asyncio
    async def test_recurrence_without_start_date_explains_what_to_fix(self, teams, graph) -> None:
        message = err(await teams.create_event("Standup", "2026-03-02T09:00:00", "2026-03-02T09:15:00", recurrence={
            "pattern": {"type": "daily"}, "range": {"type": "noEnd"},
        }))
        assert message == "recurrence range is missing startDate."
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_flat_recurrence_error_is_not_reported_as_authentication(self, teams, graph) -> None:
        message = err(await teams.create_event("Standup", "2026-03-02T09:00:00", "2026-03-02T09:15:00", recurrence={
            "type": "daily", "interval": 1,
        }))
        assert AUTH_MESSAGE_FRAGMENT not in message
        assert "startDate" in message
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_unknown_recurrence_type_is_refused_not_turned_into_daily(self, teams, graph) -> None:
        message = err(await teams.create_event("Review", "2026-03-02T09:00:00", "2026-03-02T10:00:00", recurrence={
            "pattern": {"type": "monthly", "interval": 1},
            "range": {"type": "noEnd", "startDate": "2026-03-02"},
        }))
        assert "monthly" in message and "absoluteMonthly" in message
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_api_error_is_returned(self, teams, graph) -> None:
        graph.on("POST", r"/me/calendar/events", graph_error(400, "ErrorInvalidRequest", "Start time is invalid"))
        assert "Start time is invalid" in err(await teams.create_event("x", "bad", "bad"))


class TestCreateChannelMeeting:
    @pytest.mark.asyncio
    async def test_creates_online_event_and_posts_join_link(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        graph.on("POST", r"/me/events", {"id": "ev-1", "onlineMeeting": {"joinUrl": "https://teams/join/abc"}})
        graph.on("POST", r"/teams/t1/channels/c-std/messages", {"id": "msg-1"})
        data = ok(await teams.create_channel_meeting("t1", " general ", "Retro", "2026-03-02T10:00:00", "2026-03-02T11:00:00", timezone=None))
        event_body = graph.calls("POST", r"/me/events")[0].body
        assert event_body["isOnlineMeeting"] is True
        assert event_body["onlineMeetingProvider"] == "teamsForBusiness"
        assert event_body["start"]["timeZone"] == "Asia/Kolkata"
        assert "https://teams/join/abc" in graph.calls("POST", r"/teams/t1/channels/c-std/messages")[0].body["body"]["content"]
        assert data["event_id"] == "ev-1"

    @pytest.mark.asyncio
    async def test_unknown_channel_name_is_refused(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        assert "Channel 'Random' not found." in err(await teams.create_channel_meeting("t1", "Random", "Retro", "a", "b"))
        assert graph.writes() == []

    @pytest.mark.asyncio
    async def test_private_channel_is_refused(self, teams, graph) -> None:
        graph.on("GET", r"/teams/t1/channels", CHANNELS)
        assert "private channels" in err(await teams.create_channel_meeting("t1", "Leads", "Retro", "a", "b"))
        assert graph.writes() == []


class TestEditEvent:
    @pytest.mark.asyncio
    async def test_no_fields_is_refused(self, teams, graph) -> None:
        assert err(await teams.edit_event("ev-1")) == "No fields provided to update"
        assert graph.requests == []

    @pytest.mark.asyncio
    async def test_patches_only_changed_fields(self, teams, graph) -> None:
        graph.on("PATCH", r"/me/events/ev-1", None)
        data = ok(await teams.edit_event("ev-1", subject="Moved", start_datetime="2026-03-02T12:00:00", timezone=None))
        body = graph.calls("PATCH")[0].body
        body.pop("@odata.type")
        assert body == {"start": {"dateTime": "2026-03-02T12:00:00", "timeZone": "UTC"}, "subject": "Moved"}
        assert data["event_id"] == "ev-1"

    @pytest.mark.asyncio
    async def test_api_error_is_returned(self, teams, graph) -> None:
        graph.on("PATCH", r"/me/events/ev-1", graph_error(404, "ErrorItemNotFound", "The specified object was not found"))
        assert "was not found" in err(await teams.edit_event("ev-1", subject="x"))


# ===========================================================================
# Recurrence normalisation
# ===========================================================================


class TestBuildRecurrenceBody:
    def test_nested_shape_normalises_range_type_alias(self) -> None:
        result = _build_recurrence_body({
            "pattern": {"type": "daily"},
            "range": {"rangeType": "no_end", "startDate": "2026-03-01"},
        })
        assert result["range"]["type"] == "noEnd"

    def test_nested_shape_derives_range_type(self) -> None:
        assert _build_recurrence_body({
            "pattern": {"type": "daily"}, "range": {"startDate": "2026-03-01", "numberOfOccurrences": 3},
        })["range"]["type"] == "numbered"
        assert _build_recurrence_body({
            "pattern": {"type": "daily"}, "range": {"startDate": "2026-03-01", "endDate": "2026-04-01"},
        })["range"]["type"] == "endDate"

    def test_flat_shape_is_split_into_pattern_and_range(self) -> None:
        result = _build_recurrence_body({
            "type": "weekly", "interval": 2, "daysOfWeek": ["Friday"],
            "rangeType": "EndDate", "startDate": "2026-03-06", "endDate": "2026-06-26",
        })
        assert result == {
            "pattern": {"type": "weekly", "interval": 2, "daysOfWeek": ["Friday"]},
            "range": {"type": "endDate", "startDate": "2026-03-06", "endDate": "2026-06-26"},
        }

    def test_flat_shape_without_pattern_is_refused(self) -> None:
        with pytest.raises(ValueError, match="missing pattern"):
            _build_recurrence_body({"startDate": "2026-03-01"})

    def test_missing_pattern_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="pattern type None is not supported"):
            _build_recurrence_body({"pattern": {"interval": 1}, "range": {"startDate": "2026-03-01"}})

    def test_unknown_range_type_is_refused(self) -> None:
        with pytest.raises(ValueError, match="range type 'forever' is not supported"):
            _build_recurrence_body({"type": "Weekly", "daysOfWeek": ["Monday"], "rangeType": "forever", "startDate": "2026-03-02"})

    @pytest.mark.parametrize("start_date", [None, "", "  ", "next monday", "2026-02-30", "20260302", "2026-W10-1", "\uff12\uff10\uff12\uff16-03-02"])
    def test_start_date_must_be_a_real_date(self, start_date: object) -> None:
        with pytest.raises(ValueError, match="startDate must be a date in YYYY-MM-DD form"):
            _build_recurrence_body({"pattern": {"type": "daily"}, "range": {"type": "noEnd", "startDate": start_date}})

    def test_datetime_dates_are_sent_as_plain_dates(self) -> None:
        result = _build_recurrence_body({
            "pattern": {"type": "daily"},
            "range": {"type": "endDate", "startDate": datetime(2026, 3, 2, 9, 30, tzinfo=timezone(timedelta(hours=5, minutes=30))), "endDate": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        })
        assert (result["range"]["startDate"], result["range"]["endDate"]) == ("2026-03-02", "2026-06-01")

    def test_pattern_type_is_rewritten_to_graph_spelling(self) -> None:
        result = _build_recurrence_body({"type": " RELATIVEMONTHLY", "daysOfWeek": ["Monday"], "index": "first", "startDate": "2026-03-02"})
        assert result["pattern"]["type"] == "relativeMonthly"

    def test_end_date_must_be_a_real_date(self) -> None:
        with pytest.raises(ValueError, match="endDate must be a date in YYYY-MM-DD form"):
            _build_recurrence_body({
                "pattern": {"type": "daily"}, "range": {"type": "endDate", "startDate": "2026-03-01", "endDate": "31/12/2026"},
            })

    def test_non_dict_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must be a dict"):
            _build_recurrence_body(["daily"])  # type: ignore[arg-type]


class TestHandleError:
    def test_validation_error_text_reaches_the_agent(self, teams) -> None:
        assert err(teams._handle_error(ValueError("recurrence range is missing startDate."), "create event")) == (
            "recurrence range is missing startDate."
        )

    def test_unauthorized_error_asks_for_authentication(self, teams) -> None:
        assert AUTH_MESSAGE_FRAGMENT in err(teams._handle_error(RuntimeError("401 Unauthorized"), "get teams"))

    def test_other_errors_are_passed_through(self, teams) -> None:
        assert err(teams._handle_error(RuntimeError("Graph is throttling requests"), "get teams")) == (
            "Graph is throttling requests"
        )


# ===========================================================================
# Serialisation helpers
# ===========================================================================


class TestSerializeResponse:
    def test_plain_object_uses_public_attributes_and_additional_data(self) -> None:
        class _Obj:
            def __init__(self) -> None:
                self.name = "x"
                self._private = "hidden"
                self.additional_data = {"extra": 1, "name": "ignored"}

        assert Teams._serialize_response(_Obj()) == {"name": "x", "additional_data": {"extra": 1, "name": "ignored"}, "extra": 1}

    def test_object_without_attributes_falls_back_to_string(self) -> None:
        assert Teams._serialize_response(object()).startswith("<object object")

    def test_next_link_is_read_from_any_known_key(self) -> None:
        assert Teams._extract_next_link({"odata_next_link": " https://next "}) == "https://next"
        assert Teams._extract_next_link({"nextLink": ""}) is None
        assert Teams._extract_next_link([]) is None

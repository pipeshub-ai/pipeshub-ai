"""WP-38 RATEBUDGET-2 (G-31, C20.32, GA-17): Jira, Confluence, Google and Microsoft join the shared vendor budget.

Which family each connector and toolset spends, that each connector's sync is paced by a policy that takes from it,
and that each vendor's "slow down" answer (429 with Retry-After, Google's 403 rateLimitExceeded, Graph's 503 with
Retry-After) pauses the account.
"""

from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httplib2  # type: ignore
import httpx
import pytest

from app.agents.agent_loop.instance_creator import ToolInstanceCreator
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_cloud_personal.connector import (
    JiraCloudPersonalConnector,
)
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveIndividualConnector,
)
from app.connectors.sources.google.drive.team.connector import GoogleDriveTeamConnector
from app.connectors.sources.google.gmail.individual.connector import (
    GoogleGmailIndividualConnector,
)
from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector
from app.connectors.sources.microsoft.onedrive.connector import OneDriveConnector
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.outlook_individual.connector import (
    OutlookIndividualConnector,
)
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    SharePointConnector,
)
from app.services.rate_budget import (
    AccountBudget,
    BudgetLimits,
    IRateBudget,
    RateAccount,
    RatePriority,
    account_scope,
    family_for_connector,
    family_for_toolset,
    is_throttle,
    limits_for,
    note_throttled_threadsafe,
    set_shared_budget,
)
from app.sources.client.google.send_safe_http import SendSafeHttp, google_throttled
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.jira.jira import JiraRESTClientViaApiKey
from app.sources.client.microsoft.microsoft import (
    GraphMode,
    MSGraphClient,
    MSGraphClientWithClientIdSecretConfig,
)
from app.sources.client.microsoft.send_safe_middleware import (
    ResilienceHandler,
    SendReportHandler,
    credential_graph_adapter,
)
from app.sources.client.resilience import ResiliencePolicy
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"

# The connector class, the Connectors member its app carries, and the family it spends.
CONNECTORS: list[tuple[type[BaseConnector], Connectors, str]] = [
    (JiraConnector, Connectors.JIRA, "jira"),
    (JiraCloudPersonalConnector, Connectors.JIRA_CLOUD_PERSONAL, "jira"),
    (ConfluenceConnector, Connectors.CONFLUENCE, "confluence"),
    (GoogleDriveIndividualConnector, Connectors.GOOGLE_DRIVE, "google_drive"),
    (GoogleDriveTeamConnector, Connectors.GOOGLE_DRIVE_WORKSPACE, "google_drive"),
    (GoogleGmailIndividualConnector, Connectors.GOOGLE_MAIL, "gmail"),
    (GoogleGmailTeamConnector, Connectors.GOOGLE_MAIL_WORKSPACE, "gmail"),
    (OneDriveConnector, Connectors.ONEDRIVE, "microsoft_sharepoint"),
    (SharePointConnector, Connectors.SHAREPOINT_ONLINE, "microsoft_sharepoint"),
    (OutlookConnector, Connectors.OUTLOOK, "microsoft_outlook"),
    (OutlookIndividualConnector, Connectors.OUTLOOK_INDIVIDUAL, "microsoft_outlook"),
]


# Connectors whose declared policy also sets their client's retries.
_CLIENT_RETRIES: frozenset[type[BaseConnector]] = frozenset({ConfluenceConnector})


class RecordingBudget(IRateBudget):
    def __init__(self) -> None:
        self.takes: list[tuple[RateAccount, RatePriority]] = []
        self.pauses: list[tuple[RateAccount, float]] = []

    async def take(self, account: RateAccount, limits: BudgetLimits, priority: RatePriority) -> float:
        self.takes.append((account, priority))
        return 0.0

    async def pause(self, account: RateAccount, seconds: float) -> None:
        self.pauses.append((account, seconds))


@pytest.fixture
def recording() -> Iterator[RecordingBudget]:
    budget = RecordingBudget()
    set_shared_budget(budget)
    yield budget
    set_shared_budget(None)


def _handle(recording: RecordingBudget, family: str) -> AccountBudget:
    return AccountBudget(recording, RateAccount(ORG, family), BudgetLimits(rate=10, burst=20))


def _policy(shared: AccountBudget | None) -> ResiliencePolicy:
    policy = ResiliencePolicy(rate_limit=None, max_retries=0, base_delay=0.01, max_delay=60)
    policy.shared = shared
    return policy


def _as(cls: type[BaseConnector], name: Connectors) -> BaseConnector:
    """A connector with ``cls``'s declared metadata and app name, without its vendor client."""
    fake = type(f"Fake{cls.__name__}", (BaseConnector,), {"_connector_metadata": getattr(cls, "_connector_metadata", {})})
    fake.__abstractmethods__ = frozenset()
    app = MagicMock()
    app.get_app_name.return_value = name
    return fake(app, logging.getLogger("t"), SimpleNamespace(org_id=ORG), MagicMock(), MagicMock(), "c1", "team", "u1")


# ── Families ────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("cls", "name", "family"), CONNECTORS, ids=lambda v: getattr(v, "__name__", str(v)))
def test_wp38_ga17_each_connectors_sync_is_paced_by_a_policy_that_spends_its_vendors_budget(
    recording: RecordingBudget, cls: type[BaseConnector], name: Connectors, family: str
) -> None:
    connector = _as(cls, name)
    assert connector.rate_budget is not None and connector.rate_budget.account == RateAccount(ORG, family)
    policy = connector.resilience
    assert policy is not None, f"{cls.__name__} declares a resilience config"
    assert policy.shared is connector.rate_budget
    if cls in _CLIENT_RETRIES:
        # Main #4553 moved Confluence's retries into its client's quota-aware policy, which takes its knobs from here.
        assert policy.max_retries > 0
    else:
        # Retries stay the connector's own (Jira's 429 helper, googleapiclient, kiota's RetryHandler).
        assert policy.max_retries == 0
    limits = limits_for(family)
    assert limits is not None and policy.rate_limit == limits.rate


def test_wp38_a_connectors_enum_app_name_finds_its_family(recording: RecordingBudget) -> None:
    # Connectors is not a str-Enum: its str() is "Connectors.SLACK", which no family is named.
    assert family_for_connector(Connectors.SLACK) == "slack"
    assert _as(BaseConnector, Connectors.SLACK_WORKSPACE).rate_budget is not None


@pytest.mark.parametrize(
    ("toolset", "family"),
    [
        ("jira", "jira"),
        ("confluence", "confluence"),
        ("drive", "google_drive"),
        ("gmail", "gmail"),
        ("onedrive", "microsoft_sharepoint"),
        ("sharepoint", "microsoft_sharepoint"),
        ("outlook", "microsoft_outlook"),
    ],
)
def test_wp38_ga17_each_toolset_spends_the_family_its_connector_syncs(
    recording: RecordingBudget, toolset: str, family: str
) -> None:
    assert family_for_toolset(toolset) == family
    context = SimpleNamespace(
        logger=logging.getLogger("t"), config_service=None, tool_to_toolset_map={}, toolset_configs={},
        agent_toolsets=[], user_id="u1", tool_state={}, org_id=ORG, rate_priority="interactive",
    )
    assert ToolInstanceCreator(context).call_scope(toolset) is not None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    [
        # No connector syncs these APIs, so a shared bucket would only be a second limit.
        "calendar", "docs", "sheets", "teams",
        # Self-hosted: the limit is the customer's admin's setting, off by default.
        "jiradatacenter", "confluencedatacenter",
    ],
)
def test_wp38_a_toolset_without_a_syncing_connector_or_a_documented_quota_has_no_family(name: str) -> None:
    assert family_for_toolset(name) is None
    assert family_for_connector(Connectors.JIRA_DATA_CENTER) is None
    assert family_for_connector(Connectors.CONFLUENCE_DATA_CENTER) is None


# ── Atlassian: HTTPClient ────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def vendor_answer(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Response]:
    answers: list[httpx.Response] = []

    async def send(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        response = answers.pop(0)
        response.request = request
        return response

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)
    return answers


async def test_wp38_a_paced_atlassian_client_takes_a_shared_token_and_its_429_pauses_the_account(
    recording: RecordingBudget, vendor_answer: list[httpx.Response]
) -> None:
    handle = _handle(recording, "jira")
    client = HTTPClient("t")
    client.use_resilience(_policy(handle))
    vendor_answer.append(httpx.Response(429, headers={"Retry-After": "7"}))
    response = await client.execute(HTTPRequest(url="https://acme.atlassian.net/rest/api/3/myself"))
    await asyncio.sleep(0)
    assert response.status == 429
    assert recording.takes == [(handle.account, RatePriority.INTERACTIVE)]
    # Not retried by the transport (max_retries 0), yet the policy's gate and the account are both paused.
    assert recording.pauses == [(handle.account, 7.0)]
    with pytest.raises(RuntimeError):
        client.use_resilience(None)


async def test_wp38_an_agents_atlassian_429_pauses_the_account_its_tool_call_spends(
    recording: RecordingBudget, vendor_answer: list[httpx.Response]
) -> None:
    handle = _handle(recording, "confluence")
    vendor_answer.extend([httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(503), httpx.Response(200)])
    client = HTTPClient("t")
    with account_scope(handle):
        for _ in range(3):
            _ = await client.execute(HTTPRequest(url="https://acme.atlassian.net/wiki/api/v2/pages"))
    # A bare 503 is an outage, not a throttle.
    assert recording.pauses == [(handle.account, 3.0)]
    assert recording.takes == []


@pytest.mark.parametrize(
    ("cls", "name", "builder", "datasource"),
    [
        (JiraConnector, Connectors.JIRA, "_build_jira_client", "JiraDataSource"),
        (ConfluenceConnector, Connectors.CONFLUENCE, "_build_confluence_client", "ConfluenceDataSource"),
    ],
)
async def test_wp38_jira_and_confluence_init_pace_the_client_their_builder_shares_with_the_toolsets(
    recording: RecordingBudget,
    monkeypatch: pytest.MonkeyPatch,
    cls: type[BaseConnector],
    name: Connectors,
    builder: str,
    datasource: str,
) -> None:
    connector: Any = _as(cls, name)
    # Jira's init reads the REST client's base URL (main #4428's site handling); an OAuth client's is the gateway's.
    http = (
        JiraRESTClientViaApiKey("https://api.atlassian.com/ex/jira/cloud-1", "a@example.com", "k")
        if cls is JiraConnector else HTTPClient("t")
    )
    built = MagicMock()
    built.get_client.return_value = http
    setattr(connector, builder, AsyncMock(return_value=built))
    connector._cache_authenticated_confluence_email = AsyncMock()
    connector._cache_authenticated_jira_profile = MagicMock()
    connector._remember_configured_site = AsyncMock()
    monkeypatch.setattr(__import__(cls.__module__, fromlist=[datasource]), datasource, MagicMock())
    assert await cls.init(connector) is True  # type: ignore[attr-defined]
    assert http.resilience is not None
    if cls not in _CLIENT_RETRIES:
        assert http.resilience is connector.resilience
    # Either way every call the client sends spends the connector's share of the org's vendor budget.
    assert http.resilience.shared is connector.rate_budget


# ── Google: SendSafeHttp and the datasources ─────────────────────────────────────────────────────────────────────


def _google_error(status: int, reason: str) -> bytes:
    return json.dumps({"error": {"code": status, "errors": [{"reason": reason}]}}).encode()


@pytest.mark.parametrize(
    ("status", "content", "throttled"),
    [
        (429, b"", True),
        (403, _google_error(403, "rateLimitExceeded"), True),
        (403, _google_error(403, "userRateLimitExceeded"), False),
        (403, json.dumps({"error": {"details": [{"reason": "RATE_LIMIT_EXCEEDED"}]}}).encode(), True),
        (403, _google_error(403, "insufficientPermissions"), False),
        (403, b"not json", False),
        (500, b"", False),
    ],
)
def test_wp38_google_slow_down_is_a_429_or_a_rate_limit_403(status: int, content: bytes, throttled: bool) -> None:
    assert google_throttled(status, content) is throttled


async def test_wp38_ga17_a_google_403_rate_limit_on_a_worker_thread_pauses_the_account(
    recording: RecordingBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = _handle(recording, "google_drive")
    answers = [
        (httplib2.Response({"status": "403"}), _google_error(403, "rateLimitExceeded")),
        (httplib2.Response({"status": "429", "retry-after": "9"}), b""),
        (httplib2.Response({"status": "403"}), _google_error(403, "insufficientPermissions")),
    ]
    monkeypatch.setattr(httplib2.Http, "request", lambda self, *a, **k: answers.pop(0))
    http = SendSafeHttp()
    with account_scope(handle):
        for _ in range(3):
            _ = await asyncio.to_thread(http.request, "https://www.googleapis.com/drive/v3/files")
    await asyncio.sleep(0.05)
    assert recording.pauses == [(handle.account, 2.0), (handle.account, 9.0)]


async def test_wp38_the_threadsafe_note_is_a_no_op_with_no_account_in_scope(recording: RecordingBudget) -> None:
    await asyncio.to_thread(note_throttled_threadsafe, 5.0)
    await asyncio.sleep(0.01)
    assert recording.pauses == []


async def test_wp38_a_paced_google_datasource_takes_a_token_per_request(recording: RecordingBudget) -> None:
    handle = _handle(recording, "gmail")
    source = GoogleGmailDataSource(object(), resilience=_policy(handle))  # type: ignore[arg-type]
    assert await source.execute(lambda: "a") == "a"
    assert await source.execute(lambda: "b") == "b"
    assert [account for account, _ in recording.takes] == [handle.account, handle.account]
    unpaced = GoogleGmailDataSource(object())  # type: ignore[arg-type]
    _ = await unpaced.execute(lambda: "c")
    assert len(recording.takes) == 2


# ── Microsoft Graph: the send-safe middleware ────────────────────────────────────────────────────────────────────


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return self._responses.pop(0)


def _pipeline(client: httpx.AsyncClient) -> list[str]:
    names: list[str] = []
    middleware: Any = client._transport.pipeline._first_middleware  # type: ignore[attr-defined]
    while middleware is not None:
        names.append(type(middleware).__name__)
        middleware = middleware.next
    return names


async def test_wp38_ga17_a_graph_sync_attempt_takes_a_token_and_a_503_retry_after_pauses_the_account(
    recording: RecordingBudget,
) -> None:
    handle = _handle(recording, "microsoft_sharepoint")
    paced = ResilienceHandler(_policy(handle))
    paced.next = SendReportHandler()
    transport = _Transport(httpx.Response(503, headers={"Retry-After": "4"}), httpx.Response(503))
    with account_scope(handle):
        for _ in range(2):
            _ = await paced.send(httpx.Request("GET", "https://graph.microsoft.com/v1.0/drives/d/root/delta"), transport)
    await asyncio.sleep(0)
    assert [account for account, _ in recording.takes] == [handle.account, handle.account]
    # The policy's gate and the reporter both name the account; a bare 503 names nothing.
    assert recording.pauses == [(handle.account, 4.0), (handle.account, 4.0)]


async def test_wp38_an_agents_graph_429_pauses_the_account_its_tool_call_spends(recording: RecordingBudget) -> None:
    handle = _handle(recording, "microsoft_outlook")
    with account_scope(handle):
        _ = await SendReportHandler().send(
            httpx.Request("GET", "https://graph.microsoft.com/v1.0/me/messages"),
            _Transport(httpx.Response(429, headers={"Retry-After": "11"})),
        )
    assert recording.pauses == [(handle.account, 11.0)]
    assert recording.takes == []


def test_wp38_sync_graph_clients_pace_every_attempt_below_the_retry_handler(recording: RecordingBudget) -> None:
    policy = _policy(_handle(recording, "microsoft_sharepoint"))
    names = _pipeline(credential_graph_adapter(AsyncMock(), resilience=policy)._http_client)
    assert names.index("SendSafeRetryHandler") < names.index("ResilienceHandler") < names.index("SendReportHandler")
    assert names[-1] == "SendReportHandler"
    outlook = MSGraphClient.build_with_config(
        MSGraphClientWithClientIdSecretConfig("id", "secret", "tenant"), mode=GraphMode.APP, resilience=policy
    )
    graph = outlook.get_client().get_ms_graph_service_client()
    assert "ResilienceHandler" in _pipeline(graph.request_adapter._http_client)
    # A toolset's client has no policy: its tool call already took from the budget.
    toolset = MSGraphClient.build_with_config(MSGraphClientWithClientIdSecretConfig("id", "secret", "tenant"))
    names = _pipeline(toolset.get_client().get_ms_graph_service_client().request_adapter._http_client)
    assert "ResilienceHandler" not in names and names[-1] == "SendReportHandler"


@pytest.mark.parametrize(
    ("status", "retry_after", "throttle"),
    [(429, None, True), (429, 2.0, True), (503, 1.0, True), (503, None, False), (500, 1.0, False), (200, None, False)],
)
def test_wp38_a_throttle_is_a_429_or_a_503_that_says_when(status: int, retry_after: float | None, throttle: bool) -> None:
    assert is_throttle(status, retry_after) is throttle

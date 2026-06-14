"""Regression tests for the manually maintained Jira Data Center / Server v2
helpers in ``app.sources.external.jira.jira``.

These guard against the Cloud-vs-DC field drift that caused the production
outage where ``POST /rest/api/2/search`` was rejected by Jira DC with::

    Unrecognized field "fieldsByKeys" (class com.atlassian.jira.rest.v2.search
    .SearchRequestBean), not marked as ignorable (6 known properties:
    "maxResults", "jql", "fields", "expand", "validateQuery", "startAt")

The DC ``SearchRequestBean`` rejects unknown body properties with HTTP 400, so
``search_issues_post_v2`` must only forward ``fieldsByKeys`` when a caller
explicitly opts in (it remains a Cloud-only property and is therefore omitted
from the default DC payload).
"""

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


# The production import chain
#   app.sources.external.jira.jira
#     -> app.sources.client.jira.jira (JiraClient)
#        -> app.api.routes.toolsets
#           -> app.containers.connector / etcd3 / kafka / ...
# pulls in the entire connector container subtree just to satisfy a type hint.
# This unit test only needs ``JiraDataSource`` itself, so we short-circuit the
# chain at ``app.api.routes.toolsets`` with a stub exposing ``get_toolset_by_id``
# *before* the real module is imported. Anything that doesn't actually touch
# the toolset registry inside ``JiraDataSource`` is unaffected.
_toolsets_stub = types.ModuleType("app.api.routes.toolsets")
_toolsets_stub.get_toolset_by_id = lambda *_a, **_kw: None  # type: ignore[attr-defined]
sys.modules.setdefault("app.api.routes.toolsets", _toolsets_stub)

from app.sources.external.jira.jira import JiraDataSource  # noqa: E402


@pytest.fixture
def captured_request():
    """Capture the ``HTTPRequest`` passed into ``client.execute``."""
    holder: dict[str, object] = {}

    async def _capture(req):
        holder["req"] = req
        return MagicMock(status=200)

    return holder, _capture


@pytest.fixture
def jira_data_source(captured_request):
    """Build a ``JiraDataSource`` wired to a stub HTTP client.

    Bypasses the real ``JiraClient`` / ``HTTPClient`` so the test only
    exercises request shaping inside ``JiraDataSource``.
    """
    _, capture = captured_request

    inner_client = MagicMock()
    inner_client.get_base_url.return_value = "https://jira.test"
    inner_client.execute = AsyncMock(side_effect=capture)

    jira_client = MagicMock()
    jira_client.get_client.return_value = inner_client

    return JiraDataSource(jira_client)


class TestSearchIssuesPostV2DcCompat:
    """``POST /rest/api/2/search`` body must match Jira DC's SearchRequestBean.

    DC v2 SearchRequestBean accepts exactly 6 properties: jql, startAt,
    maxResults, fields, expand, validateQuery. Anything else -> HTTP 400.
    """

    async def test_default_call_omits_fields_by_keys(self, jira_data_source, captured_request):
        """Regression: default DC search must not inject Cloud-only ``fieldsByKeys``.

        This is the exact request shape the connector uses in
        ``JiraDataCenterConnector._fetch_issues_batched`` and was the trigger
        for the production 400 on every project sync.
        """
        holder, _ = captured_request

        await jira_data_source.search_issues_post_v2(
            jql='project = "FIJI" ORDER BY updated ASC, id ASC',
            startAt=0,
            maxResults=50,
            fields=["summary", "status", "updated"],
        )

        req = holder["req"]
        assert req.method == "POST"
        assert req.url.endswith("/rest/api/2/search")

        body = req.body
        assert isinstance(body, dict)
        assert "fieldsByKeys" not in body, (
            "fieldsByKeys is a Cloud-only field; Jira DC rejects it with HTTP 400. "
            f"Body sent: {body!r}"
        )

        # The DC-supported keys the connector relies on must round-trip.
        assert body["jql"] == 'project = "FIJI" ORDER BY updated ASC, id ASC'
        assert body["startAt"] == 0
        assert body["maxResults"] == 50
        assert body["fields"] == ["summary", "status", "updated"]

    async def test_explicit_fields_by_keys_is_forwarded(self, jira_data_source, captured_request):
        """An explicit opt-in still ships ``fieldsByKeys`` in the body.

        Pins the conditional emit so it cannot silently regress back to
        "always send" or "never send".
        """
        holder, _ = captured_request

        await jira_data_source.search_issues_post_v2(
            jql="project = TEST",
            fieldsByKeys=True,
        )

        body = holder["req"].body
        assert body.get("fieldsByKeys") is True

    async def test_only_known_dc_body_keys_present(self, jira_data_source, captured_request):
        """Default DC body must be a strict subset of the SearchRequestBean schema.

        Locks down the body shape: any future param accidentally added to the
        body without DC support will trip this assertion in CI rather than in
        production.
        """
        holder, _ = captured_request

        await jira_data_source.search_issues_post_v2(
            jql="project = TEST",
            startAt=0,
            maxResults=50,
            fields=["summary"],
            validateQuery="strict",
        )

        body = holder["req"].body
        allowed = {"jql", "startAt", "maxResults", "fields", "expand", "validateQuery"}
        unexpected = set(body.keys()) - allowed
        assert not unexpected, (
            f"DC v2 SearchRequestBean rejects unknown body fields; got extras: {unexpected!r}"
        )


class TestSearchIssuesGetV2DcCompat:
    """``GET /rest/api/2/search`` query params must also be conditional.

    DC tolerates unknown query params (unlike the POST body), so this never
    caused a 400 — but a regression on the GET variant would still ship a
    misleading query string to DC servers.
    """

    async def test_default_call_omits_fields_by_keys(self, jira_data_source, captured_request):
        holder, _ = captured_request

        await jira_data_source.search_issues_get_v2(
            jql="project = TEST",
            startAt=0,
            maxResults=50,
        )

        req = holder["req"]
        assert req.method == "GET"
        assert req.url.endswith("/rest/api/2/search")
        assert "fieldsByKeys" not in req.query_params

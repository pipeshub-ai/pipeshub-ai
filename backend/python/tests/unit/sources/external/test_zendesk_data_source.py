"""Unit tests for the Zendesk data source's incremental export endpoints."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.zendesk.zendesk import ZendeskDataSource


BASE_URL = "https://acme.zendesk.com/api/v2"


@pytest.fixture
def datasource():
    http = MagicMock()
    http.get_base_url.return_value = BASE_URL
    response = MagicMock()
    response.status = 200
    response.is_json = True
    response.json.return_value = {"end_of_stream": True}
    http.execute = AsyncMock(return_value=response)

    client = MagicMock()
    client.get_client.return_value = http
    return ZendeskDataSource(client)


def _query(datasource):
    # HTTPRequest exposes the field as query_params; `query` is only its alias.
    return datasource.http.execute.await_args.kwargs["request"].query_params


def _url(datasource):
    return datasource.http.execute.await_args.kwargs["request"].url


class TestIncrementalExportsUseCursorEndpoints:
    """The time-based exports (``/incremental/tickets.json``) page with
    next_page/end_time and never return after_cursor, so the connector's cursor
    loop stopped after the first 1000 records against them."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("incremental_tickets", "/incremental/tickets/cursor.json"),
            ("incremental_users", "/incremental/users/cursor.json"),
        ],
    )
    async def test_targets_cursor_endpoint(self, datasource, method, path):
        await getattr(datasource, method)(start_time=1)

        assert _url(datasource) == f"{BASE_URL}{path}"

    async def test_organizations_target_time_based_endpoint(self, datasource):
        """Zendesk exposes cursor incremental exports for tickets and users only;
        /incremental/organizations/cursor.json 404s as InvalidEndpoint."""
        await datasource.incremental_organizations(start_time=1)

        assert _url(datasource) == f"{BASE_URL}/incremental/organizations.json"

    async def test_organizations_always_send_start_time(self, datasource):
        await datasource.incremental_organizations(start_time=1767312000)

        query = _query(datasource)
        assert query["start_time"] == "1767312000"
        assert "cursor" not in query

    @pytest.mark.parametrize(
        "method",
        ["incremental_tickets", "incremental_users", "incremental_organizations"],
    )
    async def test_first_request_seeds_with_start_time(self, datasource, method):
        await getattr(datasource, method)(start_time=1767312000)

        query = _query(datasource)
        assert query["start_time"] == "1767312000"
        assert "cursor" not in query

    @pytest.mark.parametrize(
        "method",
        ["incremental_tickets", "incremental_users"],
    )
    async def test_later_requests_send_cursor_alone(self, datasource, method):
        # Zendesk rejects start_time and cursor together.
        await getattr(datasource, method)(start_time=1767312000, cursor="abc123")

        query = _query(datasource)
        assert query["cursor"] == "abc123"
        assert "start_time" not in query

    async def test_ticket_sideloads_survive_cursor_paging(self, datasource):
        await datasource.incremental_tickets(
            start_time=1, cursor="abc123", include="users,groups"
        )

        assert _query(datasource)["include"] == "users,groups"

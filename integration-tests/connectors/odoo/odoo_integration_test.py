# pyright: ignore-file

"""Odoo integration tests — real XML-RPC calls against a live Odoo instance.

Client-level (auth, raw execute_kw) plus CRM DataSource-level (leads, teams,
stages, followers, contacts, users) — the same scope as the Odoo connector.
See conftest.py for fixtures. Read-only: nothing is created or deleted in
Odoo. No connector is registered and no full sync runs here.
"""

import pytest

from app.sources.client.odoo.odoo import OdooClient  # type: ignore[import-not-found]
from app.sources.external.odoo.odoo import OdooDataSource  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.odoo,
    pytest.mark.asyncio(loop_scope="session"),
]


class TestOdooClientAuth:
    @pytest.mark.order(1)
    async def test_connect_authenticates(self, odoo_client: OdooClient) -> None:
        """The session fixture already called connect() — prove it actually
        got a uid back from the real server, not just that it didn't raise."""
        assert odoo_client.is_connected()
        assert isinstance(odoo_client._uid, int) and odoo_client._uid > 0

    @pytest.mark.order(2)
    async def test_connect_is_idempotent_against_real_server(self, odoo_client: OdooClient) -> None:
        uid_before = odoo_client._uid
        await odoo_client.connect()
        assert odoo_client._uid == uid_before


class TestOdooClientExecuteKw:
    @pytest.mark.order(3)
    async def test_search_count_res_partner(self, odoo_client: OdooClient) -> None:
        count = await odoo_client.execute_kw("res.partner", "search_count", [[]])
        assert isinstance(count, int)
        assert count >= 0

    @pytest.mark.order(4)
    async def test_search_read_res_partner_fields(self, odoo_client: OdooClient) -> None:
        rows = await odoo_client.execute_kw(
            "res.partner", "search_read", [[]], {"fields": ["name", "write_date"], "limit": 3}
        )
        assert isinstance(rows, list)
        for row in rows:
            assert "name" in row
            assert "write_date" in row

    @pytest.mark.order(5)
    async def test_execute_kw_bad_model_raises(self, odoo_client: OdooClient) -> None:
        """Confirms real server-side faults surface as RuntimeError, not swallowed."""
        with pytest.raises(RuntimeError, match="Odoo call failed"):
            await odoo_client.execute_kw("not.a.real.model", "search_count", [[]])


class TestOdooCrmDataSource:
    """CRM-scoped DataSource methods the connector actually calls during
    sync — leads, teams, stages, followers, contacts, users. Read-only."""

    @pytest.mark.order(6)
    async def test_list_users_returns_salespersons(self, odoo_datasource: OdooDataSource) -> None:
        users = await odoo_datasource.list_users(include_inactive=True)
        assert isinstance(users, list)
        for user in users[:5]:
            assert isinstance(user.id, int)

    @pytest.mark.order(7)
    async def test_list_teams(self, odoo_datasource: OdooDataSource) -> None:
        teams = await odoo_datasource.list_teams()
        assert isinstance(teams, list)
        for team in teams:
            assert isinstance(team.id, int)

    @pytest.mark.order(8)
    async def test_list_stages_have_is_won_flag(self, odoo_datasource: OdooDataSource) -> None:
        """The connector maps stage_id -> is_won for DealRecord.is_won —
        confirms the real API actually returns that field, not just the mock."""
        stages = await odoo_datasource.list_stages()
        assert isinstance(stages, list)
        assert len(stages) > 0
        for stage in stages:
            assert isinstance(stage.is_won, bool)

    @pytest.mark.order(9)
    async def test_count_leads_matches_list_leads(self, odoo_datasource: OdooDataSource) -> None:
        """count_leads() has no include_archived param — it's always
        active-only, so it must be compared against the same (default,
        non-archived) list_leads() call, not an include_archived=True one."""
        count = await odoo_datasource.count_leads()
        leads = await odoo_datasource.list_leads(limit=count + 1)
        assert len(leads) == count

    @pytest.mark.order(10)
    async def test_list_leads_respects_type_filter(self, odoo_datasource: OdooDataSource) -> None:
        leads = await odoo_datasource.list_leads(lead_type="lead", limit=10)
        for lead in leads:
            assert lead.type == "lead"

    @pytest.mark.order(11)
    async def test_get_lead_round_trips_with_list_leads(self, odoo_datasource: OdooDataSource) -> None:
        leads = await odoo_datasource.list_leads(include_archived=True, limit=1)
        if not leads:
            pytest.skip("No leads in this Odoo instance to fetch.")
        fetched = await odoo_datasource.get_lead(leads[0].id)
        assert fetched is not None
        assert fetched.id == leads[0].id

    @pytest.mark.order(12)
    async def test_get_lead_missing_id_returns_none(self, odoo_datasource: OdooDataSource) -> None:
        assert await odoo_datasource.get_lead(999_999_999) is None

    @pytest.mark.order(13)
    async def test_list_followers_for_real_leads(self, odoo_datasource: OdooDataSource) -> None:
        leads = await odoo_datasource.list_leads(include_archived=True, limit=5)
        if not leads:
            pytest.skip("No leads in this Odoo instance to check followers for.")
        followers = await odoo_datasource.list_followers("crm.lead", [lead.id for lead in leads])
        assert isinstance(followers, list)
        for follower in followers:
            assert follower.res_id in {lead.id for lead in leads}

    @pytest.mark.order(14)
    async def test_list_followers_empty_ids_returns_empty(self, odoo_datasource: OdooDataSource) -> None:
        assert await odoo_datasource.list_followers("crm.lead", []) == []

    @pytest.mark.order(15)
    async def test_list_partners_and_get_partner_round_trip(self, odoo_datasource: OdooDataSource) -> None:
        partners = await odoo_datasource.list_partners(limit=1)
        if not partners:
            pytest.skip("No contacts in this Odoo instance.")
        fetched = await odoo_datasource.get_partner(partners[0].id)
        assert fetched is not None
        assert fetched.id == partners[0].id

# pyright: ignore-file

"""Odoo client integration tests — real XML-RPC calls against a live Odoo
instance. Client-level only (see conftest.py); no connector/sync exists yet.
"""

import pytest

from app.sources.client.odoo.odoo import OdooClient  # type: ignore[import-not-found]

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

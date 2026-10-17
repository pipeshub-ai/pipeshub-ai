# pyright: ignore-file

"""Odoo client + CRM datasource fixtures.

Read-only against a live Odoo instance — proves the real XML-RPC auth path
(OdooClient.connect / execute_kw) and the CRM-scoped DataSource methods the
Odoo connector (app/connectors/sources/odoo/connector.py) actually calls
(leads, teams, stages, followers, contacts, users). No connector is
registered here and no full sync runs — that's a much heavier, separate
concern; this stays at the Client/DataSource layer, matching the scope of
this connector (CRM only, not every Odoo module). Nothing is created or
deleted in Odoo — read-only against whatever the credentials can already see.

Scope comes entirely from ``ODOO_TEST_*`` env vars.
"""

import os

import pytest
import pytest_asyncio

from app.sources.client.odoo.odoo import OdooClient  # type: ignore[import-not-found]
from app.sources.external.odoo.odoo import OdooDataSource  # type: ignore[import-not-found]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def odoo_client() -> OdooClient:
    """Session-scoped, already-authenticated Odoo client."""
    url = os.getenv("ODOO_TEST_BASE_URL")
    db = os.getenv("ODOO_TEST_DB")
    username = os.getenv("ODOO_TEST_USERNAME")
    api_key = os.getenv("ODOO_TEST_API_KEY")

    if not url or not db or not username or not api_key:
        pytest.skip(
            "Odoo credentials not set "
            "(ODOO_TEST_BASE_URL, ODOO_TEST_DB, ODOO_TEST_USERNAME, ODOO_TEST_API_KEY)."
        )

    client = OdooClient(url=url, db=db, username=username, api_key=api_key)
    await client.connect()
    return client


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def odoo_datasource(odoo_client: OdooClient) -> OdooDataSource:
    """Session-scoped CRM datasource on top of the shared authenticated client."""
    return OdooDataSource(odoo_client)

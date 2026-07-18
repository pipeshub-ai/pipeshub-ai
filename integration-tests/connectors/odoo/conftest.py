# pyright: ignore-file

"""Odoo client fixtures.

Client-level only: no DataSource/Connector exists yet, so this proves the
real XML-RPC auth path (OdooClient.connect / execute_kw) against a live
Odoo instance. No connector is registered, no sync runs, nothing is
created or deleted in Odoo — read-only against whatever the credentials
can already see.

Scope comes entirely from ``ODOO_TEST_*`` env vars.
"""

import os

import pytest
import pytest_asyncio

from app.sources.client.odoo.odoo import OdooClient  # type: ignore[import-not-found]


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

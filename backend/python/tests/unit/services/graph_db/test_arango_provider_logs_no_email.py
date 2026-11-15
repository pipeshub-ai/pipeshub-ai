"""The ArangoDB provider methods that take a user's email never log it (success, not found and failure paths)."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

EMAIL = "alice.p9a@example.com"


@pytest.fixture
def provider(caplog: pytest.LogCaptureFixture) -> ArangoHTTPProvider:
    caplog.set_level(logging.DEBUG, logger="test.arango_no_email")
    p = ArangoHTTPProvider(logging.getLogger("test.arango_no_email"), MagicMock())
    p.http_client = AsyncMock()
    return p


def _assert_no_email(caplog: pytest.LogCaptureFixture) -> None:
    lines = [r for r in caplog.records if r.name == "test.arango_no_email"]
    assert lines, "the paths under test log something"
    assert not [r for r in lines if EMAIL in r.getMessage() or EMAIL in str(vars(r))]


@pytest.mark.parametrize("answer", [[{"_key": "u1", "email": EMAIL, "orgId": "o1"}], [], RuntimeError(f"no user {EMAIL}")])
async def test_get_app_user_by_email(provider, caplog, answer) -> None:
    provider.http_client.execute_aql = AsyncMock(side_effect=answer if isinstance(answer, Exception) else None,
                                                 return_value=None if isinstance(answer, Exception) else answer)
    provider._translate_node_from_arango = MagicMock(side_effect=lambda d: d)
    await provider.get_app_user_by_email(EMAIL, "conn-1")  # answers None on every failure
    _assert_no_email(caplog)


@pytest.mark.parametrize(("user_key", "answer"), [(None, []), ("u1", []), ("u1", [{"_key": "rel"}]), ("u1", RuntimeError(f"x {EMAIL}"))])
async def test_update_user_sync_state(provider, caplog, user_key, answer) -> None:
    provider.get_entity_id_by_email = AsyncMock(return_value=user_key)
    provider.http_client.execute_aql = AsyncMock(side_effect=answer if isinstance(answer, Exception) else None,
                                                 return_value=None if isinstance(answer, Exception) else answer)
    await provider.update_user_sync_state(EMAIL, "RUNNING")
    _assert_no_email(caplog)


async def test_store_page_token(provider, caplog) -> None:
    provider.http_client.execute_aql = AsyncMock(return_value=[{"_key": "t"}])
    await provider.store_page_token("chan-1", "res-1", EMAIL, "page-token-value", "2026-11-11")
    _assert_no_email(caplog)
    assert not [r for r in caplog.records if "page-token-value" in r.getMessage()]


@pytest.mark.parametrize("answer", [[{"token": "t"}], []])
async def test_get_page_token_db(provider, caplog, answer) -> None:
    provider.http_client.execute_aql = AsyncMock(return_value=answer)
    await provider.get_page_token_db("chan-1", "res-1", EMAIL)
    _assert_no_email(caplog)

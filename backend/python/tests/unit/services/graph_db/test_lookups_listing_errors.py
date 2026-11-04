"""The PostgreSQL lookups: a listing never turns "the graph could not answer" into an empty page, and a call inside a transaction reads inside it."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.postgres.lookups import LookupsMixin

pytestmark = pytest.mark.asyncio


def _lookups() -> LookupsMixin:
    m = LookupsMixin.__new__(LookupsMixin)
    m.logger = logging.getLogger("t")
    m._lrows = AsyncMock(return_value=[{"id": "a"}])
    m.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("down"))
    return m


@pytest.mark.parametrize("call", [
    lambda m: m.get_records_by_parent_record("p", "c", "o", 1, user_key="u"),
    lambda m: m.get_records_by_record_group("g", "c", "o", 1, user_key="u"),
])
async def test_a_listing_re_raises_when_access_cannot_be_checked(call) -> None:
    with patch("app.services.graph_db.postgres.lookups.current_org_id", return_value="o"), pytest.raises(PermissionVerificationUnavailableError):
        await call(_lookups())


async def test_records_linking_to_weburls_reads_inside_the_given_transaction() -> None:
    m = _lookups()
    with patch("app.services.graph_db.postgres.lookups.current_org_id", return_value="o"):
        await m.get_records_linking_to_weburls(["u"], "o", ["GITHUB"], transaction="tx1")
    assert m._lrows.await_args.kwargs["transaction"] == "tx1"


async def test_a_failed_people_upsert_is_raised_not_answered_false() -> None:
    m = _lookups()
    m.batch_upsert_nodes = AsyncMock(side_effect=RuntimeError("write failed"))
    with patch("app.services.graph_db.postgres.lookups.current_org_id", return_value="o"), pytest.raises(RuntimeError, match="write failed"):
        await m.batch_upsert_people([MagicMock()])

"""The page-limit refusal completes inside a held consumer record lease on the PostgreSQL lease manager."""
from __future__ import annotations

import pytest

from app.services.pg_state import PgLeaseManager, PgState
from tests.support.refusal_in_lease import refuse_inside_held_lease

pytestmark = pytest.mark.integration


async def test_a_refusal_completes_inside_the_held_record_lease(runtime, uid) -> None:
    await refuse_inside_held_lease(PgLeaseManager(PgState(runtime)), pool=f"record:rec-{uid}")


async def test_a_refusal_that_takes_the_record_lease_is_caught(runtime, uid) -> None:
    with pytest.raises(AssertionError, match="asked for a lease"):
        await refuse_inside_held_lease(PgLeaseManager(PgState(runtime)), pool=f"record:rec-{uid}", acquire_in_unindex=True)

"""`get_accessible_containers` on the PostgreSQL provider: the checks every store answers the same (tests/integration/graph_db/test_accessible_containers_real_backends.py)."""
from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest

from tests.integration.graph_db.test_accessible_containers_real_backends import (
    build,
    check,
)

from .pg_support import OrgBound, new_org

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


@pytest.fixture
async def world(pg_provider) -> AsyncIterator[tuple]:
    org = new_org()
    async with contextlib.AsyncExitStack() as cleanup:
        yield await build(OrgBound(pg_provider, org), org, cleanup)


async def test_the_containers_are_the_gated_apps(world: tuple) -> None:
    await check(world)

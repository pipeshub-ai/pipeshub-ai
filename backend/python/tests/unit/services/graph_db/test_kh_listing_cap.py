"""A whole-connector listing under memory pressure, in the Neo4j provider.

The walk holds every candidate until the page is cut. A memory error from the database is reported as
``KnowledgeHubListingTooLarge`` when the walk itself was big and ``KnowledgeHubListingBusy`` when it only met a pool
other requests had spent (never a 500). The fast path's eligibility is pinned here too: only a sort it keeps an index for.
"""

import logging
from types import SimpleNamespace

import pytest

from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingBusy,
    KnowledgeHubListingTooLarge,
    is_out_of_memory,
)
from app.services.graph_db.neo4j import kh_scope
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def provider() -> Neo4jProvider:
    p = object.__new__(Neo4jProvider)
    p.logger = logging.getLogger("test_kh_gate")
    return p


def test_the_refusals_say_what_to_do() -> None:
    assert "Open a folder, or narrow by search" in str(KnowledgeHubListingTooLarge())
    assert "try again" in str(KnowledgeHubListingBusy())


@pytest.mark.parametrize("code,expected", [
    ("Neo.TransientError.General.MemoryPoolOutOfMemoryError", True),
    ("Neo.TransientError.General.TransactionOutOfMemoryError", True),
    ("Neo.ClientError.Statement.SyntaxError", False),
    (None, False),
])
def test_out_of_memory_is_told_by_its_code(code, expected) -> None:
    assert Neo4jProvider._kh_out_of_memory(SimpleNamespace(code=code)) is expected  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_any_listing_statement_that_finds_the_pool_spent_is_busy_not_a_500() -> None:
    p = provider()

    async def spent(*_a: object, **_k: object) -> None:
        raise SimpleNamespace_error("Neo.TransientError.General.MemoryPoolOutOfMemoryError")

    p.client = SimpleNamespace(execute_query=spent)
    with pytest.raises(KnowledgeHubListingBusy) as busy:
        await p._kh_query("RETURN 1")
    assert busy.value.out_of_memory is True

    async def broken(*_a: object, **_k: object) -> None:
        raise SimpleNamespace_error("Neo.ClientError.Statement.SyntaxError")

    assert is_out_of_memory(RuntimeError("check_access failed")) is False
    wrapped = RuntimeError("check_access failed")
    wrapped.__cause__ = KnowledgeHubListingBusy(out_of_memory=True)
    assert is_out_of_memory(wrapped) and is_out_of_memory(ExceptionGroup("g", [wrapped]))
    p.client = SimpleNamespace(execute_query=broken)
    with pytest.raises(SimpleNamespace_error):
        await p._kh_query("RETURN 1")


class SimpleNamespace_error(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def test_a_walk_that_ran_a_while_before_the_pool_was_spent_is_too_large_and_one_that_failed_at_once_is_busy() -> None:
    assert isinstance(Neo4jProvider._kh_walk_refusal(0.5), KnowledgeHubListingTooLarge)
    assert isinstance(Neo4jProvider._kh_walk_refusal(3.0), KnowledgeHubListingTooLarge)
    busy = Neo4jProvider._kh_walk_refusal(0.1)
    assert isinstance(busy, KnowledgeHubListingBusy) and busy.out_of_memory is True

async def scope_reason(sort_field: str) -> str:
    with pytest.raises(kh_scope.Fallback) as fallback:
        await kh_scope.page_payload(
            None, None, "app", "org", [], 51, None, include_total=True, sort_field=sort_field,  # type: ignore[arg-type]
        )
    return fallback.value.reason


@pytest.mark.asyncio
async def test_only_a_sort_with_a_scope_index_is_eligible_for_the_fast_path() -> None:
    assert "no scope index" in await scope_reason("updatedAt")
    assert "no scope index" in await scope_reason("size")
    assert kh_scope.SORT_PROPS == {"name": "khSortName"}

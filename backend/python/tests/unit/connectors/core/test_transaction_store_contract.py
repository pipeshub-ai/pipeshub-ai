"""Every TransactionStore accepts every call its contract allows.

A store or a test double whose signature drifts from the abstract method
fails only when a caller uses the parameter it lost: a test double without
``org_id`` turns ``get_user_by_email(email, org_id=...)`` into a TypeError
that a broad ``except`` then reads as a failed lookup.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import pytest

from app.connectors.core.base.data_store.data_store import TransactionStore
from app.connectors.core.base.data_store.graph_data_store import GraphTransactionStore
from tests.unit.connectors.core.test_data_store import ConcreteTransactionStore

if TYPE_CHECKING:
    from collections.abc import Callable

_P = inspect.Parameter
_POSITIONAL = (_P.POSITIONAL_ONLY, _P.POSITIONAL_OR_KEYWORD)


def _rejected_calls(contract: Callable, impl: Callable) -> list[str]:
    """The calls ``contract`` allows that ``impl`` would reject."""
    wanted = inspect.signature(contract).parameters
    offered = inspect.signature(impl).parameters
    any_keyword = any(p.kind is _P.VAR_KEYWORD for p in offered.values())
    any_positional = any(p.kind is _P.VAR_POSITIONAL for p in offered.values())
    wanted_pos = [p for p in wanted.values() if p.kind in _POSITIONAL]
    offered_pos = [p for p in offered.values() if p.kind in _POSITIONAL]
    gaps: list[str] = []
    for n, want in enumerate(wanted_pos):
        if n >= len(offered_pos):
            if not any_positional:
                gaps.append(f"{want.name} is not accepted")
            continue
        got = offered_pos[n]
        if got.name != want.name and want.kind is _P.POSITIONAL_OR_KEYWORD and not any_keyword:
            gaps.append(f"{want.name} is called {got.name}")
        if want.default is not _P.empty and got.default is _P.empty:
            gaps.append(f"{want.name} is required")
    gaps += [f"{p.name} is required but not in the contract" for p in offered_pos[len(wanted_pos):]
             if p.default is _P.empty]
    for want in (p for p in wanted.values() if p.kind is _P.KEYWORD_ONLY):
        got = offered.get(want.name)
        if got is None:
            if not any_keyword:
                gaps.append(f"keyword {want.name} is not accepted")
        elif got.kind is _P.POSITIONAL_ONLY:
            gaps.append(f"{want.name} is positional-only")
        elif want.default is not _P.empty and got.default is _P.empty:
            gaps.append(f"{want.name} is required")
    gaps += [f"keyword {p.name} is required but not in the contract" for p in offered.values()
             if p.kind is _P.KEYWORD_ONLY and p.default is _P.empty and p.name not in wanted]
    return gaps


@pytest.mark.parametrize("store", [GraphTransactionStore, ConcreteTransactionStore], ids=lambda c: c.__name__)
def test_every_contract_call_is_accepted(store: type) -> None:
    drift = {
        name: gaps
        for name in sorted(TransactionStore.__abstractmethods__)
        if (gaps := _rejected_calls(getattr(TransactionStore, name), getattr(store, name)))
    }
    assert not drift, f"{store.__name__} rejects calls its contract allows: {drift}"


def test_the_check_sees_a_lost_keyword() -> None:
    async def contract(self, email: str, *, org_id: str | None = None) -> None: ...
    async def impl(self, email: str) -> None: ...
    assert _rejected_calls(contract, impl) == ["keyword org_id is not accepted"]

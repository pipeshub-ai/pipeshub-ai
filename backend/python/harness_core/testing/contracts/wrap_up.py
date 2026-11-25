"""Contract suite for ``WrapUpPort`` (BUDGETEND): the wrap-up reserve the answer-only turn after the spending limit
draws on.

A subclass provides ``wrap_ups``, a ``WrapUpHarness`` that builds the port over an account with room for the reserve
(``fresh``) and over one without (``starved``), and says what the account holds back (``reserved_tokens``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from harness_core.kernel.durable.ports import WrapUpPort


class WrapUpHarness(Protocol):
    def fresh(self) -> WrapUpPort: ...

    def starved(self) -> WrapUpPort: ...

    async def reserved_tokens(self) -> int: ...


class WrapUpContract:
    @pytest.fixture
    def wrap_ups(self) -> WrapUpHarness:
        raise NotImplementedError("provide a wrap_ups fixture")

    async def test_wu1_hold_holds_the_reserve_back_from_the_account(self, wrap_ups: WrapUpHarness) -> None:
        port = wrap_ups.fresh()
        assert await port.hold() and port.held
        assert await wrap_ups.reserved_tokens() >= port.size.input_tokens + port.size.output_tokens

    async def test_wu2_holding_again_holds_nothing_more(self, wrap_ups: WrapUpHarness) -> None:
        port = wrap_ups.fresh()
        assert await port.hold()
        before = await wrap_ups.reserved_tokens()
        assert await port.hold()
        assert await wrap_ups.reserved_tokens() == before

    async def test_wu3_arm_says_whether_a_reserve_is_there_to_draw(self, wrap_ups: WrapUpHarness) -> None:
        port = wrap_ups.fresh()
        assert not port.arm(), "nothing held: nothing to draw"
        assert await port.hold()
        assert port.arm()

    async def test_wu4_release_returns_the_reserve(self, wrap_ups: WrapUpHarness) -> None:
        port = wrap_ups.fresh()
        assert await port.hold()
        await port.release()
        assert not port.held and await wrap_ups.reserved_tokens() == 0
        await port.release()

    async def test_wu5_an_account_without_room_refuses_without_raising(self, wrap_ups: WrapUpHarness) -> None:
        port = wrap_ups.starved()
        assert not await port.hold()
        assert not port.held and not port.arm()


__all__ = ["WrapUpContract", "WrapUpHarness"]

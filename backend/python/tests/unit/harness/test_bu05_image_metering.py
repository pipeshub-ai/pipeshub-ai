"""BU-01, BU-05, GA-24: an image a run generates is one of the run's model calls: charged at the provider's per-image
price, admitted, refused before the provider when the run's budget is spent, released when the provider fails; tool code
builds no image model of its own. (WP-55's image cases; the run's model guard here is the default that checks nothing.)
"""

from __future__ import annotations

import ast
import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.model import helper_model_scope
from app.harness.classification import PassthroughGuard
from app.harness.run_image_model import RunImageModel
from app.utils.run_model_scope import run_model_scope
from harness_core.budget.errors import BudgetRefused
from harness_core.budget.types import MICRO_USD, Cap, RunAccountRef
from harness_core.kernel.budget.image_prices import (
    IMAGE_PRICES,
    UNLISTED_IMAGE_USD,
    image_price_usd,
)
from tests.unit.harness.test_wp49_meter_all import (
    KEY,
    PYTHON_ROOT,
    RUN,
    TOOL_REACHABLE,
    _meter,
    _Run,
    _World,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> _World:
    """WP-49's world: the fake provider answers only inside the run's slot and an open reservation."""
    monkeypatch.setattr("app.harness.assembly.model.ledger_enabled", lambda: True)
    monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "langchain")
    found = _World()
    asyncio.run(found.ledger.open_root(RUN, Cap(None, 1_000_000)))
    return found


def _naming(names: str | tuple[str, ...], *roots: str) -> Iterator[tuple[str, ast.Module]]:
    """The parsed files under ``roots`` (a directory or one file) whose text names any of ``names``: only those can
    call or import it, and parsing the whole app is what takes the time."""
    wanted = (names,) if isinstance(names, str) else names
    for root in roots:
        target = PYTHON_ROOT / root
        for path in [target] if target.suffix == ".py" else sorted(target.rglob("*.py")):
            text = path.read_text(encoding="utf-8-sig")
            if any(name in text for name in wanted):
                yield path.relative_to(PYTHON_ROOT).as_posix(), ast.parse(text)


def _calls(name: str, *roots: str) -> Iterator[tuple[str, ast.Call]]:
    for where, tree in _naming(name, *roots):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if callee == name:
                    yield where, node


def test_bu05_tool_code_builds_no_image_model_of_its_own() -> None:
    """An image tool asks ``app.utils.llm.build_image_generation_model``, which hands back the run's in a run."""
    found = [f"{where}:{call.lineno}" for where, call in _calls("get_image_generation_model", *TOOL_REACHABLE)]
    found += [
        f"{where}: imports get_image_generation_model"
        for where, tree in _naming("get_image_generation_model", *TOOL_REACHABLE)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and any(a.name == "get_image_generation_model" for a in node.names)
    ]
    assert found == []


# ---- image generation: metered per image, admitted ---------------------------------------------------


class _Images:
    """The provider's adapter: answers only inside a provider slot and an open ledger reservation."""

    provider = "openAI"

    def __init__(self, world: _World, model: str = "gpt-image-1", fail: bool = False) -> None:
        self.world = world
        self.model = model
        self.fail = fail

    async def generate(self, prompt: str, *, size: str = "1024x1024", n: int = 1) -> list[bytes]:
        if not (self.world.gate.in_flight(KEY) > 0 and self.world.ledger.open > 0):
            raise AssertionError("an image was asked for outside the run's slot and reservation")
        self.world.calls += 1
        if self.fail:
            raise RuntimeError("provider down")
        return [b"png"] * n

    async def edit(self, prompt: str, *, input_image: bytes, size: str = "1024x1024", n: int = 1) -> list[bytes]:
        return await self.generate(prompt, size=size, n=n)


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch, world: _World) -> dict[str, Any]:
    built: dict[str, Any] = {"adapter": _Images(world)}
    monkeypatch.setattr("app.utils.aimodels.get_image_generation_model", lambda provider, config: built["adapter"])
    return built


IMAGE_CONFIG = {"provider": "openAI", "configuration": {"model": "gpt-image-1"}}
# The placement verdicts are the unit tree's default (every call allowed): any configuration service.
NO_CONFIG: Any = object()


async def test_bu05_ga24_an_image_a_run_generates_is_charged_at_the_providers_per_image_price(
    world: _World, images: dict[str, Any]
) -> None:
    from app.utils.llm import build_image_generation_model

    with run_model_scope(helper_model_scope(_Run(), _meter(world), PassthroughGuard())):  # type: ignore[arg-type]
        adapter = await build_image_generation_model(NO_CONFIG, "openAI", IMAGE_CONFIG)
        assert isinstance(adapter, RunImageModel) and adapter.model == "gpt-image-1"
        produced = await adapter.generate("a chart of the leave policy", size="1792x1024", n=2)
    assert produced == [b"png", b"png"]
    remaining = await world.ledger.remaining(RUN)
    assert remaining.committed.usd_micros == round(IMAGE_PRICES["gpt-image-1"].oblong * MICRO_USD) * 2
    assert world.ledger.open == 0 and world.gate.in_flight(KEY) == 0


async def test_without_a_run_an_image_tool_gets_the_providers_adapter_unchanged(
    world: _World, images: dict[str, Any]
) -> None:
    from app.utils.llm import build_image_generation_model

    assert await build_image_generation_model(NO_CONFIG, "openAI", IMAGE_CONFIG) is images["adapter"]


async def test_bu01_a_run_out_of_budget_refuses_an_image_before_the_provider(
    world: _World, images: dict[str, Any]
) -> None:
    from app.harness.assembly.model import helper_meter
    from app.utils.llm import build_image_generation_model
    from harness_core.testing.ids import SeededIds

    spent = RunAccountRef("org-1", "run-spent")
    await world.ledger.open_root(spent, Cap(int(0.01 * MICRO_USD), None))
    ids = SeededIds(3)
    meter = helper_meter(
        admission=world.gate, priority_class="interactive", run_kind=None, accounts=world.ledger, account=spent,
        ids=lambda: ids,
    )
    with run_model_scope(helper_model_scope(_Run(), meter, PassthroughGuard())):  # type: ignore[arg-type]
        adapter = await build_image_generation_model(NO_CONFIG, "openAI", IMAGE_CONFIG)
        with pytest.raises(BudgetRefused):
            _ = await adapter.generate("a diagram", n=1)
    assert world.calls == 0


async def test_bu05_a_failed_image_call_is_released_not_charged(world: _World, images: dict[str, Any]) -> None:
    from app.utils.llm import build_image_generation_model

    images["adapter"] = _Images(world, fail=True)
    with run_model_scope(helper_model_scope(_Run(), _meter(world), PassthroughGuard())):  # type: ignore[arg-type]
        adapter = await build_image_generation_model(NO_CONFIG, "openAI", IMAGE_CONFIG)
        with pytest.raises(RuntimeError):
            _ = await adapter.generate("a diagram")
    remaining = await world.ledger.remaining(RUN)
    assert remaining.committed.usd_micros == 0 and world.ledger.open == 0 and world.gate.in_flight(KEY) == 0


def test_bu05_an_unlisted_image_model_never_costs_less_than_a_listed_one() -> None:
    assert image_price_usd("imagen-unknown", "1024x1024") == (UNLISTED_IMAGE_USD, False)
    assert all(UNLISTED_IMAGE_USD >= max(p.square, p.oblong) for p in IMAGE_PRICES.values())
    assert image_price_usd("openai/gpt-image-1", "1024x1024") == (IMAGE_PRICES["gpt-image-1"].square, True)



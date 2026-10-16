import json
import re
from collections.abc import Awaitable, Callable, Iterator
from types import SimpleNamespace
from typing import Never

import pytest

from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import AssistantMessage
from app.agent_loop_lib.core.responses import StopReason
from app.modules.named_entities import extractor as extractor_module
from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.extractor import (
    NamedEntityExtractor,
    NamedEntityRequest,
)
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.strategies import single_call
from app.modules.named_entities.strategies.agentic import AgenticExtractionStrategy
from app.modules.named_entities.strategies.base import (
    ExtractionContext,
    ExtractionDocument,
)
from app.modules.named_entities.strategies.selector import (
    ExtractionStrategySelector,
    clear_tool_capability,
    model_supports_tools,
)
from app.modules.named_entities.strategies.single_call import (
    SingleCallExtractionStrategy,
)
from app.modules.named_entities.text import TextUnit
from app.utils.concurrency import indexing_llm_slots_remaining
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

_ENABLED = frozenset({EntityKind.ORGANIZATION, EntityKind.PERSON})


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    clear_tool_capability()
    single_call._STEP_DOWN.clear()
    yield
    clear_tool_capability()
    single_call._STEP_DOWN.clear()


def _ctx(**overrides) -> ExtractionContext:
    values = {
        "org_id": "org",
        "norm": NormalizationContext(),
        "enabled": _ENABLED,
        "budgets": NamedEntityBudgets(),
        "provider_name": "p",
        "model_name": "m",
    }
    values.update(overrides)
    return ExtractionContext(**values)


class _NoTools:
    def bind_tools(self, tools) -> Never:
        raise NotImplementedError("tools unsupported")


def test_bind_tools_failure_selects_single_call_and_is_cached() -> None:
    doc = ExtractionDocument(units=[TextUnit(0, "b", "Acme hired Priya Shah")])
    ctx = _ctx(llm=_NoTools())
    assert ExtractionStrategySelector().choose(doc, ctx) == "single_call"
    assert model_supports_tools(None, "p", "m", None) is False


async def test_agent_bind_tools_error_falls_back_and_remembers() -> None:
    transport = ScriptedTransport([ScriptedStep(error=TransportError("bind_tools() failed for 5 tool(s)"))])

    async def complete(system, prompt) -> str:
        return json.dumps({"entities": [{"text": "Acme", "block": 0, "kind": "organization", "normalized": ""}]})

    extractor = NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=complete))
    result = await extractor.extract(NamedEntityRequest(
        blocks=[SimpleNamespace(index=0, id="b", type="text", data="Acme hired Priya Shah")],
        org_id="org",
        enabled=_ENABLED,
        transport=transport,
        provider_name="p",
        model_name="m",
        supports_tools=True,
    ))
    assert result.strategy == "single_call"
    assert [entity.display_name for entity in result.entities] == ["Acme"]
    assert model_supports_tools(None, "p", "m", None) is False


async def test_single_call_accepts_items_one_by_one() -> None:
    payload = json.dumps({"entities": [
        {"text": "Acme", "block": 0, "kind": "organization", "normalized": ""},
        {"text": "Priya Shah", "block": "zero", "kind": "person", "normalized": ""},
        {"text": "Globex", "block": 0, "kind": "organization", "normalized": ""},
        {"text": "Priya Shah", "block": 0, "kind": "person", "normalized": ""},
    ]})

    async def complete(system, prompt) -> str:
        return payload

    doc = ExtractionDocument(units=[TextUnit(0, "b", "Acme hired Priya Shah")])
    result = await SingleCallExtractionStrategy(complete=complete).extract(doc, [], _ctx())
    assert sorted(m.surface for m in result.mentions) == ["Acme", "Priya Shah"]
    assert result.rejected == 1


async def test_step_down_is_remembered_per_model(monkeypatch) -> None:
    calls: list[str] = []

    async def strict(llm, prompt) -> None:
        calls.append("strict")

    async def function_call(llm, prompt) -> Never:
        calls.append("tool")
        raise ValueError("schema rejected")

    class _Llm:
        async def ainvoke(self, messages) -> object:
            calls.append("prompt")
            remaining.append(indexing_llm_slots_remaining())
            return type("R", (), {"content": '{"entities": []}'})()

    remaining: list[int] = []
    baseline = indexing_llm_slots_remaining()
    monkeypatch.setattr(single_call, "_strict", strict)
    monkeypatch.setattr(single_call, "_function_call", function_call)
    strategy = SingleCallExtractionStrategy()
    ctx = _ctx(llm=_Llm())
    doc = ExtractionDocument(units=[TextUnit(0, "b", "Acme")])
    await strategy.extract(doc, [], ctx)
    await strategy.extract(doc, [], ctx)
    assert calls == ["strict", "tool", "prompt", "prompt"]
    assert single_call._STEP_DOWN[("p", "m")][0] == "prompt"
    assert remaining == [baseline - 1, baseline - 1]


async def test_an_error_every_mode_shares_does_not_step_down(monkeypatch) -> None:
    """A content filter or an outage fails strict, tool and prompt alike: the
    next record must still start strict."""
    calls: list[str] = []

    class _Refused(Exception):
        pass

    async def strict(llm, prompt) -> None:
        calls.append("strict")

    async def refuse(llm, prompt) -> Never:
        calls.append("refused")
        raise _Refused("content_filter")

    monkeypatch.setattr(single_call, "_strict", strict)
    monkeypatch.setattr(single_call, "_function_call", refuse)
    monkeypatch.setattr(single_call, "_prompted_json", refuse)
    strategy = SingleCallExtractionStrategy()
    doc = ExtractionDocument(units=[TextUnit(0, "b", "Acme")])
    result = await strategy.extract(doc, [], _ctx(llm=object()))

    assert result.termination_reason == "llm_error"
    assert single_call._STEP_DOWN == {}
    calls.clear()
    await strategy.extract(doc, [], _ctx(llm=object()))
    assert calls[0] == "strict"


async def test_a_timed_out_call_does_not_step_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A starved worker times strict out; the tool call that then answers is a
    retry, not proof that strict output is broken for the model."""
    calls: list[str] = []

    async def strict(llm: object, prompt: str) -> single_call.EntitySubmission:
        calls.append("strict")
        if calls.count("strict") == 1:
            raise TimeoutError
        return single_call.EntitySubmission(entities=[])

    async def function_call(llm: object, prompt: str) -> single_call.EntitySubmission:
        calls.append("tool")
        return single_call.EntitySubmission(entities=[])

    monkeypatch.setattr(single_call, "_strict", strict)
    monkeypatch.setattr(single_call, "_function_call", function_call)
    strategy = SingleCallExtractionStrategy()
    doc = ExtractionDocument(units=[TextUnit(0, "b", "Acme")])
    first = await strategy.extract(doc, [], _ctx(llm=object()))
    await strategy.extract(doc, [], _ctx(llm=object()))

    assert first.termination_reason == "single_call"
    assert calls == ["strict", "tool", "strict"]
    assert single_call._STEP_DOWN == {}


async def test_strict_reports_a_timed_out_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils import streaming

    async def hang(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr(streaming, "_ainvoke_throttled", hang)
    monkeypatch.setattr(streaming, "_apply_structured_output", lambda llm, schema: llm)
    with pytest.raises(TimeoutError):
        await single_call._strict(object(), "prompt")


def _unparseable_then(*later: object) -> tuple[list[str], object]:
    replies: list[str] = []

    async def call(*args: object, **kwargs: object) -> object:
        replies.append("call")
        if len(replies) == 1:
            return type("R", (), {"content": "not json"})()
        outcome = later[len(replies) - 2]
        if isinstance(outcome, type) and issubclass(outcome, BaseException):
            raise outcome
        return type("R", (), {"content": outcome})()

    return replies, call


async def test_strict_reports_a_timed_out_last_reflection(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first reply did not parse and the last retry that would correct it timed
    out: the last word was a starved call, not proof that strict output fails."""
    from app.utils import streaming

    replies, call = _unparseable_then(TimeoutError, TimeoutError)
    monkeypatch.setattr(streaming, "_ainvoke_throttled", call)
    monkeypatch.setattr(streaming, "_apply_structured_output", lambda llm, schema: llm)
    with pytest.raises(TimeoutError):
        await single_call._strict(object(), "prompt")
    assert len(replies) == 3


async def test_an_earlier_reflection_timeout_still_gets_its_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils import streaming

    replies, call = _unparseable_then(TimeoutError, '{"entities": []}')
    monkeypatch.setattr(streaming, "_ainvoke_throttled", call)
    monkeypatch.setattr(streaming, "_apply_structured_output", lambda llm, schema: llm)
    assert await single_call._strict(object(), "prompt") == single_call.EntitySubmission(entities=[])
    assert len(replies) == 3


async def test_a_remembered_step_down_expires(monkeypatch) -> None:
    single_call._STEP_DOWN[("p", "m")] = ("prompt", 0.0)
    calls: list[str] = []

    async def strict(llm, prompt) -> single_call.EntitySubmission:
        calls.append("strict")
        return single_call.EntitySubmission(entities=[])

    monkeypatch.setattr(single_call, "_strict", strict)
    await SingleCallExtractionStrategy().extract(ExtractionDocument(units=[TextUnit(0, "b", "Acme")]), [], _ctx(llm=object()))

    assert calls == ["strict"]
    assert ("p", "m") not in single_call._STEP_DOWN


def _request(transport=None, supports_tools=True) -> NamedEntityRequest:
    return NamedEntityRequest(
        blocks=[SimpleNamespace(index=0, id="b", type="text", data="Acme paid $1,250 on March 3, 2026")],
        org_id="org",
        enabled=frozenset({EntityKind.ORGANIZATION, EntityKind.CURRENCY}),
        transport=transport,
        provider_name="p",
        model_name="m",
        supports_tools=supports_tools,
    )


async def _down(system, prompt) -> Never:
    raise TransportError("provider unavailable")


@pytest.mark.parametrize("agent_first", [True, False])
async def test_an_llm_outage_is_partial_not_completed(agent_first) -> None:
    transport = ScriptedTransport([ScriptedStep(error=TransportError("provider unavailable"))]) if agent_first else None
    extractor = NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=_down))
    result = await extractor.extract(_request(transport, supports_tools=agent_first))
    assert (result.strategy, result.termination_reason, result.status) == ("deterministic", "llm_error", "PARTIAL")
    assert [entity.kind for entity in result.entities] == [EntityKind.CURRENCY]


async def test_an_llm_outage_with_nothing_deterministic_is_failed() -> None:
    extractor = NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=_down))
    request = _request(supports_tools=False)
    request.blocks[0].data = "Acme hired Priya"
    result = await extractor.extract(request)
    assert (result.termination_reason, result.status, result.entities) == ("llm_error", "FAILED", [])


def _people_request(blocks: int, **budgets: int) -> NamedEntityRequest:
    return NamedEntityRequest(
        blocks=[SimpleNamespace(index=i, id=f"b{i}", type="text", data=f"Person{i} Example joined.") for i in range(blocks)],
        org_id="org",
        enabled=frozenset({EntityKind.PERSON}),
        budgets=NamedEntityBudgets(**budgets),
        provider_name="p",
        model_name="m",
        supports_tools=False,
    )


def _people_reply(fail_call: int | None = None) -> Callable[[str, str], Awaitable[str]]:
    calls = {"n": 0}

    async def complete(system, prompt) -> str:
        calls["n"] += 1
        if calls["n"] == fail_call:
            raise TransportError("window timed out")
        blocks = [int(b) for b in re.findall(r"\[B(\d+)\]", prompt)]
        return json.dumps({"entities": [
            {"text": f"Person{b} Example", "block": b, "kind": "person", "normalized": ""} for b in blocks
        ]})

    return complete


# Each block is 23-24 characters, so a 125-character window holds five.
_FIVE_BLOCKS = 125


async def test_windows_past_the_budget_make_single_call_partial() -> None:
    extractor = NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=_people_reply()))
    result = await extractor.extract(_people_request(30, max_window_chars=_FIVE_BLOCKS, max_llm_windows=3))
    assert (result.termination_reason, result.status) == ("single_call", "PARTIAL")
    assert (result.stats.windows_total, result.stats.windows_run, result.stats.windows_failed) == (6, 3, 0)
    assert len(result.entities) == 15


async def test_a_failed_window_among_good_ones_is_partial() -> None:
    extractor = NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=_people_reply(fail_call=2)))
    result = await extractor.extract(_people_request(15, max_window_chars=_FIVE_BLOCKS))
    assert (result.termination_reason, result.status) == ("single_call", "PARTIAL")
    assert (result.stats.windows_run, result.stats.windows_failed) == (3, 1)
    assert len(result.entities) == 10


async def test_a_document_the_windows_cover_stays_completed() -> None:
    extractor = NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=_people_reply()))
    result = await extractor.extract(_people_request(10, max_window_chars=_FIVE_BLOCKS))
    assert result.status == "COMPLETED"
    assert (result.stats.windows_total, result.stats.windows_run) == (2, 2)


async def test_text_cut_at_the_record_cap_is_partial(monkeypatch) -> None:
    monkeypatch.setattr(extractor_module, "_MAX_RECORD_CHARS", 40)
    extractor = NamedEntityExtractor(single=SingleCallExtractionStrategy(complete=_people_reply()))
    result = await extractor.extract(_people_request(3))
    assert result.status == "PARTIAL"
    assert result.stats.chars_truncated is True


async def test_an_agent_that_gives_up_with_nothing_hands_the_document_to_the_single_call() -> None:
    """The live failure: the agent stopped on no_progress having accepted nothing."""
    from app.modules.named_entities.strategies.base import StrategyResult

    class _GaveUp:
        async def extract(self, doc, seed, ctx) -> StrategyResult:
            return StrategyResult(termination_reason="no_progress", rejected=17, rejected_reasons={"schema": 17})

    async def complete(system, prompt) -> str:
        return json.dumps({"entities": [{"text": "Acme", "block": 0, "kind": "organization", "normalized": ""}]})

    extractor = NamedEntityExtractor(agent=_GaveUp(), single=SingleCallExtractionStrategy(complete=complete))
    result = await extractor.extract(_request(ScriptedTransport([]), supports_tools=True))
    assert (result.strategy, result.termination_reason) == ("single_call", "single_call")
    assert EntityKind.ORGANIZATION in {entity.kind for entity in result.entities}


async def test_a_plain_reply_with_blocks_unread_is_not_a_finished_run() -> None:
    transport = ScriptedTransport([ScriptedStep(message=AssistantMessage(content="All done."), stop_reason=StopReason.END_TURN)])
    units = [TextUnit(i, f"b{i}", f"Block {i} mentions Acme. " + "Filler text. " * 75) for i in range(12)]
    result = await AgenticExtractionStrategy().extract(
        ExtractionDocument(units=units, token_estimate=3_000), [], _ctx(transport=transport),
    )
    assert result.termination_reason == "budget_turns"
    assert result.incomplete is True


class _Config:
    def __init__(self, blob) -> None:
        self._blob = blob

    async def get_config(self, key, default=None, use_cache=True) -> object:
        return self._blob


@pytest.mark.parametrize("blob", [{"budgets": {"max_turns": "8x"}}, {"enabled_kinds": 5}])
async def test_a_wrong_typed_stored_config_falls_back_to_defaults(blob, caplog) -> None:
    from app.modules.named_entities.domain.config import (
        NamedEntityConfig,
        load_named_entity_config,
    )

    assert await load_named_entity_config(_Config(blob)) == NamedEntityConfig()
    assert "invalid fields" in caplog.text
    assert "8x" not in caplog.text

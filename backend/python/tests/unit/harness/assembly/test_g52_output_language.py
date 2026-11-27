"""G-52 / C20.53: the person's locale sets the answer's language unless they write in another one or ask otherwise.

The locale reaches the run as ``input.locale`` (Node: the request's ``Accept-Language``, kept only while a live worker
advertises ``output_locale``). The loop says it in a turn section at the context tail, beside the time, never in the
cached prefix (GA-27).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.section_order import SECTION_NAMES, TURN_VOLATILE_NAMES
from app.harness.assembly.context import RunContext
from app.harness.contracts.state import RunInputSnapshot
from app.harness.worker.capabilities import PIPESHUB_FEATURES
from app.utils.output_language import build_output_language, output_locale
from tests.unit.agents.adapter.conftest import make_context

from . import world as w

_QUIET = {
    "_build_knowledge_context": MagicMock(return_value=""),
    "build_capability_summary": MagicMock(return_value=""),
    "_format_user_context": MagicMock(return_value=""),
}


def _blocks(**context: object) -> tuple[str, str]:
    builder = PipesHubPromptBuilder(make_context(**context))
    spec = AgentSpec(
        name="pipeshub-agent", system_prompt=builder,
        model=ModelSpec(provider="lc", model="claude-opus-5-5"), loop=ReActLoop(), max_turns=4,
    )
    with patch.multiple("app.agents.agent_loop.prompt_builder", **_QUIET):
        return builder.build_blocks(spec, AgentRuntime(tool_registry=ToolRegistry()), Goal(description="q"), [], {})


def test_g52_lang_1_the_run_input_carries_the_locale_to_the_run_context() -> None:
    snapshot = RunInputSnapshot.model_validate({"query": "q", "model": {"chatMode": "quick"}, "locale": "de-DE"})
    assert snapshot.locale == "de-DE"
    run = RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[], kb=[]), locale="de-DE"))
    assert run.locale == "de-DE"
    assert RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[], kb=[]))).locale is None


def test_g52_lang_2_an_ill_formed_locale_is_refused_at_the_wire() -> None:
    with pytest.raises(ValueError):
        _ = RunInputSnapshot.model_validate({"query": "q", "model": {"chatMode": "quick"}, "locale": "de DE; drop"})


@pytest.mark.parametrize(
    ("tag", "name"),
    [
        ("de-DE", "German"), ("es-ES", "Spanish"), ("cs-CZ", "Czech"), ("hi-IN", "Hindi"), ("ko-KR", "Korean"),
        ("en-US", "English"), ("en-IN", "English"), ("zh-CN", "Simplified Chinese"), ("zh-SG", "Simplified Chinese"),
        ("zh-TW", "Traditional Chinese"), ("fr", "French"),
    ],
)
def test_g52_lang_3_each_shipped_locale_names_its_language(tag: str, name: str) -> None:
    section = build_output_language(tag)
    assert f"Write your answer in {name}" in section
    assert f"({tag})" in section
    assert "unless they write to you in another language or ask for a different one" in section


def test_g52_lang_4_an_unknown_or_missing_locale_adds_nothing_or_names_the_tag() -> None:
    assert build_output_language(None) == ""
    assert build_output_language("") == ""
    assert build_output_language("not a tag!") == ""
    assert "language tagged xx-QQ" in build_output_language("xx-QQ")
    assert output_locale(" DE-de ") == "de-DE"
    assert output_locale("zh-hant-tw") == "zh-Hant-TW"


def test_g52_lang_5_the_language_is_a_turn_section_after_the_time() -> None:
    assert "output_language" in TURN_VOLATILE_NAMES
    assert SECTION_NAMES.index("output_language") == SECTION_NAMES.index("time_context") + 1


def test_g52_lang_6_the_prompt_says_the_language_in_the_volatile_tail_only() -> None:
    stable, volatile = _blocks(timezone="Europe/Berlin", current_time="2026-11-06T09:00:00+01:00", locale="de-DE")
    assert "Write your answer in German" in volatile
    assert "German" not in stable
    assert volatile.index("**Current time**") < volatile.index("Write your answer in German")


def test_g52_lang_7_no_locale_no_language_section() -> None:
    _, volatile = _blocks(timezone="Europe/Berlin")
    assert "Write your answer in" not in volatile


def test_g52_lang_8_the_chat_and_agent_paths_hand_the_locale_to_the_loop_state() -> None:
    from app.modules.agents.qna.chat_state import build_initial_state

    state = build_initial_state(
        {"query": "q", "locale": "ko-KR", "timezone": "Asia/Seoul"},
        {"userId": "u", "orgId": "o"}, MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(),
        "model", "key", has_sql_connector=False,
    )
    assert state["locale"] == "ko-KR"


def test_g52_lang_9_this_worker_advertises_output_locale() -> None:
    assert "output_locale" in PIPESHUB_FEATURES


def test_wp100_lang_n5_a_scheduled_run_with_no_pinned_locale_has_no_answer_language_section() -> None:
    # WP100-FU-4: no org default language. A schedule made through the API without Accept-Language pins none (Node,
    # wp100-output-language.test.ts LANG-N5); its run then answers in the language its prompt is written in.
    from app.harness.engines.instructions import engine_answer_language

    scheduled = w.claim(w.pinned_spec(tool_ids=[], kb=[]), trigger={"kind": "cron", "slotAt": "2026-07-06T07:00:00Z"})
    run = RunContext.from_claim(scheduled)
    assert (run.unattended, run.locale) == (True, None)
    _, volatile = _blocks(timezone="Europe/Berlin", locale=run.locale)
    assert "Answer Language" not in volatile and "Write your answer in" not in volatile
    assert engine_answer_language(scheduled.run) == ""

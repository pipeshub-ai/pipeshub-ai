"""`OpenAICacheStrategy`: automatic mode adds only `prompt_cache_key`
and touches nothing else; explicit mode (GPT-5.6+) additionally
restructures chosen messages into block-list content carrying
`prompt_cache_breakpoint`, gated by the same cumulative-floor and
allocator-budget rules Anthropic's strategy uses.
"""

from __future__ import annotations

from app.agent_loop_lib.cache.base import CacheableRequest
from app.llm.prompt_cache.capabilities import CacheCapability
from app.llm.prompt_cache.strategy.openai import OpenAICacheStrategy


def _capability(*, mode: str = "explicit", min_prefix_tokens: int = 10, max_breakpoints: int = 4) -> CacheCapability:
    return CacheCapability(
        mode=mode,  # type: ignore[arg-type]
        min_prefix_tokens=min_prefix_tokens,
        max_breakpoints=max_breakpoints,
        default_ttl="30m",
        extended_ttl=None,
        write_multiplier=1.0,
        read_multiplier=0.5,
        can_cache_tools=False,
        can_cache_system=mode == "explicit",
    )


def _strategy(cache_key: str | None = None, **kwargs: object) -> OpenAICacheStrategy:
    return OpenAICacheStrategy(_capability(**kwargs), cache_key=cache_key)


def _apply(strategy: OpenAICacheStrategy, request: CacheableRequest):
    return strategy.apply(strategy.plan(request), request)


class TestAutomaticMode:
    def test_no_breakpoints_added(self) -> None:
        messages = [
            {"role": "system", "content": "x" * 50},
            {"role": "user", "content": "x" * 50},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(mode="automatic"), CacheableRequest(messages=messages))
        for msg in result.messages:
            assert isinstance(msg["content"], str)

    def test_messages_unchanged_content(self) -> None:
        messages = [{"role": "user", "content": "hello"}]
        result = _apply(_strategy(mode="automatic"), CacheableRequest(messages=messages))
        assert result.messages == messages

    def test_prompt_cache_key_still_applied_in_automatic_mode(self) -> None:
        result = _apply(
            _strategy(mode="automatic", cache_key="org-1:user-1"),
            CacheableRequest(messages=[{"role": "user", "content": "hi"}]),
        )
        assert result.request_kwargs == {"prompt_cache_key": "org-1:user-1"}

    def test_no_cache_key_means_no_request_kwargs(self) -> None:
        result = _apply(
            _strategy(mode="automatic"), CacheableRequest(messages=[{"role": "user", "content": "hi"}])
        )
        assert result.request_kwargs == {}


class TestExplicitModeRequestKwargs:
    def test_adds_prompt_cache_options_explicit_mode(self) -> None:
        result = _apply(
            _strategy(cache_key="org-1:user-1"),
            CacheableRequest(messages=[{"role": "user", "content": "hi"}]),
        )
        assert result.request_kwargs["prompt_cache_options"] == {"mode": "explicit", "ttl": "30m"}
        assert result.request_kwargs["prompt_cache_key"] == "org-1:user-1"


class TestExplicitModeMessageBreakpoints:
    def test_final_two_messages_never_eligible(self) -> None:
        messages = [
            {"role": "user", "content": "x" * 50},
            {"role": "user", "content": "x" * 50},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        for msg in result.messages:
            assert isinstance(msg["content"], str)

    def test_below_cumulative_floor_not_marked(self) -> None:
        messages = [
            {"role": "system", "content": "x" * 5},  # 5 chars -> 1 token, floor is 10
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert isinstance(result.messages[0]["content"], str)

    def test_above_cumulative_floor_is_restructured_into_block_with_breakpoint(self) -> None:
        messages = [
            {"role": "system", "content": "x" * 50},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        content = result.messages[0]["content"]
        assert content == [
            {"type": "text", "text": "x" * 50, "prompt_cache_breakpoint": {"mode": "explicit"}}
        ]

    def test_tool_role_message_is_never_marked_even_if_it_clears_the_floor(self) -> None:
        messages = [
            {"role": "tool", "content": "x" * 50, "tool_call_id": "tc1"},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert isinstance(result.messages[0]["content"], str)

    def test_marks_last_and_second_to_last_eligible_boundaries(self) -> None:
        messages = [
            {"role": "user", "content": "x" * 50},
            {"role": "assistant", "content": "x" * 50},
            {"role": "user", "content": "x" * 50},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert isinstance(result.messages[0]["content"], str)
        assert isinstance(result.messages[1]["content"], list)
        assert isinstance(result.messages[2]["content"], list)

    def test_does_not_mutate_input_messages(self) -> None:
        messages = [
            {"role": "system", "content": "x" * 50},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        _apply(_strategy(), CacheableRequest(messages=messages))
        assert messages[0]["content"] == "x" * 50

    def test_none_content_assistant_message_is_never_marked(self) -> None:
        messages = [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "user", "content": "next"},
            {"role": "user", "content": "next"},
        ]
        result = _apply(_strategy(), CacheableRequest(messages=messages))
        assert result.messages[0]["content"] is None


class TestPlanStandsDownOnExistingMarkers:
    def test_prompt_cache_breakpoint_marker_triggers_standdown(self) -> None:
        messages = [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "x", "prompt_cache_breakpoint": {"mode": "explicit"}}
                ],
            }
        ]
        plan = _strategy().plan(CacheableRequest(messages=messages))
        assert plan.enabled is False
        assert plan.reason == "gateway_standdown"

    def test_no_markers_enables_plan(self) -> None:
        plan = _strategy().plan(CacheableRequest(messages=[{"role": "user", "content": "hi"}]))
        assert plan.enabled is True

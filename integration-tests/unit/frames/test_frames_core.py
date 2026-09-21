"""Config validation, the run store, the tool-call guard, the LLM layer."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from frames_testkit import FakeLLM, make_model

from benchmarks.harness.config import ModelSelector, RunConfig, load_config
from benchmarks.harness.errors import ConfigError, ModelNotRegisteredError, ResumeMismatchError
from benchmarks.harness.guard import ToolCallGuard
from benchmarks.harness.llm.cache import CachedLLMClient, LLMCache
from benchmarks.harness.llm.client import ChatMessage, LLMRequest, build_completion_kwargs
from benchmarks.harness.llm.registry import ModelResolver
from benchmarks.harness.models import Question
from benchmarks.datasets.frames.paths import CONFIG_DIR
from benchmarks.harness.store import RunStore

BASE_CONFIG = {
    "run_name": "unit",
    "answerer": {"model": "gpt-5.6-luna", "provider": "openAI"},
    "systems": [{"kind": "closed_book"}],
    "grading": {"primary": {"model": "claude-sonnet-5", "provider": "anthropic"}},
}


def _write(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload))
    return path


class TestConfig:
    @pytest.mark.parametrize("name", ["smoke.yaml", "dev.yaml", "full.yaml"])
    def test_shipped_configs_validate(self, name: str) -> None:
        config = load_config(CONFIG_DIR / name)
        assert {s.kind for s in config.systems} == {"pipeshub", "closed_book", "bm25", "oracle"}

    def test_unknown_keys_are_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, {**BASE_CONFIG, "temperature": 0}))

    def test_duplicate_system_labels_are_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_config(_write(tmp_path, {**BASE_CONFIG, "systems": [{"kind": "bm25"}, {"kind": "bm25"}]}))

    def test_hash_ignores_endpoints_but_not_settings(self) -> None:
        a = RunConfig.model_validate({**BASE_CONFIG, "pipeshub": {"base_url": "http://a"}})
        b = RunConfig.model_validate({**BASE_CONFIG, "pipeshub": {"base_url": "http://b"}})
        c = RunConfig.model_validate({**BASE_CONFIG, "seed": 1})
        assert a.config_hash() == b.config_hash() != c.config_hash()


class TestRunStore:
    def _config(self, **changes: object) -> RunConfig:
        return RunConfig.model_validate({**BASE_CONFIG, **changes})

    def test_append_read_and_latest_wins(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, self._config())
        for answer in ("old", "new"):
            store.append("q.jsonl", Question(id="1", prompt="p", answer=answer, labels=[], gold_refs=[]))
        latest = store.latest_by_key("q.jsonl", Question, lambda q: q.id)
        assert latest["1"].answer == "new"

    def test_truncated_last_line_is_tolerated(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, self._config())
        store.append("q.jsonl", Question(id="1", prompt="p", answer="a", labels=[], gold_refs=[]))
        with store.path("q.jsonl").open("a") as handle:
            handle.write('{"id": 2, "prom')
        assert [q.id for q in store.read("q.jsonl", Question)] == ["1"]

    def test_resume_refuses_a_different_config(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, self._config())
        assert RunStore.resume(tmp_path, store.run_id, self._config()).run_dir == store.run_dir
        with pytest.raises(ResumeMismatchError):
            RunStore.resume(tmp_path, store.run_id, self._config(seed=99))


class TestGuard:
    def test_knowledge_tools_are_allowed(self) -> None:
        guard = ToolCallGuard()
        names = ["knowledgegraph__search", "retrieval__search_internal_knowledge", "knowledgegraph__fetch_record", "fetch_tools"]
        assert guard.violations(names) == []

    @pytest.mark.parametrize("name", ["dynamic__web_search", "dynamic__fetch_url", "run_code", "execute_python", "coding_sandbox__run", "mcp__github"])
    def test_web_and_code_tools_are_violations(self, name: str) -> None:
        assert ToolCallGuard().violations([name, "knowledgegraph__search"]) == [name]

    def test_deny_list_wins_over_allow_list(self) -> None:
        assert ToolCallGuard(allowed=("*",)).classify("dynamic__web_search") == "denied"

    def test_unknown_tools_only_fail_in_strict_mode(self) -> None:
        assert ToolCallGuard(strict=True).violations(["jira__create_issue"]) == ["jira__create_issue"]
        assert ToolCallGuard(strict=False).violations(["jira__create_issue"]) == []


def _request(*, reasoning: bool = False, cacheable: bool = True) -> LLMRequest:
    return LLMRequest(
        model=make_model(reasoning=reasoning), messages=(ChatMessage(role="user", content="hi"),),
        temperature=0.0, max_tokens=64, prompt_version="v1", cacheable=cacheable,
    )


class TestLLMLayer:
    def test_requests_never_carry_tools_or_grounding(self) -> None:
        kwargs = build_completion_kwargs(_request(), "sk-test", 30)
        assert not {"tools", "tool_choice", "functions", "web_search_options"} & set(kwargs)
        assert kwargs["model"] == "anthropic/judge-model"
        assert kwargs["temperature"] == 0.0

    def test_reasoning_models_run_at_temperature_one_with_headroom(self) -> None:
        kwargs = build_completion_kwargs(_request(reasoning=True), "sk-test", 30)
        assert kwargs["temperature"] == 1.0
        assert kwargs["max_tokens"] >= 16_384

    def test_claude_models_without_sampling_params_get_no_temperature(self) -> None:
        request = _request().model_copy(update={"model": make_model("claude-sonnet-5", "anthropic")})
        assert "temperature" not in build_completion_kwargs(request, "sk-test", 30)

    def test_cache_serves_repeats_and_skips_uncacheable(self, tmp_path: Path) -> None:
        inner = FakeLLM(lambda _r: "Decision: TRUE")
        client = CachedLLMClient(inner, LLMCache(tmp_path / "cache.sqlite"))
        first, second = client.complete(_request()), client.complete(_request())
        client.complete(_request(cacheable=False))
        assert (first.cached, second.cached) == (False, True)
        assert len(inner.requests) == 2


REGISTRY = [
    {"provider": "openAI", "modelKey": "k1", "configuration": {"model": "gpt-5.6-luna, gpt-5.4-nano"}, "isReasoning": True},
    {"provider": "anthropic", "modelKey": "k2", "configuration": {"model": "claude-sonnet-5"}, "contextLength": 200000},
]


class TestModelResolver:
    def test_resolves_by_name_within_comma_list(self) -> None:
        model = ModelResolver(lambda: REGISTRY).resolve(ModelSelector(model="gpt-5.4-nano"))
        assert (model.model_key, model.model_name, model.is_reasoning) == ("k1", "gpt-5.4-nano", True)

    def test_resolves_by_key_and_keeps_context_length(self) -> None:
        model = ModelResolver(lambda: REGISTRY).resolve(ModelSelector(model="x", model_key="k2"))
        assert (model.provider, model.context_length) == ("anthropic", 200000)

    def test_unregistered_model_lists_what_is_available(self) -> None:
        with pytest.raises(ModelNotRegisteredError, match="anthropic:claude-sonnet-5"):
            ModelResolver(lambda: REGISTRY).resolve(ModelSelector(model="gemini-3.8-flash", provider="gemini"))


def test_run_parallel_reports_results_as_they_finish() -> None:
    """Results must be handled while the stage runs: waiting for the whole
    batch hides progress and loses everything in flight on a crash."""
    import threading

    from benchmarks.harness.concurrency import run_parallel

    seen: list[int] = []
    first_seen = threading.Event()
    blocked = threading.Event()

    def worker(item: int) -> int:
        if item != 0:
            blocked.wait(timeout=10)  # every other item finishes only later
        return item

    def on_result(_item: int, result: int) -> None:
        seen.append(result)
        if result == 0:
            first_seen.set()

    thread = threading.Thread(target=run_parallel, args=(range(4), worker), kwargs={"workers": 4, "on_result": on_result})
    thread.start()
    assert first_seen.wait(timeout=5), "first result was not reported before the batch finished"
    assert seen == [0]
    blocked.set()
    thread.join(timeout=10)
    assert sorted(seen) == [0, 1, 2, 3]

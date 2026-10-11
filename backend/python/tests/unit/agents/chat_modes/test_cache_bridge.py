"""Unit tests for Semantic Cache bridge integration (Phase 4).

Covers:
- Cacheability checks and all 11 bypass reason codes
- Config hash resolution
- Replay event sequence & shape for both protocols (legacy and agui)
- Fresh runId, servedFromCache, cacheRef markers
- Cross-user hit author privacy (stripping reasoning/parts)
- Shadow mode: concurrent background lookup, zero latency, numerical comparison
- Writes: eligibility preconditions, background store, client disconnect/cancellation
- Privacy: zero query text, answer text, or embeddings in telemetry or logs
- Fail open on 50ms timeout and Redis errors
- Mode 'off' zero-overhead: zero cache calls, zero Redis operations
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.protocol.formatter import LegacyFormatter
from app.agents.agent_loop.stream_bridge import QueueEventSink
from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.cache_bridge import (
    RELATIVE_TIME_PATTERN,
    emit_shadow_comparison,
    evaluate_cacheability,
    replay_cached_turn,
    resolve_config_hash_params,
    should_store_turn,
)
from app.agents.chat_modes.policy import INTERNAL_SEARCH_POLICY
from app.agents.chat_modes.prefetch import PrefetchResult
from app.config.constants.arangodb import Connectors
from app.services.cache.config import SemanticCacheConfig, clear_config_cache
from app.services.cache.semantic_cache_service import (
    PROMPT_TEMPLATE_VERSION,
    CacheLookupResult,
    SemanticCacheConfigHashParams,
    compute_config_hash,
    compute_context_digest,
)
from app.telemetry.event_buffer import event_buffer
from app.utils.chat_helpers import CitationRefMapper


def _patch_connectors() -> tuple[Any, Any]:
    return (
        patch(
            "app.agents.chat_modes.bridge.fetch_user_connector_instances",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.agents.chat_modes.bridge._fetch_available_connectors",
            new=AsyncMock(return_value=[]),
        ),
    )


@pytest.fixture(autouse=True)
def _reset_config_cache() -> Generator[None, None, None]:
    clear_config_cache()
    yield
    clear_config_cache()


def _make_mock_config_service(
    flag_enabled: bool = True,
    kv_value: dict[str, object] | str | None = None,
) -> MagicMock:
    mock_service = MagicMock()

    async def _mock_get_config(key: str, default: object = None, use_cache: bool = True) -> object:
        if "platform/settings" in key:
            return {"featureFlags": {"ENABLE_SEMANTIC_CACHE": flag_enabled}}
        if "semanticCache" in key:
            return kv_value
        return default

    mock_service.get_config = AsyncMock(side_effect=_mock_get_config)
    return mock_service


def _stream_agent(result: object) -> MagicMock:
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal: object, **kwargs: object) -> AsyncGenerator[None, None]:
        agent.last_stream_result = result
        return
        yield  # pragma: no cover

    agent.stream = _fake_stream
    return agent


async def _fake_create(
    self: object, context: object, llm: object, chat_mode: object, *, query: str, model_name: str = "", model_key: object = None
) -> tuple[object, object, object, object]:
    agent = _stream_agent(MagicMock(success=True, error=None, output="Live answer", cancelled=False))
    return agent, MagicMock(constraints=[]), MagicMock(description=query, constraints=[]), []


async def _fake_finalizer_run(
    self: object,
    *,
    agent_success: object,
    agent_error: object,
    event_sink: object,
    agent_output: object = None,
    streamed_answer: str = "",
    reasoning_turns: object = None,
    agent_cancelled: bool = False,
) -> dict[str, object]:
    ans = str(agent_output or "Live answer")
    sink = cast(QueueEventSink, event_sink)
    await sink.write({"event": "complete", "data": {"answer": ans, "confidence": "High"}})
    return {"answer": ans, "confidence": "High"}


def _make_fake_prefetch(
    *,
    final_results: list[dict[str, Any]] | None = None,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    tool_records: list[dict[str, Any]] | None = None,
    query_embedding: list[float] | None = None,
    record_fingerprints: list[Any] | None = None,
    citation_ref_mapper: CitationRefMapper | None = None,
    is_empty: bool = False,
    formatted_context: str = "Sample context",
) -> PrefetchResult:
    return PrefetchResult(
        formatted_context=formatted_context,
        final_results=final_results or [],
        virtual_record_id_to_result=virtual_record_id_to_result or {},
        tool_records=tool_records or [],
        citation_ref_mapper=citation_ref_mapper or CitationRefMapper(),
        is_empty=is_empty,
        query_embedding=query_embedding,
        record_fingerprints=record_fingerprints,
    )


class TestEvaluateCacheability:
    @pytest.fixture
    def base_config(self) -> SemanticCacheConfig:
        return SemanticCacheConfig(mode="serve", share_across_org=True)

    @pytest.fixture
    def base_prefetch(self) -> PrefetchResult:
        return _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "rec-1", "connector": "google_drive"}],
            record_fingerprints=[
                ("rec-1", "1", 100, "meta", "hash")
            ],
            formatted_context="Sample context",
        )

    @pytest.fixture
    def base_config_params(self) -> SemanticCacheConfigHashParams:
        return SemanticCacheConfigHashParams(
            embedding_provider="default",
            embedding_model="default",
            embedding_dimension=3,
            generator_provider="openai",
            generator_model="gpt-4o",
            prompt_template_version=1,
            storage_schema_version="v1",
        )

    def test_bypass_mode_off(
        self,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        cfg = SemanticCacheConfig(mode="off")
        is_cacheable, reason = evaluate_cacheability(
            cfg, "query", {"chatMode": "internal_search"}, base_prefetch, base_config_params
        )
        assert is_cacheable is False
        assert reason == "mode_off"

    def test_bypass_not_internal_search(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        is_cacheable, reason = evaluate_cacheability(
            base_config, "query", {"chatMode": "agent"}, base_prefetch, base_config_params
        )
        assert is_cacheable is False
        assert reason == "not_internal_search"

    def test_bypass_multi_turn(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "query",
            {"chatMode": "internal_search", "previous_conversations": [{"role": "user"}]},
            base_prefetch,
            base_config_params,
        )
        assert is_cacheable is False
        assert reason == "multi_turn"

        is_cacheable_alt, reason_alt = evaluate_cacheability(
            base_config,
            "query",
            {"chatMode": "internal_search", "previousConversations": [{"role": "user"}]},
            base_prefetch,
            base_config_params,
        )
        assert is_cacheable_alt is False
        assert reason_alt == "multi_turn"

    def test_bypass_attachments(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "query",
            {"chatMode": "internal_search", "attachments": [{"name": "file.pdf"}]},
            base_prefetch,
            base_config_params,
        )
        assert is_cacheable is False
        assert reason == "attachments"

    def test_bypass_web_search_flags(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        is_c1, r1 = evaluate_cacheability(
            base_config,
            "query",
            {"chatMode": "internal_search", "include_web_search": True},
            base_prefetch,
            base_config_params,
        )
        assert is_c1 is False
        assert r1 == "web_search_enabled"

        is_c2, r2 = evaluate_cacheability(
            base_config,
            "query",
            {"chatMode": "internal_search", "web_search": True},
            base_prefetch,
            base_config_params,
        )
        assert is_c2 is False
        assert r2 == "web_search_enabled"

    @pytest.mark.parametrize(
        "q",
        [
            "what is the latest revenue?",
            "recent updates on the product",
            "currently active members",
            "what is our current strategy?",
            "how did sales do this quarter?",
            "what happened last week?",
            "metrics for next month",
            "office status today",
            "who visited yesterday",
            "schedule for tomorrow",
            "what is running now?",
            "expenses so far",
            "as of last sprint",
            "year to date metrics",
            "ytd revenue",
            "past 7 days incident count",
            "past 2 weeks tickets",
        ],
    )
    def test_bypass_relative_time_patterns(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
        q: str,
    ) -> None:
        assert RELATIVE_TIME_PATTERN.search(q) is not None
        is_cacheable, reason = evaluate_cacheability(
            base_config, q, {"chatMode": "internal_search"}, base_prefetch, base_config_params
        )
        assert is_cacheable is False
        assert reason == "relative_time"

    def test_bypass_unsupported_language(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        # Guard does not support Arabic/Hebrew (RTL non-Latin scripts)
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "ما هي السياسة الأمنية؟",
            {"chatMode": "internal_search"},
            base_prefetch,
            base_config_params,
        )
        assert is_cacheable is False
        assert reason == "unsupported_language"

    def test_bypass_prefetch_none(
        self,
        base_config: SemanticCacheConfig,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "what is the policy?",
            {"chatMode": "internal_search"},
            None,
            base_config_params,
        )
        assert is_cacheable is False
        assert reason == "prefetch_none"

    def test_bypass_no_records(
        self,
        base_config: SemanticCacheConfig,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        empty_records_prefetch = _make_fake_prefetch(
            query_embedding=[0.1], tool_records=[]
        )
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "what is the policy?",
            {"chatMode": "internal_search"},
            empty_records_prefetch,
            base_config_params,
        )
        assert is_cacheable is False
        assert reason == "no_records"

    def test_bypass_fingerprints_none(
        self,
        base_config: SemanticCacheConfig,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        prefetch_no_fp = _make_fake_prefetch(
            query_embedding=[0.1],
            tool_records=[{"id": "1"}],
            record_fingerprints=None,
        )
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "what is the policy?",
            {"chatMode": "internal_search"},
            prefetch_no_fp,
            base_config_params,
        )
        assert is_cacheable is False
        assert reason == "fingerprints_none"

    def test_bypass_gmail_shared(
        self,
        base_config: SemanticCacheConfig,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        gmail_prefetch = _make_fake_prefetch(
            query_embedding=[0.1],
            tool_records=[{"id": "m1", "connector": Connectors.GOOGLE_MAIL.value}],
            record_fingerprints=[("m1", "1", 100, "meta", "hash")],
        )
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "what is the policy?",
            {"chatMode": "internal_search"},
            gmail_prefetch,
            base_config_params,
        )
        assert is_cacheable is False
        assert reason == "gmail_shared"

    def test_bypass_config_unresolved(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
    ) -> None:
        is_cacheable, reason = evaluate_cacheability(
            base_config, "what is the policy?", {"chatMode": "internal_search"}, base_prefetch, None
        )
        assert is_cacheable is False
        assert reason == "config_unresolved"

    def test_clean_turn_is_cacheable(
        self,
        base_config: SemanticCacheConfig,
        base_prefetch: PrefetchResult,
        base_config_params: SemanticCacheConfigHashParams,
    ) -> None:
        is_cacheable, reason = evaluate_cacheability(
            base_config,
            "what is the mobile device policy?",
            {"chatMode": "internal_search"},
            base_prefetch,
            base_config_params,
        )
        assert is_cacheable is True
        assert reason is None


class TestConfigHashResolution:
    def test_resolve_config_hash_params_complete(self) -> None:
        context = MagicMock()
        context.llm_provider = "openai"
        context.model_name = "gpt-4o"
        context.is_multimodal_llm = False
        context.tool_state = {
            "instructions": "Be concise.",
            "custom_instructions": "Tone: formal.",
            "project_instructions": "Project X.",
        }

        query_info = {
            "chatMode": "internal_search",
            "reasoningEffort": "medium",
            "temperature": 0.2,
            "responseLanguage": "en",
        }

        prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3, 0.4]
        )

        params = resolve_config_hash_params(context, query_info, prefetch)
        assert params is not None
        assert params.embedding_dimension == 4
        assert params.generator_provider == "openai"
        assert params.generator_model == "gpt-4o"
        assert params.reasoning_effort == "medium"
        assert params.sampling_temperature == 0.2
        assert params.response_language == "en"
        assert params.prompt_template_version == 1
        assert params.storage_schema_version == "v1"
        assert params.enrichment_flags == "chatMode=internal_search;multimodal=False"
        assert params.system_prompt_template_hash != ""
        assert params.project_instructions_hash != ""

    def test_resolve_config_hash_unresolved_when_embedding_missing(self) -> None:
        context = MagicMock(llm_provider="openai", model_name="gpt-4o")
        prefetch = _make_fake_prefetch(query_embedding=None)
        params = resolve_config_hash_params(context, {}, prefetch)
        assert params is None

    def test_resolve_config_hash_unresolved_when_generator_model_missing(self) -> None:
        context = MagicMock(llm_provider="openai", model_name="")
        prefetch = _make_fake_prefetch(query_embedding=[0.1, 0.2])
        params = resolve_config_hash_params(context, {}, prefetch)
        assert params is None

    def test_config_hash_identical_when_user_name_email_current_time_differ(self) -> None:
        """Prove config_hash is IDENTICAL for requests differing only in user name, email, current time."""
        prefetch = _make_fake_prefetch(query_embedding=[0.1, 0.2, 0.3, 0.4])

        # Request 1: Alice
        c1 = MagicMock()
        c1.user_id = "user_alice_1"
        c1.user_email = "alice@pipeshub.com"
        c1.llm_provider = "openai"
        c1.model_name = "gpt-4o"
        c1.is_multimodal_llm = False
        c1.tool_state = {
            "instructions": "Base instructions template.",
            "custom_instructions": "Custom team prompt.",
            "project_instructions": "Project Apollo.",
            "user_info": {"fullName": "Alice Smith", "email": "alice@pipeshub.com"},
        }
        q1 = {
            "chatMode": "internal_search",
            "currentTime": "2026-10-11T09:00:00Z",
            "timezone": "America/New_York",
            "reasoningEffort": "medium",
            "temperature": 0.0,
            "responseLanguage": "en",
        }

        # Request 2: Bob (different name, email, current time, timezone)
        c2 = MagicMock()
        c2.user_id = "user_bob_2"
        c2.user_email = "bob.jones@pipeshub.com"
        c2.llm_provider = "openai"
        c2.model_name = "gpt-4o"
        c2.is_multimodal_llm = False
        c2.tool_state = {
            "instructions": "Base instructions template.",
            "custom_instructions": "Custom team prompt.",
            "project_instructions": "Project Apollo.",
            "user_info": {"fullName": "Robert Jones", "email": "bob.jones@pipeshub.com"},
        }
        q2 = {
            "chatMode": "internal_search",
            "currentTime": "2026-10-11T18:45:22Z",
            "timezone": "Europe/London",
            "reasoningEffort": "medium",
            "temperature": 0.0,
            "responseLanguage": "en",
        }

        p1 = resolve_config_hash_params(c1, q1, prefetch)
        p2 = resolve_config_hash_params(c2, q2, prefetch)
        assert p1 is not None and p2 is not None

        hash1 = compute_config_hash(p1)
        hash2 = compute_config_hash(p2)
        assert hash1 == hash2, "config_hash must be IDENTICAL regardless of user identity/time"

    def test_config_hash_diverges_when_config_inputs_change(self) -> None:
        """Prove config_hash is DIFFERENT when prompt, model, provider, effort, embedding, or version change."""

        def _make_ctx_query(
            *,
            instructions: str = "Base prompt",
            custom_instructions: str = "Custom prompt",
            project_instructions: str = "Project Apollo",
            llm_provider: str = "openai",
            model_name: str = "gpt-4o",
            reasoning_effort: str = "medium",
            embedding_dim: int = 4,
            prompt_version: int = PROMPT_TEMPLATE_VERSION,
        ) -> tuple[MagicMock, dict[str, Any], PrefetchResult, int]:
            ctx = MagicMock()
            ctx.llm_provider = llm_provider
            ctx.model_name = model_name
            ctx.is_multimodal_llm = False
            ctx.tool_state = {
                "instructions": instructions,
                "custom_instructions": custom_instructions,
                "project_instructions": project_instructions,
            }
            query = {
                "chatMode": "internal_search",
                "reasoningEffort": reasoning_effort,
            }
            pref = _make_fake_prefetch(query_embedding=[0.1] * embedding_dim)
            return ctx, query, pref, prompt_version

        def _get_hash(ctx: MagicMock, query: dict[str, Any], pref: PrefetchResult) -> str:
            p = resolve_config_hash_params(ctx, query, pref)
            assert p is not None
            return compute_config_hash(p)

        # Base hash
        c_base, q_base, p_base, v_base = _make_ctx_query()
        base_params = resolve_config_hash_params(c_base, q_base, p_base)
        assert base_params is not None
        base_hash = compute_config_hash(base_params)

        # 1. System prompt instructions change
        c, q, p, _ = _make_ctx_query(instructions="Updated base prompt")
        assert _get_hash(c, q, p) != base_hash

        # 2. Custom instructions change
        c, q, p, _ = _make_ctx_query(custom_instructions="Updated custom tone")
        assert _get_hash(c, q, p) != base_hash

        # 3. Project instructions change
        c, q, p, _ = _make_ctx_query(project_instructions="Project Gemini")
        assert _get_hash(c, q, p) != base_hash

        # 4. Generator model changes
        c, q, p, _ = _make_ctx_query(model_name="claude-3-5-sonnet")
        assert _get_hash(c, q, p) != base_hash

        # 5. Generator provider changes
        c, q, p, _ = _make_ctx_query(llm_provider="anthropic")
        assert _get_hash(c, q, p) != base_hash

        # 6. Reasoning effort changes
        c, q, p, _ = _make_ctx_query(reasoning_effort="high")
        assert _get_hash(c, q, p) != base_hash

        # 7. Embedding dimension changes
        c, q, p, _ = _make_ctx_query(embedding_dim=8)
        assert _get_hash(c, q, p) != base_hash

        # 8. PROMPT_TEMPLATE_VERSION bump changes
        bumped_params = SemanticCacheConfigHashParams(
            embedding_provider=base_params.embedding_provider,
            embedding_model=base_params.embedding_model,
            embedding_dimension=base_params.embedding_dimension,
            generator_provider=base_params.generator_provider,
            generator_model=base_params.generator_model,
            prompt_template_version=base_params.prompt_template_version + 1,
            storage_schema_version=base_params.storage_schema_version,
            reasoning_effort=base_params.reasoning_effort,
            sampling_temperature=base_params.sampling_temperature,
            response_language=base_params.response_language,
            system_prompt_template_hash=base_params.system_prompt_template_hash,
            project_instructions_hash=base_params.project_instructions_hash,
            enrichment_flags=base_params.enrichment_flags,
        )
        assert compute_config_hash(bumped_params) != base_hash

        # 9. embedding_provider changes
        p_emb_prov = SemanticCacheConfigHashParams(**{**base_params.__dict__, "embedding_provider": "huggingface"})
        assert compute_config_hash(p_emb_prov) != base_hash

        # 10. embedding_model changes
        p_emb_model = SemanticCacheConfigHashParams(**{**base_params.__dict__, "embedding_model": "text-embedding-3-small"})
        assert compute_config_hash(p_emb_model) != base_hash

        # 11. storage_schema_version changes
        p_schema = SemanticCacheConfigHashParams(**{**base_params.__dict__, "storage_schema_version": "v2"})
        assert compute_config_hash(p_schema) != base_hash

        # 12. sampling_temperature changes
        p_temp = SemanticCacheConfigHashParams(**{**base_params.__dict__, "sampling_temperature": 0.9})
        assert compute_config_hash(p_temp) != base_hash

        # 13. response_language changes
        p_lang = SemanticCacheConfigHashParams(**{**base_params.__dict__, "response_language": "es"})
        assert compute_config_hash(p_lang) != base_hash

        # 14. system_prompt_template_hash changes
        p_sys_hash = SemanticCacheConfigHashParams(**{**base_params.__dict__, "system_prompt_template_hash": "different_sys_hash"})
        assert compute_config_hash(p_sys_hash) != base_hash

        # 15. project_instructions_hash changes
        p_proj_hash = SemanticCacheConfigHashParams(**{**base_params.__dict__, "project_instructions_hash": "different_proj_hash"})
        assert compute_config_hash(p_proj_hash) != base_hash

        # 16. enrichment_flags changes
        p_flags = SemanticCacheConfigHashParams(**{**base_params.__dict__, "enrichment_flags": "chatMode=other"})
        assert compute_config_hash(p_flags) != base_hash


class TestContextDigestFingerprints:
    def test_digest_changes_when_updated_at_alone_changes(self) -> None:
        rec1 = [("rec_1", 100, 90, 1, "hash_1")]
        rec2 = [("rec_1", 101, 90, 1, "hash_1")]
        assert compute_context_digest(rec1) != compute_context_digest(rec2)

    def test_digest_changes_when_source_last_modified_alone_changes(self) -> None:
        rec1 = [("rec_1", 100, 90, 1, "hash_1")]
        rec2 = [("rec_1", 100, 91, 1, "hash_1")]
        assert compute_context_digest(rec1) != compute_context_digest(rec2)

    def test_digest_changes_when_version_alone_changes(self) -> None:
        rec1 = [("rec_1", 100, 90, 1, "hash_1")]
        rec2 = [("rec_1", 100, 90, 2, "hash_1")]
        assert compute_context_digest(rec1) != compute_context_digest(rec2)

    def test_digest_changes_when_md5_checksum_alone_changes(self) -> None:
        rec1 = [("rec_1", 100, 90, 1, "hash_1")]
        rec2 = [("rec_1", 100, 90, 1, "hash_2")]
        assert compute_context_digest(rec1) != compute_context_digest(rec2)

    def test_digest_changes_when_id_alone_changes(self) -> None:
        rec1 = [("rec_1", 100, 90, 1, "hash_1")]
        rec2 = [("rec_2", 100, 90, 1, "hash_1")]
        assert compute_context_digest(rec1) != compute_context_digest(rec2)


class TestReplayCachedTurn:
    @pytest.mark.asyncio
    async def test_replay_legacy_protocol_event_sequence_and_shape(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink = QueueEventSink(q)
        context = AgentContext(
            org_id="org_test",
            user_id="user_test",
            user_email="test@example.com",
            conversation_id="conv-1",
            run_id="run-fresh-123",
            formatter=LegacyFormatter(),
            event_sink=sink,
        )

        lookup_result = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            entry_id="entry-abc",
            completion_data={
                "answer": "This is the cached answer.",
                "citations": [{"id": "c1", "title": "Doc 1"}],
                "confidence": "High",
                "author_user_id": "user_test",
                "reasoning": [{"thought": "internal chain"}],
            },
        )

        replayed = await replay_cached_turn(
            context,
            lookup_result,
            query="what is our policy?",
            current_user_id="user_test",
        )

        assert replayed["servedFromCache"] is True
        assert replayed["runId"] == "run-fresh-123"
        assert replayed["cacheRef"]["entryId"] == "entry-abc"

        # Drain sink queue to verify event wire shapes
        events = []
        while not q.empty():
            events.append(await q.get())

        assert len(events) == 2
        # Event 1: answer_chunk
        assert events[0]["event"] == "answer_chunk"
        assert events[0]["data"]["chunk"] == "This is the cached answer."
        assert events[0]["data"]["accumulated"] == "This is the cached answer."
        assert events[0]["data"]["citations"] == [{"id": "c1", "title": "Doc 1"}]
        assert events[0]["data"]["confidence"] == "High"

        # Event 2: complete
        assert events[1]["event"] == "complete"
        assert events[1]["data"]["answer"] == "This is the cached answer."
        assert events[1]["data"]["servedFromCache"] is True
        assert events[1]["data"]["runId"] == "run-fresh-123"

    @pytest.mark.asyncio
    async def test_replay_agui_protocol_event_sequence_and_shape(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink = QueueEventSink(q)
        context = AgentContext(
            org_id="org_test",
            user_id="user_test",
            user_email="test@example.com",
            conversation_id="conv-1",
            run_id="run-agui-456",
            protocol="agui",
            event_sink=sink,
        )

        lookup_result = CacheLookupResult(
            hit=True,
            kind="semantic",
            score=0.965,
            entry_id="entry-xyz",
            completion_data={
                "answer": "AGUI cached response.",
                "citations": [],
                "confidence": "High",
                "author_user_id": "user_test",
            },
        )

        await replay_cached_turn(
            context,
            lookup_result,
            query="tell me about agui",
            current_user_id="user_test",
        )

        events = []
        while not q.empty():
            events.append(await q.get())

        # AGUI emits: STATE_DELTA, STATE_SNAPSHOT, RUN_FINISHED
        assert len(events) == 3
        assert events[0]["event"] == "STATE_DELTA"
        assert events[0]["data"]["runId"] == "run-agui-456"

        assert events[1]["event"] == "STATE_SNAPSHOT"
        assert events[1]["data"]["runId"] == "run-agui-456"
        assert events[1]["data"]["snapshot"]["servedFromCache"] is True

        assert events[2]["event"] == "RUN_FINISHED"
        assert events[2]["data"]["runId"] == "run-agui-456"
        assert events[2]["data"]["result"]["servedFromCache"] is True

    @pytest.mark.asyncio
    async def test_cross_user_hit_strips_reasoning_and_parts(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink = QueueEventSink(q)
        context = AgentContext(
            org_id="org_test",
            user_id="user_viewer_2",
            user_email="viewer@example.com",
            conversation_id="conv-1",
            run_id="run-fresh",
            formatter=LegacyFormatter(),
            event_sink=sink,
        )

        lookup_result = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            entry_id="entry-1",
            completion_data={
                "answer": "Universal answer.",
                "author_user_id": "user_author_1",
                "reasoning": [{"step": "confidential private thought"}],
                "parts": [{"type": "reasoning", "text": "secret"}],
            },
        )

        replayed = await replay_cached_turn(
            context,
            lookup_result,
            query="common query",
            current_user_id="user_viewer_2",
        )

        # Reasoning and parts must be completely absent from cross-user replay
        assert "reasoning" not in replayed
        assert "parts" not in replayed
        assert replayed["answer"] == "Universal answer."

    @pytest.mark.asyncio
    async def test_same_user_hit_preserves_reasoning(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink = QueueEventSink(q)
        context = AgentContext(
            org_id="org_test",
            user_id="user_same",
            user_email="same@example.com",
            conversation_id="conv-1",
            run_id="run-fresh",
            formatter=LegacyFormatter(),
            event_sink=sink,
        )

        lookup_result = CacheLookupResult(
            hit=True,
            kind="semantic",
            score=0.98,
            entry_id="entry-2",
            completion_data={
                "answer": "My answer.",
                "author_user_id": "user_same",
                "reasoning": [{"step": "my thoughts"}],
            },
        )

        replayed = await replay_cached_turn(
            context,
            lookup_result,
            query="my query",
            current_user_id="user_same",
        )

        assert "reasoning" in replayed
        assert replayed["reasoning"] == [{"step": "my thoughts"}]


class TestShadowComparison:
    def test_emit_shadow_comparison_emits_numbers_only(self) -> None:
        live_completion = {
            "answer": "Answer from live generator.",
            "citations": [{"id": "rec-1"}, {"id": "rec-2"}],
            "confidence": "High",
        }
        cached_completion = {
            "answer": "Answer from live generator.",
            "citations": [{"id": "rec-1"}, {"id": "rec-3"}],
            "confidence": "High",
        }

        lookup_result = CacheLookupResult(
            hit=True,
            kind="semantic",
            score=0.942,
            completion_data=cached_completion,
        )

        start_size = event_buffer.size()
        emit_shadow_comparison(live_completion, lookup_result, org_id="org-shadow")

        assert event_buffer.size() > start_size
        latest = event_buffer._events[-1]
        assert latest["event"] == "semantic_cache_shadow_comparison"
        props = latest["props"]

        assert props["org_id"] == "org-shadow"
        assert props["kind"] == "semantic"
        assert props["score"] == 0.942
        assert props["answer_hash_equal"] is True
        # Intersection {rec-1} (1), Union {rec-1, rec-2, rec-3} (3) -> Jaccard = 1/3 ~ 0.3333
        assert props["citation_jaccard"] == round(1 / 3, 4)
        assert props["confidence_equal"] is True
        assert props["length_ratio"] == 1.0

        # Assert zero text content in props
        for v in props.values():
            assert "Answer from live generator" not in str(v)


class TestWriteEligibility:
    def test_clean_turn_eligible(self) -> None:
        completion_data = {
            "answer": "Valid complete answer.",
            "confidence": "High",
            "succeeded_tool_names": [],
            "failed_tool_names": [],
        }
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is True
        assert reason is None

    def test_cancelled_turn_skipped(self) -> None:
        completion_data = {"answer": "Partial ans...", "confidence": "High"}
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=True, agent_success=True
        )
        assert eligible is False
        assert reason == "cancelled"

        completion_data_stopped = {
            "answer": "Partial ans...",
            "status": "stopped",
            "confidence": "High",
        }
        eligible_stopped, reason_stopped = should_store_turn(
            completion_data_stopped, is_cancelled=False, agent_success=True
        )
        assert eligible_stopped is False
        assert reason_stopped == "cancelled"

    def test_unsuccessful_turn_skipped(self) -> None:
        completion_data = {"answer": "Error text", "confidence": "High"}
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=False
        )
        assert eligible is False
        assert reason == "unsuccessful"

    def test_tool_calls_skipped(self) -> None:
        completion_data = {
            "answer": "Answer using tool.",
            "succeeded_tool_names": ["knowledgegraph__fetch_record"],
        }
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "tool_calls"

    def test_failed_tool_calls_skipped(self) -> None:
        completion_data = {
            "answer": "Answer with failed tool.",
            "failed_tool_names": ["knowledgegraph__fetch_record"],
        }
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "tool_calls"

    def test_tool_names_key_skipped(self) -> None:
        completion_data = {
            "answer": "Answer with tool_names.",
            "tool_names": ["internal_search"],
        }
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "tool_calls"

    def test_tool_calls_key_skipped(self) -> None:
        completion_data = {
            "answer": "Answer with tool_calls.",
            "tool_calls": [{"name": "search"}],
        }
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "tool_calls"

    def test_tool_call_parts_skipped(self) -> None:
        completion_data = {
            "answer": "Answer with tool part.",
            "parts": [{"type": "tool_call", "tool": "retrieval"}],
        }
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "tool_calls"

    def test_fallback_response_skipped(self) -> None:
        completion_data = {
            "answer": "I wasn't able to find...",
            "answerMatchType": "Fallback Response",
        }
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "fallback_response"

    def test_low_confidence_skipped(self) -> None:
        completion_data = {"answer": "Uncertain answer.", "confidence": "Low"}
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "low_confidence"

    def test_empty_answer_skipped(self) -> None:
        completion_data = {"answer": "   \n  ", "confidence": "High"}
        eligible, reason = should_store_turn(
            completion_data, is_cancelled=False, agent_success=True
        )
        assert eligible is False
        assert reason == "empty_answer"


class TestBridgeIntegration:
    @staticmethod
    def _base_kwargs(config_service: object, semantic_cache_service: object = None) -> dict[str, Any]:
        return {
            "query_info": {
                "query": "what is the security policy?",
                "chatMode": "internal_search",
                "filters": {},
                "modelName": "gpt-4o",
            },
            "user_info": {"userId": "user-1", "orgId": "org-1"},
            "llm": MagicMock(),
            "policy": INTERNAL_SEARCH_POLICY,
            "log": logging.getLogger("test_bridge"),
            "retrieval_service": AsyncMock(),
            "graph_provider": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": config_service,
            "semantic_cache_service": semantic_cache_service,
            "model_name": "gpt-4o",
            "llm_provider": "openai",
        }

    @pytest.mark.asyncio
    async def test_serve_mode_exact_hit_replays_and_bypasses_agent(self) -> None:
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            entry_id="eid-123",
            completion_data={
                "answer": "Cached answer for security policy.",
                "citations": [{"id": "c1", "title": "Sec Doc"}],
                "confidence": "High",
                "author_user_id": "user-1",
            },
        )

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "c1", "connector": "google_drive"}],
            record_fingerprints=[("c1", "1", 100, "meta", "hash")],
        )

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # Stream yielded answer_chunk and complete from replay
        assert any("event: complete" in e for e in events)
        complete_event = next(e for e in events if "event: complete" in e)
        data = json.loads(complete_event.split("data: ", 1)[1].strip())
        assert data["servedFromCache"] is True
        assert data["answer"] == "Cached answer for security policy."

    @pytest.mark.asyncio
    async def test_mode_off_makes_zero_cache_calls(self) -> None:
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "off"})

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            _ = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # Verify zero calls to lookup or store
        mock_cache.lookup.assert_not_called()
        mock_cache.store.assert_not_called()

    @pytest.mark.asyncio
    async def test_fail_open_on_lookup_timeout(self) -> None:
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()

        async def _slow_lookup(*args: object, **kwargs: object) -> CacheLookupResult:
            await asyncio.sleep(0.100)  # > 50ms timeout
            return CacheLookupResult(hit=True, kind="exact")

        mock_cache.lookup.side_effect = _slow_lookup

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # Live turn executed and completed despite cache timeout!
        assert any("event: complete" in e for e in events)

    @pytest.mark.asyncio
    async def test_fail_open_on_cache_exception(self) -> None:
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.side_effect = ConnectionError("Redis down")

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        assert any("event: complete" in e for e in events)

    @pytest.mark.asyncio
    async def test_cancellation_never_stores_partial_answer(self) -> None:
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(hit=False, kind="none")

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )
        # Agent cancelled!
        fake_agent = _stream_agent(
            MagicMock(success=True, error=None, output="Partial text...", cancelled=True)
        )

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch(
                "app.agents.chat_modes.bridge.PipesHubAgentFactory.create",
                return_value=(fake_agent, MagicMock(), MagicMock(description="goal", constraints=[]), []),
            ),
            patch(
                "app.agents.chat_modes.bridge.AnswerFinalizer.run",
                return_value={"answer": "Partial text...", "confidence": "High", "status": "stopped"},
            ),
        ):
            _ = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # Give any async background tasks a chance to run
        await asyncio.sleep(0.05)

        # Cache store must NEVER be called for cancelled run!
        mock_cache.store.assert_not_called()

    @pytest.mark.asyncio
    async def test_bridge_shadow_mode_hit_never_serves_cache_runs_live(self) -> None:
        """Item 1a: Shadow mode with a cache hit NEVER serves cached answer. Live agent still runs."""
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            entry_id="entry-shadow-hit",
            completion_data={
                "answer": "CACHED SHADOW ANSWER THAT MUST NEVER BE RETURNED",
                "citations": [],
                "confidence": "High",
            },
        )

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "shadow"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        async def _mock_finalizer_run(
            self_obj: object,
            *,
            agent_success: object,
            agent_error: object,
            event_sink: object,
            agent_output: object = None,
            streamed_answer: str = "",
            reasoning_turns: object = None,
            agent_cancelled: bool = False,
        ) -> dict[str, object]:
            ans = "Live agent response text."
            sink = cast(QueueEventSink, event_sink)
            await sink.write({"event": "complete", "data": {"answer": ans, "confidence": "High"}})
            return {"answer": ans, "confidence": "High", "citations": []}

        live_agent = _stream_agent(
            MagicMock(success=True, error=None, output="Live agent response text.", cancelled=False)
        )

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch(
                "app.agents.chat_modes.bridge.PipesHubAgentFactory.create",
                return_value=(live_agent, MagicMock(), MagicMock(description="goal", constraints=[]), []),
            ),
            patch(
                "app.agents.chat_modes.bridge.AnswerFinalizer.run",
                new=_mock_finalizer_run,
            ),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # 1. Shadow lookup was executed
        mock_cache.lookup.assert_called_once()
        # 2. Emitted stream does NOT contain cached answer, but DOES contain live answer
        stream_text = "".join(events)
        assert "CACHED SHADOW ANSWER THAT MUST NEVER BE RETURNED" not in stream_text
        assert "Live agent response text." in stream_text

    @pytest.mark.asyncio
    async def test_bridge_miss_live_run_calls_background_store_with_correct_keys(self) -> None:
        """Item 1b: Miss => live run => background store called with correct key inputs and completion data."""
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(hit=False, kind="none")

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        live_agent = _stream_agent(
            MagicMock(success=True, error=None, output="Live answer text.", cancelled=False)
        )

        completion_data = {
            "answer": "Live answer text.",
            "confidence": "High",
            "citations": [{"id": "r1"}],
        }

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch(
                "app.agents.chat_modes.bridge.PipesHubAgentFactory.create",
                return_value=(live_agent, MagicMock(), MagicMock(description="goal", constraints=[]), []),
            ),
            patch(
                "app.agents.chat_modes.bridge.AnswerFinalizer.run",
                return_value=completion_data,
            ),
        ):
            _ = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # Await async store task in background
        await asyncio.sleep(0.05)

        mock_cache.store.assert_called_once()
        store_kwargs = mock_cache.store.call_args.kwargs
        assert store_kwargs["org_id"] == "org-1"
        assert store_kwargs["user_id"] == "user-1"
        assert store_kwargs["query"] == "what is the security policy?"
        assert store_kwargs["query_embedding"] == [0.1, 0.2, 0.3]
        assert store_kwargs["context_digest"] == compute_context_digest(fake_prefetch.record_fingerprints)
        assert len(store_kwargs["config_hash"]) == 64
        assert store_kwargs["completion_data"] == completion_data

    @pytest.mark.asyncio
    async def test_bridge_semantic_hit_serve_mode(self) -> None:
        """Item 1c: Semantic hit (not just exact) in serve mode through the bridge."""
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(
            hit=True,
            kind="semantic",
            score=0.985,
            entry_id="sem-entry-456",
            completion_data={
                "answer": "Semantic hit cached answer.",
                "citations": [{"id": "r1", "title": "Doc"}],
                "confidence": "High",
            },
        )

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        stream_called = False

        async def _tracking_stream(goal: object, **kwargs: object) -> AsyncGenerator[None, None]:
            nonlocal stream_called
            stream_called = True
            return
            yield

        live_agent = MagicMock()
        live_agent.stream = _tracking_stream

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch(
                "app.agents.chat_modes.bridge.PipesHubAgentFactory.create",
                return_value=(live_agent, MagicMock(), MagicMock(description="goal", constraints=[]), []),
            ),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        mock_cache.lookup.assert_called_once()
        stream_text = "".join(events)
        assert "Semantic hit cached answer." in stream_text
        assert "servedFromCache" in stream_text
        # Agent stream must never be called on a serve hit
        assert stream_called is False

    @pytest.mark.asyncio
    async def test_bridge_event_allowlist_and_zero_pii_in_telemetry_and_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Item 1d: Full flow with record_event spied: allowlisted property keys, enum/numbers/hashes only, caplog zero PII."""
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(hit=False, kind="none")

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.111, 0.222, 0.333],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        secret_query = "what is the secret super confidential acquisition price?"
        secret_answer = "The secret acquisition price is 42 billion dollars."

        live_agent = _stream_agent(
            MagicMock(success=True, error=None, output=secret_answer, cancelled=False)
        )

        # Allowlist of keys per event type
        event_key_allowlist: dict[str, set[str]] = {
            "semantic_cache_hit": {"kind", "score", "mode", "latency_ms", "org_id"},
            "semantic_cache_miss": {"mode", "latency_ms", "reasons", "org_id"},
            "semantic_cache_bypass": {"reason", "mode", "org_id"},
            "semantic_cache_write": {"org_id", "mode"},
            "semantic_cache_write_skipped": {"reason", "org_id", "mode"},
            "semantic_cache_shadow_comparison": {
                "org_id", "kind", "score", "answer_hash_equal",
                "citation_jaccard", "confidence_equal", "length_ratio",
            },
            "semantic_cache_error": {"op", "org_id"},
        }

        spied_events: list[tuple[str, dict[str, Any]]] = []

        def _spy_record_event(evt_name: str, props: dict[str, Any]) -> None:
            if evt_name.startswith("semantic_cache_"):
                spied_events.append((evt_name, props))

        with (
            caplog.at_level(logging.DEBUG),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.cache_bridge.record_event", side_effect=_spy_record_event),
            patch("app.agents.chat_modes.bridge.record_event", side_effect=_spy_record_event),
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": secret_query},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch(
                "app.agents.chat_modes.bridge.PipesHubAgentFactory.create",
                return_value=(live_agent, MagicMock(), MagicMock(description="goal", constraints=[]), []),
            ),
            patch(
                "app.agents.chat_modes.bridge.AnswerFinalizer.run",
                return_value={"answer": secret_answer, "confidence": "High", "citations": []},
            ),
        ):
            _ = [
                chunk
                async for chunk in run_chat_stream(
                    **{
                        **self._base_kwargs(config_service, semantic_cache_service=mock_cache),
                        "query_info": {**self._base_kwargs(config_service)["query_info"], "query": secret_query},
                    }
                )
            ]
            await asyncio.sleep(0.05)

        assert len(spied_events) > 0

        # Check every spied semantic_cache event against allowlist and value type constraints
        for evt_name, props in spied_events:
            allowed_keys = event_key_allowlist[evt_name]
            for k, v in props.items():
                assert k in allowed_keys, f"Disallowed key '{k}' in event '{evt_name}'"
                # Values must only be numbers, booleans, short enums/reasons, lists of short enums, or IDs/hashes
                if isinstance(v, (int, float, bool)):
                    continue
                if isinstance(v, str):
                    # Must not be secret query or secret answer
                    assert secret_query not in v
                    assert secret_answer not in v
                    assert len(v) <= 128  # short enums, reasons, org_id or sha256 hash only
                elif isinstance(v, list):
                    for item in v:
                        assert isinstance(item, (str, int, float))
                        assert secret_query not in str(item)
                else:
                    pytest.fail(f"Unexpected value type {type(v)} for key '{k}' in event '{evt_name}'")

        # Zero PII in caplog!
        log_text = caplog.text
        assert secret_query not in log_text
        assert secret_answer not in log_text
        assert "0.111" not in log_text
        assert "0.222" not in log_text

    @pytest.mark.asyncio
    async def test_bridge_shadow_lookup_concurrent_and_cancellation_hygiene(self) -> None:
        """Item 1e: Shadow lookup runs concurrently with live run, and unawaited task on cancel leaks no unretrieved exception."""
        sql_patch, slack_patch = _patch_connectors()

        lookup_started = asyncio.Event()
        lookup_allow_finish = asyncio.Event()

        async def _slow_lookup(*args: object, **kwargs: object) -> CacheLookupResult:
            lookup_started.set()
            await lookup_allow_finish.wait()
            return CacheLookupResult(hit=True, kind="exact", score=1.0, completion_data={"answer": "Cached"})

        mock_cache = AsyncMock()
        mock_cache.lookup.side_effect = _slow_lookup

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "shadow"})

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        live_stream_started = asyncio.Event()

        fake_agent = MagicMock()
        fake_agent.last_stream_result = MagicMock(success=True, error=None, output="Live", cancelled=False)

        async def _fake_stream(goal: object, **kwargs: object) -> AsyncGenerator[None, None]:
            # Live stream starts while shadow lookup is still in-flight
            live_stream_started.set()
            assert lookup_started.is_set(), "Shadow lookup must have started concurrently"
            assert not lookup_allow_finish.is_set(), "Shadow lookup must NOT have finished yet"
            return
            yield

        fake_agent.stream = _fake_stream

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch(
                "app.agents.chat_modes.bridge.PipesHubAgentFactory.create",
                return_value=(fake_agent, MagicMock(), MagicMock(description="goal", constraints=[]), []),
            ),
            patch(
                "app.agents.chat_modes.bridge.AnswerFinalizer.run",
                return_value={"answer": "Live", "confidence": "High", "citations": []},
            ),
        ):
            # Start generator
            stream_gen = run_chat_stream(
                **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
            )
            # Fetch first chunk to start processing
            first_chunk_task = asyncio.create_task(stream_gen.__anext__())
            await live_stream_started.wait()

            # Now release the slow lookup so it can complete
            lookup_allow_finish.set()
            _ = await first_chunk_task

            # Drain remainder
            async for _ in stream_gen:
                pass


class TestGoldenReplayParity:
    """Item 3: Golden replay test capturing live turn's event sequence and comparing against replay."""

    @pytest.mark.asyncio
    async def test_golden_replay_legacy_protocol(self) -> None:
        q_live: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink_live = QueueEventSink(q_live)
        ctx_live = AgentContext(
            org_id="org-test",
            user_id="user-1",
            user_email="user1@pipeshub.com",
            conversation_id="conv-golden",
            run_id="run-live-1",
            protocol="legacy",
            event_sink=sink_live,
        )

        full_completion_payload = {
            "answer": "This is the authoritative golden response with details.",
            "citations": [
                {
                    "id": "c1",
                    "title": "Golden Documentation",
                    "url": "https://docs.pipeshub.com/guide",
                    "metadata": {"connector": "confluence"},
                }
            ],
            "confidence": "High",
            "followUpQuestions": [
                {"question": "How do I configure this?", "confidence": 0.95, "reasoning": "Next logical step"}
            ],
            "modelInfo": {"model": "gpt-4o", "provider": "openai"},
            "author_user_id": "user-1",
            "referenceData": [{"name": "config_doc", "id": "ref-100"}],
        }

        # 1. Produce live event sequence
        for evt in ctx_live.formatter.answer_delta(
            ctx_live,
            chunk=full_completion_payload["answer"],
            accumulated=full_completion_payload["answer"],
            citations=full_completion_payload["citations"],
            confidence=full_completion_payload["confidence"],
            raw_length=len(full_completion_payload["answer"]),
        ):
            await sink_live.write(evt)

        for evt in ctx_live.formatter.answer_final(ctx_live, completion_data=full_completion_payload):
            await sink_live.write(evt)

        live_events: list[dict[str, Any]] = []
        while not q_live.empty():
            live_events.append(await q_live.get())

        # 2. Store into cache representation & replay
        q_replay: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink_replay = QueueEventSink(q_replay)
        ctx_replay = AgentContext(
            org_id="org-test",
            user_id="user-1",
            user_email="user1@pipeshub.com",
            conversation_id="conv-golden",
            run_id="run-replay-2",  # fresh runId
            protocol="legacy",
            event_sink=sink_replay,
        )

        lookup = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            entry_id="golden-entry-99",
            completion_data=dict(full_completion_payload),
        )

        replayed_payload = await replay_cached_turn(
            ctx_replay,
            lookup,
            query="golden query",
            current_user_id="user-1",
        )

        replay_events: list[dict[str, Any]] = []
        while not q_replay.empty():
            replay_events.append(await q_replay.get())

        assert len(live_events) == len(replay_events) == 2

        # Verify survival of modelInfo, followUpQuestions, referenceData
        assert replayed_payload["followUpQuestions"] == full_completion_payload["followUpQuestions"]
        assert replayed_payload["modelInfo"] == full_completion_payload["modelInfo"]
        assert replayed_payload["referenceData"] == full_completion_payload["referenceData"]
        assert replayed_payload["servedFromCache"] is True
        assert replayed_payload["cacheRef"] == {"entryId": "golden-entry-99"}

        # Compare event structures after normalizing transient fields
        def _normalize_event(evt: dict[str, Any]) -> dict[str, Any]:
            d = dict(evt)
            data = dict(d.get("data") or {})
            data.pop("runId", None)
            data.pop("timestamp", None)
            data.pop("servedFromCache", None)
            data.pop("cacheRef", None)
            d["data"] = data
            return d

        for le, re in zip(live_events, replay_events, strict=True):
            assert le["event"] == re["event"]
            assert _normalize_event(le) == _normalize_event(re)

    @pytest.mark.asyncio
    async def test_golden_replay_agui_protocol(self) -> None:
        q_live: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink_live = QueueEventSink(q_live)
        ctx_live = AgentContext(
            org_id="org-test",
            user_id="user-1",
            user_email="user1@pipeshub.com",
            conversation_id="conv-golden",
            run_id="run-live-agui",
            protocol="agui",
            event_sink=sink_live,
        )

        full_completion_payload = {
            "answer": "AGUI authoritative response text.",
            "citations": [],
            "confidence": "High",
            "followUpQuestions": [{"question": "Next steps?", "confidence": 0.88}],
            "modelInfo": {"model": "claude-3-5-sonnet", "provider": "anthropic"},
            "author_user_id": "user-1",
        }

        for evt in ctx_live.formatter.answer_delta(
            ctx_live,
            chunk=full_completion_payload["answer"],
            accumulated=full_completion_payload["answer"],
            citations=[],
            confidence=full_completion_payload["confidence"],
            raw_length=len(full_completion_payload["answer"]),
        ):
            await sink_live.write(evt)

        for evt in ctx_live.formatter.answer_final(ctx_live, completion_data=full_completion_payload):
            await sink_live.write(evt)

        live_events: list[dict[str, Any]] = []
        while not q_live.empty():
            live_events.append(await q_live.get())

        # Replay in AGUI
        q_replay: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        sink_replay = QueueEventSink(q_replay)
        ctx_replay = AgentContext(
            org_id="org-test",
            user_id="user-1",
            user_email="user1@pipeshub.com",
            conversation_id="conv-golden",
            run_id="run-replay-agui",
            protocol="agui",
            event_sink=sink_replay,
        )

        lookup = CacheLookupResult(
            hit=True,
            kind="semantic",
            score=0.97,
            entry_id="golden-agui-entry",
            completion_data=dict(full_completion_payload),
        )

        replayed_payload = await replay_cached_turn(
            ctx_replay,
            lookup,
            query="golden query",
            current_user_id="user-1",
        )

        replay_events: list[dict[str, Any]] = []
        while not q_replay.empty():
            replay_events.append(await q_replay.get())

        assert len(live_events) == len(replay_events) == 3
        assert replayed_payload["followUpQuestions"] == full_completion_payload["followUpQuestions"]
        assert replayed_payload["modelInfo"] == full_completion_payload["modelInfo"]


class TestFailOpenRobustness:
    """Tests covering Item 1, 2, 3, 4, 5, 6 for fail-open and boundary guarantees."""

    @staticmethod
    def _base_kwargs(config_service: object, semantic_cache_service: object = None) -> dict[str, Any]:
        return {
            "query_info": {
                "query": "what is the security policy?",
                "chatMode": "internal_search",
                "filters": {},
                "modelName": "gpt-4o",
            },
            "user_info": {"userId": "user-1", "orgId": "org-1"},
            "llm": MagicMock(),
            "policy": INTERNAL_SEARCH_POLICY,
            "log": logging.getLogger("test_bridge"),
            "retrieval_service": AsyncMock(),
            "graph_provider": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": config_service,
            "semantic_cache_service": semantic_cache_service,
            "model_name": "gpt-4o",
            "llm_provider": "openai",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("patch_target", "exception_to_raise", "expected_op"),
        [
            ("app.agents.chat_modes.bridge.resolve_config_hash_params", RuntimeError("hash fail"), "pre_llm_cache"),
            ("app.agents.chat_modes.bridge.evaluate_cacheability", ValueError("bad eval"), "pre_llm_cache"),
            ("app.agents.chat_modes.bridge.compute_context_digest", KeyError("digest fail"), "pre_llm_cache"),
            ("app.agents.chat_modes.bridge.get_semantic_cache_config", TimeoutError("kv timeout"), "pre_llm_cache"),
            ("app.agents.chat_modes.bridge.should_store_turn", TypeError("store check error"), "post_stream_cache"),
        ],
    )
    async def test_fail_open_when_component_raises(
        self,
        patch_target: str,
        exception_to_raise: Exception,
        expected_op: str,
    ) -> None:
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(hit=False, kind="none")
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        recorded_errors: list[dict[str, Any]] = []

        def _capture_event(name: str, payload: dict[str, Any]) -> None:
            if name == "semantic_cache_error":
                recorded_errors.append(payload)

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch("app.agents.chat_modes.bridge.record_event", side_effect=_capture_event),
            patch(patch_target, side_effect=exception_to_raise),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # Live answer still streamed normally and completed!
        assert any("event: complete" in e for e in events)
        # No error SSE frame emitted to client stream!
        assert not any("event: error" in e for e in events)
        # Error telemetry recorded the op
        assert any(e.get("op") == expected_op for e in recorded_errors)

    @pytest.mark.asyncio
    async def test_fail_open_when_shadow_create_task_raises(self) -> None:
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(
            hit=False, kind="none", completion_data={"answer": "cached", "confidence": "High"}
        )
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "shadow"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "r1", "connector": "google_drive"}],
            record_fingerprints=[("r1", "1", 100, "meta", "hash")],
        )

        real_create_task = asyncio.create_task

        def _exploding_create_task(coro: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            coro_name = getattr(coro, "__name__", "") or str(coro)
            if "lookup" in coro_name:
                coro.close()
                raise RuntimeError("cannot create shadow task")
            return real_create_task(coro, *args, **kwargs)

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch("app.agents.chat_modes.bridge.asyncio.create_task", side_effect=_exploding_create_task),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        assert any("event: complete" in e for e in events)
        assert not any("event: error" in e for e in events)

    @pytest.mark.asyncio
    async def test_mode_off_factory_create_receives_exact_original_arguments(self) -> None:
        """Item 2: Mode off must be byte-identical and factory.create receives exact original model_name."""
        sql_patch, slack_patch = _patch_connectors()
        config_service = _make_mock_config_service(flag_enabled=False)
        captured_create_kwargs: dict[str, Any] = {}

        async def _spy_create(
            self: object,
            context: object,
            llm: object,
            chat_mode: object,
            *,
            query: str,
            model_name: str = "",
            model_key: object = None,
            **kwargs: Any,  # noqa: ANN401
        ) -> tuple[object, object, object, object]:
            captured_create_kwargs["model_name"] = model_name
            return await _fake_create(self, context, llm, chat_mode, query=query, model_name=model_name, model_key=model_key, **kwargs)

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "q"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=None),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_spy_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            base_kwargs = self._base_kwargs(config_service)
            base_kwargs["model_name"] = "claude-3-5-sonnet"
            _ = [chunk async for chunk in run_chat_stream(**base_kwargs)]

        assert captured_create_kwargs.get("model_name") == "claude-3-5-sonnet"

    @pytest.mark.asyncio
    async def test_clean_turn_with_real_answer_finalizer_calls_store(self) -> None:
        """Item 3: Prove clean turn with real AnswerFinalizer calls store with answer and citations."""
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(hit=False, kind="none")
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        ref_mapper = CitationRefMapper()
        ref = ref_mapper.get_or_create_ref("https://example.com/security")
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            final_results=[
                {
                    "block_web_url": "https://example.com/security",
                    "content": "Security doc content",
                    "metadata": {"title": "Security Doc"},
                }
            ],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
            citation_ref_mapper=ref_mapper,
        )

        async def _real_agent_create(
            self: object,
            context: object,
            llm: object,
            chat_mode: object,
            *,
            query: str,
            model_name: str = "",
            model_key: object = None,
            **kwargs: Any,  # noqa: ANN401
        ) -> tuple[object, object, object, object]:
            mock_agent = MagicMock()
            mock_agent.last_stream_result = MagicMock(
                success=True,
                error=None,
                output=f"Final answer from live LLM with [Security Doc]({ref}).",
                cancelled=False,
            )

            async def _empty_stream(goal: Any) -> AsyncGenerator[Any, None]:  # noqa: ANN401
                if False:
                    yield None

            mock_agent.stream = _empty_stream
            goal = MagicMock()
            goal.description = query
            goal.constraints = []
            runtime = MagicMock()
            return mock_agent, runtime, goal, []

        def _side_effect_prefetch(*args: Any, **kwargs: Any) -> PrefetchResult:  # noqa: ANN401
            rm = kwargs.get("ref_mapper")
            if rm is not None:
                rm.get_or_create_ref("https://example.com/security")
            return fake_prefetch

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", side_effect=_side_effect_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_real_agent_create),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]
            await asyncio.sleep(0.05)

        assert any("event: complete" in e for e in events)
        assert mock_cache.store.called
        call_kwargs = mock_cache.store.call_args.kwargs
        assert "Final answer" in call_kwargs["completion_data"]["answer"]
        assert len(call_kwargs["completion_data"]["citations"]) > 0

    @pytest.mark.asyncio
    async def test_hung_shadow_lookup_times_out_and_does_not_delay_stream(self) -> None:
        """Item 4: A hung shadow lookup (e.g. 5s) times out at 100ms and does not delay stream completion."""
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()

        async def _hanging_lookup(**kwargs: Any) -> CacheLookupResult:  # noqa: ANN401
            await asyncio.sleep(5.0)
            return CacheLookupResult(hit=True, kind="exact", score=1.0)

        mock_cache.lookup = _hanging_lookup
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "shadow"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
        )

        recorded_errors: list[dict[str, Any]] = []

        def _capture_event(name: str, payload: dict[str, Any]) -> None:
            if name == "semantic_cache_error":
                recorded_errors.append(payload)

        start = asyncio.get_event_loop().time()
        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch("app.agents.chat_modes.bridge.record_event", side_effect=_capture_event),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0
        assert any("event: complete" in e for e in events)
        assert any(e.get("op") == "shadow_timeout" for e in recorded_errors)

    @pytest.mark.asyncio
    async def test_container_wiring_and_service_unavailable_bypass(self) -> None:
        """Item 5: load_semantic_cache_service matches sibling pattern, warns once, and bypasses when None."""
        from app.api.routes.chatbot import load_semantic_cache_service

        logger = logging.getLogger("test_wiring")
        empty_container = MagicMock()
        del empty_container.semantic_cache_service

        with patch.object(logger, "warning") as mock_warn:
            res = await load_semantic_cache_service(empty_container, logger)
            assert res is None
            assert mock_warn.called

        sql_patch, slack_patch = _patch_connectors()
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
        )

        recorded_bypasses: list[dict[str, Any]] = []

        def _capture_event(name: str, payload: dict[str, Any]) -> None:
            if name == "semantic_cache_bypass":
                recorded_bypasses.append(payload)

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch("app.agents.chat_modes.bridge.record_event", side_effect=_capture_event),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=None)
                )
            ]

        assert any("event: complete" in e for e in events)
        assert any(b.get("reason") == "service_unavailable" for b in recorded_bypasses)

    @pytest.mark.asyncio
    async def test_background_store_task_tracking_and_exception_counting(self) -> None:
        """Item 6: Background store tasks held in strong refs, exceptions counted, not raised."""
        from app.agents.chat_modes.bridge import get_background_tasks

        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(hit=False, kind="none")
        mock_cache.store.side_effect = ConnectionError("Redis crashed")

        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
        )

        recorded_errors: list[dict[str, Any]] = []

        def _capture_event(name: str, payload: dict[str, Any]) -> None:
            if name == "semantic_cache_error":
                recorded_errors.append(payload)

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch("app.agents.chat_modes.bridge.record_event", side_effect=_capture_event),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]
            await asyncio.sleep(0.05)

        assert any("event: complete" in e for e in events)
        assert any(e.get("op") == "store" for e in recorded_errors)
        assert len(get_background_tasks()) == 0

    @pytest.mark.asyncio
    async def test_real_cache_hit_through_bridge_legacy_protocol(self) -> None:
        """Item 1: Real cache hit through bridge.py with REAL replay_cached_turn (no mock) on legacy protocol."""
        sql_patch, slack_patch = _patch_connectors()
        cached_payload = {
            "answer": "This is the authoritative cached answer.",
            "confidence": "High",
            "citations": [{"id": "c1", "title": "Doc 1", "url": "https://example.com"}],
            "modelInfo": {"model": "gpt-4o", "provider": "openai"},
            "author_user_id": "user-1",
        }
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            entry_id="golden-entry-1",
            completion_data=dict(cached_payload),
        )
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
        )

        agent_stream_called = False

        async def _spy_create(*args: Any, **kwargs: Any) -> tuple[object, object, object, object]:  # noqa: ANN401
            agent = MagicMock()
            async def _stream(goal: Any) -> AsyncGenerator[Any, None]:  # noqa: ANN401
                nonlocal agent_stream_called
                agent_stream_called = True
                yield "live agent chunk"
            agent.stream = _stream
            goal = MagicMock(description="goal", constraints=[])
            return agent, MagicMock(), goal, []

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_spy_create),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache),
                    protocol="legacy",
                )
            ]

        # 1. Live agent was NEVER called
        assert not agent_stream_called
        # 2. Exact event sequence for legacy wire protocol
        assert len(events) == 2
        lines0 = events[0].strip().split("\n")
        lines1 = events[1].strip().split("\n")
        assert lines0[0] == "event: answer_chunk"
        assert lines1[0] == "event: complete"
        evt0_data = json.loads(lines0[1].replace("data: ", ""))
        evt1_data = json.loads(lines1[1].replace("data: ", ""))
        assert evt0_data["chunk"] == "This is the authoritative cached answer."
        assert evt0_data["confidence"] == "High"
        assert evt1_data["answer"] == "This is the authoritative cached answer."
        assert evt1_data["servedFromCache"] is True
        assert evt1_data["cacheRef"] == {"entryId": "golden-entry-1"}

    @pytest.mark.asyncio
    async def test_real_cache_hit_through_bridge_agui_protocol(self) -> None:
        """Item 1: Real cache hit through bridge.py with REAL replay_cached_turn (no mock) on agui protocol."""
        sql_patch, slack_patch = _patch_connectors()
        cached_payload = {
            "answer": "AGUI authoritative cached response.",
            "confidence": "High",
            "citations": [],
            "modelInfo": {"model": "gpt-4o", "provider": "openai"},
            "author_user_id": "user-1",
        }
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            entry_id="golden-agui-entry",
            completion_data=dict(cached_payload),
        )
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
        )

        agent_stream_called = False

        async def _spy_create(*args: Any, **kwargs: Any) -> tuple[object, object, object, object]:  # noqa: ANN401
            agent = MagicMock()
            async def _stream(goal: Any) -> AsyncGenerator[Any, None]:  # noqa: ANN401
                nonlocal agent_stream_called
                agent_stream_called = True
                yield "live agent chunk"
            agent.stream = _stream
            goal = MagicMock(description="goal", constraints=[])
            return agent, MagicMock(), goal, []

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_spy_create),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache),
                    protocol="agui",
                )
            ]

        # 1. Live agent was NEVER called
        assert not agent_stream_called
        # 2. Exact event sequence for AGUI wire protocol
        cache_events = [e for e in events if not e.startswith(": ping") and not e.startswith("event: ping")]
        event_names = [e.split("\n")[0].replace("event: ", "") for e in cache_events]
        assert event_names == ["STATE_DELTA", "STATE_SNAPSHOT", "RUN_FINISHED"]
        delta_data = json.loads(cache_events[0].split("data: ")[1])
        assert any(
            d.get("path") == "/normalizedAnswer" and d.get("value") == "AGUI authoritative cached response."
            for d in delta_data.get("delta", [])
        )
        snapshot_data = json.loads(cache_events[1].split("data: ")[1])
        assert snapshot_data["snapshot"]["answer"] == "AGUI authoritative cached response."
        assert snapshot_data["snapshot"]["servedFromCache"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("bad_data_value", "expected_desc"),
        [
            ("not valid json {[[[", "corrupt_data_key_json"),
            (json.dumps({"citations": [], "confidence": "High"}), "missing_answer"),
            (json.dumps({"schema_version": "v999", "answer": "old answer"}), "wrong_schema_version"),
        ],
    )
    async def test_corrupt_or_incompatible_entry_fails_open_live_agent_runs(
        self,
        bad_data_value: str,
        expected_desc: str,
    ) -> None:
        """Item 2: Corrupt or incompatible cache entries must fail open.

        Tests: corrupt data-key JSON, missing 'answer', wrong schema version
        => the live agent runs, no error event emitted.
        """
        import hashlib

        import fakeredis.aioredis

        from app.services.cache.semantic_cache_service import (
            SemanticCacheService,
            normalize_for_key,
        )

        sql_patch, slack_patch = _patch_connectors()
        fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        service = SemanticCacheService(
            config_service=config_service,
            redis_client=fake_redis,
        )

        query = "what is the security policy?"
        norm_query = normalize_for_key(query)
        entry_id = hashlib.sha256(norm_query.encode("utf-8")).hexdigest()[:16]

        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
        )

        agent_created = False

        async def _spy_fake_create(*args: Any, **kwargs: Any) -> tuple[object, object, object, object]:  # noqa: ANN401
            nonlocal agent_created
            agent_created = True
            return await _fake_create(*args, **kwargs)

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": query},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_spy_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            # Seed the cache with a valid store first
            _ = await service.store(
                org_id="org-1",
                user_id="user-1",
                query=query,
                query_embedding=[0.1, 0.2, 0.3],
                completion_data={"answer": "original cached", "confidence": "High"},
                context_digest=compute_context_digest(fake_prefetch.record_fingerprints or []),
                config_hash="test_config_hash",
            )
            # Patch compute_config_hash to match our seeded hash
            with patch("app.agents.chat_modes.bridge.compute_config_hash", return_value="test_config_hash"):
                # Now corrupt the data key in Redis
                data_key = f"pipeshub:semcache:v1:{{org-1}}:data:{entry_id}"
                await fake_redis.set(data_key, bad_data_value)

                events = [
                    chunk
                    async for chunk in run_chat_stream(
                        **self._base_kwargs(config_service, semantic_cache_service=service)
                    )
                ]

        # 1. The live agent runs!
        assert agent_created, f"Live agent did not run for {expected_desc}"
        # 2. Live completion produced
        assert any("event: complete" in e for e in events)
        # 3. No error SSE frame emitted to client!
        assert not any("event: error" in e for e in events), f"Unexpected error event for {expected_desc}"
        # 4. Corrupt data key was evicted from Redis
        assert await fake_redis.get(data_key) is None

    @pytest.mark.asyncio
    async def test_failure_after_first_write_emits_one_error_event_and_no_live_run(self) -> None:
        """Item 2: Failure after the first write => exactly one error event, no live run."""
        sql_patch, slack_patch = _patch_connectors()
        mock_cache = AsyncMock()
        mock_cache.lookup.return_value = CacheLookupResult(
            hit=True,
            kind="exact",
            score=1.0,
            completion_data={
                "answer": "cached answer",
                "confidence": "High",
                "citations": [],
            },
        )
        config_service = _make_mock_config_service(flag_enabled=True, kv_value={"mode": "serve"})
        fake_prefetch = _make_fake_prefetch(
            query_embedding=[0.1, 0.2, 0.3],
            tool_records=[{"id": "doc-1", "connector": "jira"}],
            record_fingerprints=[("doc-1", "1", 100, "meta", "hash")],
        )

        agent_stream_called = False

        async def _spy_create(*args: Any, **kwargs: Any) -> tuple[object, object, object, object]:  # noqa: ANN401
            agent = MagicMock()
            async def _stream(goal: Any) -> AsyncGenerator[Any, None]:  # noqa: ANN401
                nonlocal agent_stream_called
                agent_stream_called = True
                yield "live agent chunk"
            agent.stream = _stream
            goal = MagicMock(description="goal", constraints=[])
            return agent, MagicMock(), goal, []

        real_sink_write = QueueEventSink.write
        write_call_count = 0

        async def _failing_sink_write(self: QueueEventSink, event: object) -> None:
            nonlocal write_call_count
            write_call_count += 1
            # 1st write succeeds (answer_delta), 2nd write fails (after first write)
            if write_call_count == 2:
                raise RuntimeError("Sink failure after first write")
            await real_sink_write(self, event)

        with (
            sql_patch,
            slack_patch,
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "what is the security policy?"},
            ),
            patch("app.agents.chat_modes.bridge.prefetch_retrieval", return_value=fake_prefetch),
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_spy_create),
            patch.object(QueueEventSink, "write", new=_failing_sink_write),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(
                    **self._base_kwargs(config_service, semantic_cache_service=mock_cache)
                )
            ]

        # 1. Live agent was NEVER called
        assert not agent_stream_called
        # 2. Exactly one terminal/error event is emitted
        error_events = [e for e in events if "event: error" in e]
        assert len(error_events) == 1
        # 3. No duplicate completion event
        assert not any("event: complete" in e for e in events)


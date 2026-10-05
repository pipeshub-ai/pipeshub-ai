"""The resolver's merge call and winner check:

- The merge call's timeout covers the provider call, not the wait for
  the shared model slot, so a busy slot does not turn names into duplicates.
- A failed winner check is reported as an error, not as stale winners.
- A call that returns nothing drops the cached model handle.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityRecord, EntityType
from app.modules.entity_resolution import resolver as resolver_module
from app.modules.entity_resolution.models import MergeDecision, MergeDecisions
from app.modules.entity_resolution.normalizer import normalize_name

TOPICS = CollectionNames.TOPICS.value


async def _seed_winner(fake_graph, fake_store, key: str = "k-bug", name: str = "Bug bash testing") -> None:
    await fake_store.upsert_entities_batch([
        EntityRecord(entity_id=key, entity_type=EntityType.TOPIC, name=name, org_id="acme"),
    ])
    fake_graph.nodes[(TOPICS, key)] = {
        "name": name, "normalizedName": normalize_name(name), "orgId": "acme", "aliases": [],
    }


class TestMergeCallTimeout:
    async def test_timeout_is_passed_per_call_not_around_the_slot_wait(
        self, make_resolver, fake_graph, fake_store, metadata_factory, ctx_factory,
    ) -> None:
        await _seed_winner(fake_graph, fake_store)
        answer = MergeDecisions(decisions=[MergeDecision(i=0, same=True, target="k-bug")])

        async def _slow_slot_then_answer(*_args: object, **_kwargs: object) -> MergeDecisions:
            # Stands in for a long wait for the shared indexing slot.
            await asyncio.sleep(0.2)
            return answer

        invoke = AsyncMock(side_effect=_slow_slot_then_answer)
        with patch.object(resolver_module, "MERGE_CALL_TIMEOUT_SECONDS", 0.05), \
             patch.object(resolver_module, "invoke_with_structured_output_and_reflection", invoke), \
             patch.object(resolver_module, "get_llm_for_role", AsyncMock(return_value=(MagicMock(), {}))):
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
            )
        assert invoke.await_args.kwargs["call_timeout"] == 0.05
        assert resolution.stats.model_failures == 0
        assert resolution.entries[(TOPICS, "bug bash testing")].key == "k-bug"


class TestWinnerCheckErrors:
    async def test_lookup_is_asked_to_raise(
        self, make_resolver, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model,
    ) -> None:
        await _seed_winner(fake_graph, fake_store)
        scripted_model()
        await make_resolver().resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash session"]))
        )
        assert fake_graph.node_lookup_kwargs
        assert all(kw.get("raise_on_error") is True for kw in fake_graph.node_lookup_kwargs)

    async def test_failed_check_is_an_error_not_stale_winners(
        self, make_resolver, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model,
    ) -> None:
        await _seed_winner(fake_graph, fake_store)
        fake_graph.fail_node_lookup = True
        scripted_model()
        with patch.object(resolver_module.metrics, "record_fallback") as fallback:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash session"]))
            )
        reasons = [c.args[0] for c in fallback.call_args_list]
        assert "winner_check_error" in reasons
        assert "stale_winner" not in reasons
        assert resolution.stats.stale_winners == 0
        assert resolution.stats.winners_offered == 0


class TestModelHandleReset:
    async def test_empty_response_drops_the_cached_model(
        self, make_resolver, fake_graph, fake_store, metadata_factory, ctx_factory,
    ) -> None:
        await _seed_winner(fake_graph, fake_store)
        get_llm = AsyncMock(return_value=(MagicMock(), {}))
        with patch.object(resolver_module, "invoke_with_structured_output_and_reflection",
                          AsyncMock(return_value=None)), \
             patch.object(resolver_module, "get_llm_for_role", get_llm):
            resolver = make_resolver()
            await resolver.resolve(ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash session"])))
            assert resolver._llm is None
            await resolver.resolve(ctx_factory("r2", "acme", metadata_factory(topics=["Bug bash day"])))
        assert get_llm.await_count == 2

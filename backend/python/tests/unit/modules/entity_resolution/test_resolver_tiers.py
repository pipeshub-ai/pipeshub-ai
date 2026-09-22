"""EntityResolver tiers: name collection, exact match, winner lookup, model call."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.models import ResolutionMode

TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value
SUB1 = CollectionNames.SUBCATEGORIES1.value
SUB2 = CollectionNames.SUBCATEGORIES2.value
LANGUAGES = CollectionNames.LANGUAGES.value


def _seed(fake_graph, collection, org, name, key=None, aliases=()) -> str:
    from app.modules.entity_resolution.normalizer import normalize_name

    key = key or taxonomy_node_key(org, collection, normalize_name(name))
    fake_graph.nodes[(collection, key)] = {
        "name": name, "normalizedName": normalize_name(name), "orgId": org,
        "aliases": list(aliases),
    }
    return key


def _find_calls(fake_graph) -> list:
    return [args for name, args in fake_graph.calls if name == "find_taxonomy_nodes"]


class TestCollection:
    async def test_names_batched_per_collection_scoped_by_org(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(categories=["Cat"], topics=["Alpha", "Beta"], languages=["English"])
        await resolver.resolve(ctx_factory("r1", "acme", meta))
        calls = _find_calls(fake_graph)
        assert (CATEGORIES, "acme", ["cat"]) in calls
        assert (TOPICS, "acme", ["alpha", "beta"]) in calls
        assert (LANGUAGES, "acme", ["english"]) in calls
        assert all(org == "acme" for _c, org, _n in calls)

    async def test_in_record_duplicates_collapse_first_spelling_wins(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(topics=["Bug bash testing", "bug bash testing", " BUG BASH TESTING "])
        ctx = ctx_factory("r1", "acme", meta)
        resolution = await resolver.resolve(ctx)
        assert resolution.stats.names_deduped == 2
        assert meta.topics == ["Bug bash testing"]
        (entity,) = [e for e in resolution.entries.values() if e.kind.collection == TOPICS]
        assert entity.name == "Bug bash testing"
        assert entity.extracted_names == ["Bug bash testing"]

    async def test_same_string_in_two_kinds_stays_two_entities(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(categories=["Legal"], topics=["Legal"])
        resolution = await resolver.resolve(ctx_factory("r1", "acme", meta))
        keys = {e.key for e in resolution.entries.values()}
        assert len(keys) == 2
        assert (CATEGORIES, "acme", ["legal"]) in _find_calls(fake_graph)
        assert (TOPICS, "acme", ["legal"]) in _find_calls(fake_graph)

    async def test_subcategory_levels_use_their_own_collections(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(
            categories=["Legal"], sub_category_level_1="Contract", sub_category_level_2="Contract",
        )
        resolution = await resolver.resolve(ctx_factory("r1", "acme", meta))
        assert (SUB1, "acme", ["contract"]) in _find_calls(fake_graph)
        assert (SUB2, "acme", ["contract"]) in _find_calls(fake_graph)
        assert resolution.entries[(SUB1, "contract")].key != resolution.entries[(SUB2, "contract")].key

    async def test_subcategory_chain_stops_at_first_gap(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(
            categories=["Legal"], sub_category_level_1="", sub_category_level_2="Deep",
        )
        resolution = await resolver.resolve(ctx_factory("r1", "acme", meta))
        assert not any(e.kind.collection == SUB2 for e in resolution.entries.values())
        assert meta.sub_category_level_2 is None

    async def test_unacceptable_names_are_dropped_and_counted(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(topics=["", "a", "x" * 101, "Valid topic"])
        resolution = await resolver.resolve(ctx_factory("r1", "acme", meta))
        assert resolution.stats.names_dropped == 3
        assert meta.topics == ["Valid topic"]

    async def test_languages_map_through_iso_table_without_vector_or_model(
        self, make_resolver, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(languages=["english", "en-US", "Français", "Klingon"])
        resolution = await resolver.resolve(ctx_factory("r1", "acme", meta))
        assert meta.languages == ["English", "French", "Klingon"]
        assert fake_store.match_calls == []
        assert model.calls == []
        assert resolution.stats.names_deduped == 1


class TestTier0:
    async def test_exact_hit_reuses_node_without_alias_or_lookup(
        self, make_resolver, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        key = _seed(fake_graph, TOPICS, "acme", "Bug bash testing")
        resolver = make_resolver()
        meta = metadata_factory(topics=[" BUG BASH TESTING "])
        resolution = await resolver.resolve(ctx_factory("r1", "acme", meta))
        entity = resolution.entries[(TOPICS, "bug bash testing")]
        assert entity.key == key
        assert entity.is_new is False
        assert entity.new_aliases == []
        assert entity.decision == "exact"
        assert resolution.stats.tier0_hits == 1
        assert fake_store.match_calls == []
        assert model.calls == []
        assert meta.topics == ["Bug bash testing"]

    async def test_hit_carries_existing_aliases(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        _seed(fake_graph, TOPICS, "acme", "NDA", aliases=["Non-disclosure agreement"])
        resolver = make_resolver()
        resolution = await resolver.resolve(ctx_factory("r1", "acme", metadata_factory(topics=["nda"])))
        assert resolution.entries[(TOPICS, "nda")].aliases == ["Non-disclosure agreement"]

    async def test_other_org_node_never_matches(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        _seed(fake_graph, TOPICS, "globex", "Bug bash testing")
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing"]))
        )
        entity = resolution.entries[(TOPICS, "bug bash testing")]
        assert entity.is_new is True
        assert entity.key == taxonomy_node_key("acme", TOPICS, "bug bash testing")

    async def test_graph_error_raises_in_apply_and_is_swallowed_in_shadow(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        fake_graph.fail_find = True
        with pytest.raises(RuntimeError):
            await make_resolver("apply").resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["x y"]))
            )
        shadow = make_resolver("shadow")
        assert await shadow.resolve(ctx_factory("r1", "acme", metadata_factory(topics=["x y"]))) is None


class TestTier1:
    async def test_one_lookup_per_type_and_level(
        self, make_resolver, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        meta = metadata_factory(
            categories=["Legal"], sub_category_level_1="Contract",
            sub_category_level_2="NDA", topics=["Bug bash", "Release checklist"],
        )
        await resolver.resolve(ctx_factory("r1", "acme", meta))
        groups = {(t, level): names for names, _org, t, level in fake_store.match_calls}
        assert groups[("category", None)] == ["Legal"]
        assert groups[("subcategory", "1")] == ["Contract"]
        assert groups[("subcategory", "2")] == ["NDA"]
        assert groups[("topic", None)] == ["Bug bash", "Release checklist"]
        assert all(org == "acme" for _n, org, _t, _l in fake_store.match_calls)

    async def test_empty_store_means_no_winner_and_no_model_call(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(categories=["Legal"], topics=["Bug bash"]))
        )
        assert model.calls == []
        assert resolution.stats.winners_offered == 0
        assert resolution.stats.new_nodes == 2

    async def test_winner_payload_reaches_the_model(
        self, make_resolver, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        from app.models.entities import EntityRecord, EntityType

        model = scripted_model({"bug bash testing session": ("same", "Bug bash testing")})
        await fake_store.upsert_entities_batch([
            EntityRecord(entity_id="k-bug", entity_type=EntityType.TOPIC, name="Bug bash testing",
                         org_id="acme", aliases=["bbt"]),
        ])
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
        )
        (items,) = model.calls
        assert items[0]["match"] == {"id": "k-bug", "name": "Bug bash testing", "aliases": ["bbt"]}
        assert resolution.stats.winners_offered == 1

    async def test_store_failure_falls_back_to_new_without_raising(
        self, make_resolver, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        fake_store.fail_matches = True
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash"]))
        )
        assert resolution.stats.vector_failures == 1
        assert resolution.stats.new_nodes == 1
        assert model.calls == []

    async def test_mismatched_type_or_level_from_store_is_discarded(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        store = MagicMock()
        store.find_best_matches = AsyncMock(return_value=[
            {"entityId": "k", "entityType": "category", "name": "Legal", "aliases": [], "level": None}
        ])
        resolver = make_resolver(store=store)
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Legal"]))
        )
        assert resolution.stats.winners_offered == 0
        assert model.calls == []

    async def test_no_store_wired_skips_tier1(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        resolver = make_resolver(store=None)
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash"]))
        )
        assert resolution.stats.new_nodes == 1
        assert model.calls == []

    async def test_two_unresolved_of_one_kind_go_to_model_even_without_winner(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        resolver = make_resolver()
        await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash", "Bug bash testing"]))
        )
        assert len(model.calls) == 1
        assert [i["match"] for i in model.calls[0]] == [None, None]


class TestTier2:
    async def test_single_call_per_record_with_role_and_effort(
        self, make_resolver, fake_store, metadata_factory, ctx_factory
    ) -> None:
        from app.models.entities import EntityRecord, EntityType

        await fake_store.upsert_entities_batch([
            EntityRecord(entity_id="k-bug", entity_type=EntityType.TOPIC, name="Bug bash testing", org_id="acme"),
            EntityRecord(entity_id="k-legal", entity_type=EntityType.CATEGORY, name="Legal", org_id="acme"),
        ])
        get_llm = AsyncMock(return_value=(MagicMock(name="llm"), {}))
        invoke = AsyncMock(return_value=None)
        with patch("app.modules.entity_resolution.resolver.get_llm_for_role", new=get_llm), patch(
            "app.modules.entity_resolution.resolver.invoke_with_structured_output_and_reflection",
            new=invoke,
        ):
            resolver = make_resolver()
            meta = metadata_factory(categories=["Law"], topics=["Bug bash", "Release", "Onboarding"])
            await resolver.resolve(ctx_factory("r1", "acme", meta))
            await resolver.resolve(ctx_factory("r2", "acme", metadata_factory(topics=["Q3 plan"])))
        assert invoke.await_count == 2
        assert get_llm.await_count == 1, "the model client is built once and reused"
        assert get_llm.await_args.args[1] == "indexing"
        assert get_llm.await_args.kwargs["reasoning_effort"] == "low"

    async def test_model_none_means_every_item_new(
        self, make_resolver, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        from app.models.entities import EntityRecord, EntityType

        scripted_model({"bug bash testing session": "fail"})
        await fake_store.upsert_entities_batch([
            EntityRecord(entity_id="k-bug", entity_type=EntityType.TOPIC, name="Bug bash testing", org_id="acme"),
        ])
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
        )
        assert resolution.stats.model_calls == 1
        assert resolution.stats.model_failures == 1
        entity = resolution.entries[(TOPICS, "bug bash testing session")]
        assert entity.is_new is True

    async def test_model_exception_resets_client_and_falls_back(
        self, make_resolver, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        from app.models.entities import EntityRecord, EntityType

        model = scripted_model()
        model.raise_error = True
        await fake_store.upsert_entities_batch([
            EntityRecord(entity_id="k-bug", entity_type=EntityType.TOPIC, name="Bug bash testing", org_id="acme"),
        ])
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
        )
        assert resolution.stats.model_failures == 1
        assert resolver._llm is None
        assert resolution.entries[(TOPICS, "bug bash testing session")].is_new

    async def test_unknown_and_duplicate_decisions_are_ignored(
        self, make_resolver, fake_store, metadata_factory, ctx_factory
    ) -> None:
        from app.models.entities import EntityRecord, EntityType
        from app.modules.entity_resolution.models import MergeDecision, MergeDecisions

        await fake_store.upsert_entities_batch([
            EntityRecord(entity_id="k-bug", entity_type=EntityType.TOPIC, name="Bug bash testing", org_id="acme"),
        ])
        answer = MergeDecisions(decisions=[
            MergeDecision(i=0, same=True, target="k-bug"),
            MergeDecision(i=0, same=False),  # duplicate index: ignored
            MergeDecision(i=7, same=True, target="k-bug"),  # unknown index: ignored
        ])
        with patch("app.modules.entity_resolution.resolver.get_llm_for_role", new=AsyncMock(return_value=(MagicMock(), {}))), patch(
            "app.modules.entity_resolution.resolver.invoke_with_structured_output_and_reflection",
            new=AsyncMock(return_value=answer),
        ):
            resolver = make_resolver()
            resolution = await resolver.resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
            )
        entity = resolution.entries[(TOPICS, "bug bash testing")]
        assert entity.key == "k-bug"
        assert entity.new_aliases == ["Bug bash testing session"]
        assert resolution.stats.merges == 1

    async def test_no_model_call_when_nothing_is_unresolved(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        _seed(fake_graph, TOPICS, "acme", "Bug bash testing")
        resolver = make_resolver(mode=ResolutionMode.APPLY)
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing"]))
        )
        assert model.calls == []
        assert resolution.stats.model_calls == 0

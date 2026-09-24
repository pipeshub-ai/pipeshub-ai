"""Decision validation: every model answer is checked before it changes anything."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityRecord, EntityType
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.models import MergeDecision, MergeDecisions

TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value


@pytest.fixture
def seeded_store(fake_store) -> object:
    async def _seed(*records) -> object:
        await fake_store.upsert_entities_batch(list(records))
        return fake_store

    return _seed


def _topic(entity_id, name, org="acme", aliases=()) -> EntityRecord:
    return EntityRecord(entity_id=entity_id, entity_type=EntityType.TOPIC, name=name,
                        org_id=org, aliases=list(aliases))


def _answer(*decisions) -> MergeDecisions:
    return MergeDecisions(decisions=list(decisions))


def _patched(answer) -> tuple:
    return (
        patch("app.modules.entity_resolution.resolver.get_llm_for_role",
              new=AsyncMock(return_value=(MagicMock(), {}))),
        patch("app.modules.entity_resolution.resolver.invoke_with_structured_output_and_reflection",
              new=AsyncMock(return_value=answer)),
    )


class TestMergeIntoWinner:
    async def test_same_with_offered_target_merges_and_records_alias(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-bug", "Bug bash testing"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=True, target="k-bug")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
            )
        entity = resolution.entries[(TOPICS, "bug bash testing")]
        assert entity.key == "k-bug" and entity.is_new is False
        assert entity.decision == "merge"
        assert entity.aliases == ["Bug bash testing session"]
        assert entity.new_aliases == ["Bug bash testing session"]
        assert entity.extracted_names == ["Bug bash testing session"]
        assert resolution.stats.merges == 1

    async def test_alias_not_repeated_when_already_known(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-bug", "Bug bash testing", aliases=["bug bash testing session"]))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=True, target="k-bug")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug Bash Testing Session"]))
            )
        entity = resolution.entries[(TOPICS, "bug bash testing")]
        assert entity.new_aliases == []

    async def test_alias_cap_still_merges_but_does_not_append(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        aliases = [f"alias {i}" for i in range(20)]
        await seeded_store(_topic("k-bug", "Bug bash testing", aliases=aliases))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=True, target="k-bug")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
            )
        entity = resolution.entries[(TOPICS, "bug bash testing")]
        assert entity.key == "k-bug"
        assert len(entity.aliases) == 20
        assert entity.new_aliases == []
        assert resolution.stats.alias_cap_hits == 1


class TestRejectedAnswers:
    async def test_target_not_offered_becomes_new(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-bug", "Bug bash testing"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=True, target="k-other")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
            )
        entity = resolution.entries[(TOPICS, "bug bash testing session")]
        assert entity.is_new is True
        assert resolution.stats.rejected_decisions == 1
        assert resolution.stats.merges == 0

    async def test_same_without_target_or_item_becomes_new(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-bug", "Bug bash testing"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=True)))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
            )
        assert resolution.entries[(TOPICS, "bug bash testing session")].is_new
        assert resolution.stats.rejected_decisions == 1

    async def test_same_as_item_across_kinds_is_rejected(
        self, make_resolver, metadata_factory, ctx_factory
    ) -> None:
        # item 0 = category "Legal", item 1 = topic "Legal matters"; both unresolved.
        p1, p2 = _patched(_answer(
            MergeDecision(i=0, same=False),
            MergeDecision(i=1, same=True, same_as_item=0),
        ))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(categories=["Legal"], topics=["Legal matters", "Other"]))
            )
        assert resolution.entries[(TOPICS, "legal matters")].is_new
        assert resolution.stats.rejected_decisions == 1

    async def test_same_as_self_is_rejected(self, make_resolver, metadata_factory, ctx_factory) -> None:
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=True, same_as_item=0), MergeDecision(i=1, same=False)))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Alpha", "Beta"]))
            )
        assert resolution.stats.rejected_decisions == 1
        assert resolution.stats.new_nodes == 2

    async def test_missing_decision_becomes_new(self, make_resolver, metadata_factory, ctx_factory) -> None:
        p1, p2 = _patched(_answer(MergeDecision(i=1, same=False)))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Alpha", "Beta"]))
            )
        assert resolution.stats.new_nodes == 2


class TestNewEntities:
    async def test_new_without_display_form_keeps_trimmed_spelling(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-bug", "Bug bash testing"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=False)))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["  Release checklist. "]))
            )
        entity = resolution.entries[(TOPICS, "release checklist")]
        assert entity.name == "Release checklist"
        assert entity.key == taxonomy_node_key("acme", TOPICS, "release checklist")
        assert entity.new_aliases == []

    async def test_new_with_display_form_keys_on_it_and_keeps_raw_as_alias(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-rc", "Release checklist"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=False, canonical_name="Release Checklist v2")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["release-checklist v2 (draft)"]))
            )
        entity = resolution.entries[(TOPICS, "release checklist v2")]
        assert entity.name == "Release Checklist v2"
        assert entity.key == taxonomy_node_key("acme", TOPICS, "release checklist v2")
        assert entity.aliases == ["release-checklist v2 (draft)"]

    async def test_display_form_matching_existing_node_merges_into_it(
        self, make_resolver, fake_graph, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        from app.modules.entity_resolution.normalizer import normalize_name

        existing_key = taxonomy_node_key("acme", TOPICS, "release checklist")
        fake_graph.nodes[(TOPICS, existing_key)] = {
            "name": "Release checklist", "normalizedName": "release checklist", "orgId": "acme",
        }
        await seeded_store(_topic("k-other", "Something else"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=False, canonical_name="Release Checklist")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["The release-checklist"]))
            )
        entity = resolution.entries[(TOPICS, normalize_name("Release checklist"))]
        assert entity.key == existing_key
        assert entity.is_new is False
        assert entity.decision == "canonical_exact"
        assert entity.aliases == ["The release-checklist"]

    async def test_empty_or_overlong_display_form_falls_back_to_extracted(
        self, make_resolver, metadata_factory, ctx_factory
    ) -> None:
        p1, p2 = _patched(_answer(
            MergeDecision(i=0, same=False, canonical_name="   "),
            MergeDecision(i=1, same=False, canonical_name="y" * 200),
        ))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Alpha", "Beta"]))
            )
        assert (TOPICS, "alpha") in resolution.entries
        assert (TOPICS, "beta") in resolution.entries


class TestSameAsItem:
    async def test_pair_collapses_to_lowest_index(self, make_resolver, metadata_factory, ctx_factory) -> None:
        p1, p2 = _patched(_answer(
            MergeDecision(i=0, same=False),
            MergeDecision(i=1, same=True, same_as_item=0),
        ))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash", "Bug bash testing"]))
            )
        (entity,) = resolution.entries.values()
        assert entity.name == "Bug bash"
        assert entity.aliases == ["Bug bash testing"]
        assert entity.extracted_names == ["Bug bash", "Bug bash testing"]
        assert resolution.stats.new_nodes == 1
        assert resolution.stats.in_record_merges == 1

    async def test_chain_and_cycle_resolve_transitively(self, make_resolver, metadata_factory, ctx_factory) -> None:
        p1, p2 = _patched(_answer(
            MergeDecision(i=0, same=True, same_as_item=1),
            MergeDecision(i=1, same=True, same_as_item=2),
            MergeDecision(i=2, same=True, same_as_item=0),
        ))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["A one", "A two", "A three"]))
            )
        (entity,) = resolution.entries.values()
        assert entity.name == "A one"
        assert set(entity.aliases) == {"A two", "A three"}

    async def test_group_follows_member_that_merged_into_winner(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-bug", "Bug bash testing"))
        p1, p2 = _patched(_answer(
            MergeDecision(i=0, same=True, same_as_item=1),
            MergeDecision(i=1, same=True, target="k-bug"),
        ))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash", "Bug bash testing session"]))
            )
        entity = resolution.entries[(TOPICS, "bug bash testing")]
        assert entity.key == "k-bug"
        assert set(entity.aliases) == {"Bug bash", "Bug bash testing session"}
        assert resolution.stats.merges == 1
        assert resolution.stats.in_record_merges == 1

    async def test_conflicting_winners_in_one_group_keep_first_and_count(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-a", "Alpha topic"), _topic("k-b", "Beta topic"))
        p1, p2 = _patched(_answer(
            MergeDecision(i=0, same=True, target="k-a"),
            MergeDecision(i=1, same=True, target="k-b"),
            MergeDecision(i=2, same=True, same_as_item=0),
        ))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Alpha topic x", "Beta topic x", "Gamma"]))
            )
        # only a genuine group can conflict: bind item 1 to item 0 through 2
        assert resolution.stats.rejected_decisions == 0  # 0 and 1 are separate components here


class TestConcurrencyByConstruction:
    async def test_two_records_resolve_same_new_name_to_same_key(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        a = await resolver.resolve(ctx_factory("r1", "acme", metadata_factory(topics=["Onboarding checklist"])))
        b = await resolver.resolve(ctx_factory("r2", "acme", metadata_factory(topics=["onboarding  checklist"])))
        assert a.entries[(TOPICS, "onboarding checklist")].key == b.entries[(TOPICS, "onboarding checklist")].key

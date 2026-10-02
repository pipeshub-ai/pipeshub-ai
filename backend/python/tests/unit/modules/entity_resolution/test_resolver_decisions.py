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
                ctx_factory("r1", "acme", metadata_factory(topics=["release-checklist V2"]))
            )
        entity = resolution.entries[(TOPICS, "release checklist v2")]
        assert entity.name == "Release Checklist v2"
        assert entity.key == taxonomy_node_key("acme", TOPICS, "release checklist v2")
        assert entity.aliases == ["release-checklist V2"]
        assert resolution.stats.rejected_decisions == 0

    async def test_display_form_that_changes_words_is_ignored(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        """The prompt allows casing and punctuation fixes only; dropping
        "(draft)" makes it a different name the model was never asked about."""
        await seeded_store(_topic("k-rc", "Release checklist"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=False, canonical_name="Release Checklist v2")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["release-checklist v2 (draft)"]))
            )
        entity = resolution.entries[(TOPICS, "release-checklist v2 (draft)")]
        assert entity.name == "release-checklist v2 (draft)"
        assert entity.is_new is True
        assert resolution.stats.rejected_decisions == 1

    async def test_display_form_naming_an_unoffered_node_does_not_merge_into_it(
        self, make_resolver, fake_graph, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        """A "cleaned" form that drops words must not become an unvalidated
        merge into whatever node carries that name."""
        existing_key = taxonomy_node_key("acme", TOPICS, "revenue")
        fake_graph.nodes[(TOPICS, existing_key)] = {
            "name": "Revenue", "normalizedName": "revenue", "orgId": "acme",
        }
        await seeded_store(_topic("k-other", "Something else"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=False, canonical_name="Revenue")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Q3 revenue forecast"]))
            )
        entity = resolution.entries[(TOPICS, "q3 revenue forecast")]
        assert entity.key != existing_key
        assert entity.is_new is True
        assert fake_graph.nodes[(TOPICS, existing_key)].get("aliases") in (None, [])

    async def test_display_form_matching_existing_node_merges_into_it(
        self, make_resolver, fake_graph, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        """Only punctuation differs, so it is the same name spelled differently,
        and the node that already carries it is the right target."""
        from app.modules.entity_resolution.normalizer import normalize_name

        existing_key = taxonomy_node_key("acme", TOPICS, "release checklist")
        fake_graph.nodes[(TOPICS, existing_key)] = {
            "name": "Release checklist", "normalizedName": "release checklist", "orgId": "acme",
        }
        await seeded_store(_topic("k-other", "Something else"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=False, canonical_name="Release Checklist")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["release-checklist"]))
            )
        entity = resolution.entries[(TOPICS, normalize_name("Release checklist"))]
        assert entity.key == existing_key
        assert entity.is_new is False
        assert entity.decision == "canonical_exact"
        assert entity.aliases == ["release-checklist"]

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

    async def test_a_valid_target_wins_over_an_item_pointer(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        """KG-39: the model named both the offered node and another item; the
        node is the stronger answer and must not be dropped for the pointer."""
        await seeded_store(_topic("k-bug", "Bug bash testing"))
        p1, p2 = _patched(_answer(
            MergeDecision(i=0, same=False),
            MergeDecision(i=1, same=True, target="k-bug", same_as_item=0),
        ))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Release notes", "Bug bash testing session"]))
            )
        merged = resolution.entries[(TOPICS, "bug bash testing")]
        assert merged.key == "k-bug" and merged.extracted_names == ["Bug bash testing session"]
        assert resolution.entries[(TOPICS, "release notes")].is_new
        assert resolution.stats.merges == 1 and resolution.stats.rejected_decisions == 0


class TestEmptyAnswer:
    async def test_an_answer_with_no_usable_decision_counts_as_a_failed_call(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        """KG-39: an empty (or all-invalid) decision list is not a success."""
        from app.modules.entity_resolution import resolver as resolver_module

        await seeded_store(_topic("k-bug", "Bug bash testing"))
        p1, p2 = _patched(_answer(MergeDecision(i=7, same=True, target="k-bug")))
        with p1, p2, patch.object(resolver_module.metrics, "record_model_call") as calls, \
                patch.object(resolver_module.metrics, "record_fallback") as fallbacks:
            resolver = make_resolver()
            resolution = await resolver.resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Bug bash testing session"]))
            )
        assert [c.args[0] for c in calls.call_args_list] == ["empty"]
        assert ("model_empty", 1) in [tuple(c.args) for c in fallbacks.call_args_list]
        assert resolution.stats.model_failures == 1
        assert resolution.entries[(TOPICS, "bug bash testing session")].is_new


class TestConcurrencyByConstruction:
    async def test_two_records_resolve_same_new_name_to_same_key(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        resolver = make_resolver()
        a = await resolver.resolve(ctx_factory("r1", "acme", metadata_factory(topics=["Onboarding checklist"])))
        b = await resolver.resolve(ctx_factory("r2", "acme", metadata_factory(topics=["onboarding  checklist"])))
        assert a.entries[(TOPICS, "onboarding checklist")].key == b.entries[(TOPICS, "onboarding checklist")].key


class TestSeveralCandidates:
    """KG-12: the model is offered the top candidates, and may merge into any
    of them; anything it was not offered is still rejected."""

    async def test_merging_into_the_second_ranked_candidate(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        await seeded_store(_topic("k-notes", "Release notes"), _topic("k-check", "Release checklist"))
        p1, p2 = _patched(_answer(MergeDecision(i=0, same=True, target="k-check")))
        with p1, p2:
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Release check list"]))
            )
        entity = resolution.entries[(TOPICS, "release checklist")]
        assert entity.key == "k-check" and entity.decision == "merge"
        assert resolution.stats.merges == 1

    async def test_every_offered_candidate_reaches_the_prompt(
        self, make_resolver, seeded_store, metadata_factory, ctx_factory
    ) -> None:
        from app.modules.entity_resolution import resolver as resolver_module

        await seeded_store(_topic("k-a", "Release notes"), _topic("k-b", "Release checklist"), _topic("k-c", "Release plan"))
        seen: list[str] = []

        async def _capture(llm, messages, schema, **kwargs) -> MergeDecisions:
            seen.append(messages[0].content)
            return _answer(MergeDecision(i=0, same=False))

        with patch.object(resolver_module, "get_llm_for_role", AsyncMock(return_value=(MagicMock(), {}))), \
                patch.object(resolver_module, "invoke_with_structured_output_and_reflection", side_effect=_capture):
            await make_resolver().resolve(ctx_factory("r1", "acme", metadata_factory(topics=["Release thing"])))
        assert all(key in seen[0] for key in ("k-a", "k-b", "k-c"))

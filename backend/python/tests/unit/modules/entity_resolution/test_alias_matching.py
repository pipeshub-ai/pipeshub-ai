"""Aliases are payload only, and a merged spelling resolves exactly from then on."""

from unittest.mock import MagicMock

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityRecord, EntityType
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.normalizer import normalize_name

TOPICS = CollectionNames.TOPICS.value


def _seed(fake_graph, org, name, aliases=()) -> str:
    key = taxonomy_node_key(org, TOPICS, normalize_name(name))
    fake_graph.nodes[(TOPICS, key)] = {
        "name": name, "normalizedName": normalize_name(name), "orgId": org,
        "aliases": list(aliases), "normalizedAliases": [normalize_name(a) for a in aliases],
    }
    return key


class TestEmbeddedText:
    def test_only_the_canonical_name_is_embedded(self) -> None:
        entity = EntityRecord(
            entity_id="k", entity_type=EntityType.TOPIC, name="  Bug bash session ",
            org_id="acme", aliases=["bug bash testing session"], description="ignored",
        )
        assert entity.embedding_text == "Bug bash session"
        assert entity.to_vector_payload()["aliases"] == ["bug bash testing session"]


class TestTier0AliasMatch:
    async def test_known_alias_is_an_exact_hit_without_vector_or_model(
        self, make_resolver, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        key = _seed(fake_graph, "acme", "bug bash session", aliases=["bug bash testing session"])
        resolver = make_resolver()
        meta = metadata_factory(topics=["Bug Bash Testing Session"])
        resolution = await resolver.resolve(ctx_factory("r3", "acme", meta))

        entity = resolution.entries[(TOPICS, "bug bash session")]
        assert entity.key == key
        assert entity.decision == "exact"
        assert entity.new_aliases == []
        assert entity.extracted_names == ["Bug Bash Testing Session"]
        assert resolution.stats.tier0_hits == 1
        assert fake_store.match_calls == []
        assert model.calls == []
        assert meta.topics == ["bug bash session"]

    async def test_name_match_wins_over_alias_match(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        by_name = _seed(fake_graph, "acme", "release checklist")
        _seed(fake_graph, "acme", "launch plan", aliases=["release checklist"])
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Release Checklist"]))
        )
        assert resolution.entries[(TOPICS, "release checklist")].key == by_name

    async def test_alias_of_another_org_never_matches(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        _seed(fake_graph, "globex", "bug bash session", aliases=["bug bash testing session"])
        resolver = make_resolver()
        resolution = await resolver.resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["bug bash testing session"]))
        )
        assert resolution.entries[(TOPICS, "bug bash testing session")].is_new


class TestMergeWritesBothAliasForms:
    async def test_graph_receives_display_and_normalized_pairs(
        self, make_resolver, make_transformer, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        from app.modules.transformers.sink_orchestrator import SinkOrchestrator

        fake_graph.add_record("r1", "acme")
        fake_graph.add_record("r2", "acme")
        resolver = make_resolver()
        sink = SinkOrchestrator(
            graphdb=make_transformer(), blob_storage=MagicMock(), vector_store=MagicMock(),
            graph_provider=MagicMock(), logger=MagicMock(), config_service=MagicMock(),
            entity_vector_store=fake_store, entity_resolver=resolver,
        )
        scripted_model()
        ctx1 = ctx_factory("r1", "acme", metadata_factory(categories=["QA"], topics=["Bug bash session"]))
        await sink.resolve_entities(ctx1)
        await sink.enrich(ctx1)

        scripted_model({"bug bash testing session": ("same", "Bug bash session")})
        ctx2 = ctx_factory("r2", "acme", metadata_factory(categories=["QA"], topics=["Bug bash testing session"]))
        await sink.resolve_entities(ctx2)
        await sink.enrich(ctx2)

        key = taxonomy_node_key("acme", TOPICS, "bug bash session")
        node = fake_graph.node(TOPICS, key)
        assert node["aliases"] == ["Bug bash testing session"]
        assert node["normalizedAliases"] == ["bug bash testing session"]
        point = fake_store.point("acme", "topic", key)
        assert point["page_content"] == "Bug bash session"
        assert point["aliases"] == ["Bug bash testing session"]

        # Third record with the same variant: exact hit, no model call.
        model = scripted_model()
        fake_graph.add_record("r3", "acme")
        ctx3 = ctx_factory("r3", "acme", metadata_factory(categories=["QA"], topics=["BUG BASH TESTING SESSION"]))
        await sink.resolve_entities(ctx3)
        await sink.enrich(ctx3)
        assert model.calls == []
        assert ctx3.entity_resolution.stats.tier0_hits == 2
        assert len(fake_graph.nodes_in(TOPICS)) == 1


class TestTier0AliasTies:
    async def test_an_alias_shared_by_two_nodes_resolves_to_the_same_one_every_time(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        """KG-39: the winner no longer depends on the provider's row order."""
        scripted_model()
        first = _seed(fake_graph, "acme", "launch plan", aliases=["go live"])
        second = _seed(fake_graph, "acme", "cutover", aliases=["go live"])
        expected = min(first, second)
        for order in (list(fake_graph.nodes.items()), list(reversed(fake_graph.nodes.items()))):
            fake_graph.nodes = dict(order)
            resolution = await make_resolver().resolve(
                ctx_factory("r1", "acme", metadata_factory(topics=["Go live"]))
            )
            (entity,) = resolution.entries.values()
            assert entity.key == expected

    async def test_two_live_nodes_with_one_name_resolve_to_the_lowest_key(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        """Older graphs can hold two nodes with one normalized name; the name
        match follows the same lowest-key rule as an alias tie."""
        scripted_model()
        for key in ("k-b", "k-a"):
            fake_graph.nodes[(TOPICS, key)] = {"name": "Go live", "normalizedName": "go live", "orgId": "acme"}
        resolution = await make_resolver().resolve(
            ctx_factory("r1", "acme", metadata_factory(topics=["Go live"]))
        )
        (entity,) = resolution.entries.values()
        assert entity.key == "k-a"

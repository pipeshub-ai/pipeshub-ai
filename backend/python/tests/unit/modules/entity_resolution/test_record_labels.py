"""A record's labels are the names extracted from its own content.

Resolution still decides which canonical node each name links to; the
record's ``semantic_metadata`` keeps its own spellings.
"""

from app.config.constants.arangodb import CollectionNames
from app.models.entities import EntityRecord, EntityType
from app.modules.entity_resolution.keys import taxonomy_node_key

TOPICS = CollectionNames.TOPICS.value
CATEGORIES = CollectionNames.CATEGORIES.value
BELONGS_TO_TOPIC = CollectionNames.BELONGS_TO_TOPIC.value
BELONGS_TO_CATEGORY = CollectionNames.BELONGS_TO_CATEGORY.value


class TestARecordKeepsItsOwnExtractedLabels:
    async def test_exact_match_on_another_spelling_keeps_the_records_spelling(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        key = taxonomy_node_key("acme", TOPICS, "bug bash testing")
        fake_graph.nodes[(TOPICS, key)] = {
            "name": "Bug Bash Testing", "normalizedName": "bug bash testing", "orgId": "acme",
        }
        meta = metadata_factory(
            categories=["  Quality Assurance. "], sub_category_level_1="testing",
            sub_category_level_2="Manual testing", languages=["en"],
            topics=["bug bash testing", "Test plan", "BUG BASH TESTING"],
        )
        ctx = ctx_factory("r1", "acme", meta)

        resolution = await make_resolver("apply").resolve(ctx)

        assert ctx.entity_resolution is resolution
        assert meta.topics == ["bug bash testing", "Test plan"]
        assert meta.categories == ["Quality Assurance"]
        assert (meta.sub_category_level_1, meta.sub_category_level_2) == ("testing", "Manual testing")
        assert meta.sub_category_level_3 is None
        assert meta.languages == ["English"]
        assert resolution.get(TOPICS, "bug bash testing").key == key

    async def test_a_merged_name_keeps_the_records_spelling_and_links_the_winner(
        self, make_resolver, make_transformer, fake_graph, fake_store,
        metadata_factory, ctx_factory, scripted_model,
    ) -> None:
        winner = "k-winner"
        await fake_store.upsert_entities_batch([
            EntityRecord(entity_id=winner, entity_type=EntityType.TOPIC,
                         name="Northwind rollout", org_id="acme"),
        ])
        scripted_model({"regional rollout": ("same", "Northwind rollout")})
        fake_graph.add_record("r2", "acme")
        meta = metadata_factory(topics=["Regional rollout"])
        ctx = ctx_factory("r2", "acme", meta)

        await make_resolver("apply").resolve(ctx)
        await make_transformer().apply(ctx)

        assert meta.topics == ["Regional rollout"]
        edges = fake_graph.edges_from("r2", BELONGS_TO_TOPIC)
        assert [(e["to_id"], e.get("extractedName")) for e in edges] == [(winner, "Regional rollout")]
        assert fake_graph.node(TOPICS, winner)["name"] == "Northwind rollout"

    async def test_rendered_record_text_uses_the_records_own_words(
        self, make_resolver, fake_store, metadata_factory, ctx_factory, scripted_model,
    ) -> None:
        await fake_store.upsert_entities_batch([
            EntityRecord(entity_id="k-cat", entity_type=EntityType.CATEGORY,
                         name="Northwind programme", org_id="acme"),
        ])
        scripted_model({"regional programme": ("same", "Northwind programme")})
        meta = metadata_factory(categories=["Regional programme"], topics=["Shipping dates"])

        await make_resolver("apply").resolve(ctx_factory("r3", "acme", meta))

        text = "\n".join(meta.to_llm_context())
        assert "Category: Regional programme" in text
        assert "northwind" not in text.casefold()

    async def test_the_graph_links_each_own_name_to_its_canonical_node(
        self, make_resolver, make_transformer, fake_graph, metadata_factory, ctx_factory, scripted_model,
    ) -> None:
        scripted_model()
        key = taxonomy_node_key("acme", CATEGORIES, "quality assurance")
        fake_graph.nodes[(CATEGORIES, key)] = {
            "name": "Quality assurance", "normalizedName": "quality assurance", "orgId": "acme",
        }
        fake_graph.add_record("r4", "acme")
        meta = metadata_factory(categories=["QUALITY ASSURANCE"], topics=["Release checklist"])
        ctx = ctx_factory("r4", "acme", meta)

        await make_resolver("apply").resolve(ctx)
        await make_transformer().apply(ctx)

        assert meta.categories == ["QUALITY ASSURANCE"]
        cat_edges = fake_graph.edges_from("r4", BELONGS_TO_CATEGORY)
        assert [(e["to_id"], e.get("extractedName")) for e in cat_edges] == [(key, "QUALITY ASSURANCE")]
        assert [n["name"] for n in fake_graph.nodes_in(CATEGORIES)] == ["Quality assurance"]


class TestEverySpellingOfOneNodeIsKept:
    async def test_two_spellings_of_one_node_are_both_on_the_edge(
        self, make_resolver, make_transformer, fake_graph, metadata_factory, ctx_factory, scripted_model,
    ) -> None:
        scripted_model({"bug bash testing": ("same_as", "Bug bash")})
        fake_graph.add_record("r5", "acme")
        meta = metadata_factory(topics=["Bug bash", "Bug bash testing"])
        ctx = ctx_factory("r5", "acme", meta)

        await make_resolver("apply").resolve(ctx)
        await make_transformer().apply(ctx)

        (edge,) = fake_graph.edges_from("r5", BELONGS_TO_TOPIC)
        assert edge["extractedName"] == "Bug bash"
        assert edge["extractedNames"] == ["Bug bash", "Bug bash testing"]
        (row,) = await fake_graph.get_record_taxonomy_links(["r5"])
        assert row["extractedNames"] == ["Bug bash", "Bug bash testing"]


class TestAReindexWritesTheSpellingOntoAnExistingEdge:
    async def test_an_edge_without_a_spelling_gets_the_records_spelling(
        self, make_resolver, make_transformer, fake_graph, metadata_factory, ctx_factory, scripted_model,
    ) -> None:
        scripted_model()
        key = taxonomy_node_key("acme", TOPICS, "release checklist")
        fake_graph.nodes[(TOPICS, key)] = {
            "name": "Release checklist", "normalizedName": "release checklist", "orgId": "acme",
        }
        fake_graph.add_record("r6", "acme")
        target = (BELONGS_TO_TOPIC, "records/r6", f"{TOPICS}/{key}")
        fake_graph.edges[target] = {
            "from_id": "r6", "from_collection": "records", "to_id": key, "to_collection": TOPICS,
            "createdAtTimestamp": 7, "mergedFrom": "topics/older",
        }
        ctx = ctx_factory("r6", "acme", metadata_factory(topics=["release checklist"]))

        await make_resolver("apply").resolve(ctx)
        await make_transformer().apply(ctx)

        edge = fake_graph.edges[target]
        assert edge["extractedName"] == "release checklist"
        assert edge["extractedNames"] == ["release checklist"]
        assert (edge["createdAtTimestamp"], edge["mergedFrom"]) == (7, "topics/older")

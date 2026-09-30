"""End-to-end scenarios: real resolver, real graph transformer, real sink
orchestrator, driven against in-memory fakes. Mirrors the dry run that was
agreed before implementation (R1 to R11 plus failure and concurrency cases).
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.transformers.sink_orchestrator import SinkOrchestrator

CATEGORIES = CollectionNames.CATEGORIES.value
SUB1 = CollectionNames.SUBCATEGORIES1.value
SUB2 = CollectionNames.SUBCATEGORIES2.value
TOPICS = CollectionNames.TOPICS.value
LANGUAGES = CollectionNames.LANGUAGES.value
BELONGS_TO_TOPIC = CollectionNames.BELONGS_TO_TOPIC.value
BELONGS_TO_CATEGORY = CollectionNames.BELONGS_TO_CATEGORY.value
BELONGS_TO_LANGUAGE = CollectionNames.BELONGS_TO_LANGUAGE.value
BELONGS_TO_DEPARTMENT = CollectionNames.BELONGS_TO_DEPARTMENT.value
HIERARCHY = CollectionNames.INTER_CATEGORY_RELATIONS.value


def k(org: str, collection: str, normalized: str) -> str:
    return taxonomy_node_key(org, collection, normalized)


@pytest.fixture
def pipeline(make_resolver, make_transformer, fake_graph, fake_store) -> object:
    """Runs one record through resolve -> graph write -> entity points."""

    def _build(mode="apply") -> object:
        resolver = make_resolver(mode)
        sink = SinkOrchestrator(
            graphdb=make_transformer(),
            blob_storage=MagicMock(),
            vector_store=MagicMock(),
            graph_provider=MagicMock(),
            logger=MagicMock(),
            config_service=MagicMock(),
            entity_vector_store=fake_store,
            entity_resolver=resolver,
        )

        async def _run(ctx) -> object:
            record = ctx.record
            if record.id not in fake_graph.records:
                fake_graph.add_record(record.id, record.org_id, record.connector_id, record.record_group_id)
            await sink.resolve_entities(ctx)
            await sink.enrich(ctx)
            return ctx.entity_resolution

        _run.resolver = resolver
        return _run

    return _build


def _r1_metadata(metadata_factory) -> object:
    return metadata_factory(
        departments=["Engineering"],
        categories=["Quality Assurance"],
        sub_category_level_1="Testing",
        sub_category_level_2="Manual Testing",
        languages=["English"],
        topics=["Bug bash testing", "Test plan", "bug bash testing"],
        summary="QA process document.",
    )


async def _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model) -> tuple:
    scripted_model()
    fake_graph.add_department("Engineering")
    run = pipeline("apply")
    ctx = ctx_factory("r1", "acme", _r1_metadata(metadata_factory))
    await run(ctx)
    return run, ctx


class TestR1FirstRecord:
    async def test_everything_is_new_and_siblings_are_checked_once(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model()
        fake_graph.add_department("Engineering")
        run = pipeline("apply")
        ctx = ctx_factory("r1", "acme", _r1_metadata(metadata_factory))
        resolution = await run(ctx)

        # Nothing exists yet, so no winner is offered; the one call exists
        # only to let the model group in-record siblings (two new topics).
        assert len(model.calls) == 1
        assert all(item["match"] is None for item in model.calls[0])
        assert resolution.stats.winners_offered == 0
        assert resolution.stats.new_nodes == 6
        assert resolution.stats.names_deduped == 1

        cat = fake_graph.node(CATEGORIES, k("acme", CATEGORIES, "quality assurance"))
        assert cat["name"] == "Quality Assurance"
        assert cat["normalizedName"] == "quality assurance"
        assert cat["orgId"] == "acme"
        assert cat["aliases"] == []
        assert (SUB1, k("acme", SUB1, "testing")) in fake_graph.nodes
        assert (SUB2, k("acme", SUB2, "manual testing")) in fake_graph.nodes
        assert (TOPICS, k("acme", TOPICS, "bug bash testing")) in fake_graph.nodes
        assert (TOPICS, k("acme", TOPICS, "test plan")) in fake_graph.nodes
        assert (LANGUAGES, k("acme", LANGUAGES, "english")) in fake_graph.nodes

        topic_edges = fake_graph.edges_from("r1", BELONGS_TO_TOPIC)
        assert sorted(e["extractedName"] for e in topic_edges) == ["Bug bash testing", "Test plan"]
        assert len(fake_graph.edges_from("r1", BELONGS_TO_CATEGORY)) == 3
        assert len(fake_graph.edges_from("r1", BELONGS_TO_LANGUAGE)) == 1
        assert len(fake_graph.edges_from("r1", BELONGS_TO_DEPARTMENT)) == 1
        assert (HIERARCHY, f"{SUB1}/{k('acme', SUB1, 'testing')}", f"{CATEGORIES}/{k('acme', CATEGORIES, 'quality assurance')}") in fake_graph.edges
        assert (HIERARCHY, f"{SUB2}/{k('acme', SUB2, 'manual testing')}", f"{SUB1}/{k('acme', SUB1, 'testing')}") in fake_graph.edges

        assert ctx.record.semantic_metadata.topics == ["Bug bash testing", "Test plan"]
        assert len(fake_store.points) == 7  # 6 taxonomy + department
        sub2_point = fake_store.point("acme", "subcategory", k("acme", SUB2, "manual testing"))
        assert sub2_point["level"] == "2"
        assert fake_store.point("acme", "topic", k("acme", TOPICS, "bug bash testing"))["page_content"] == "Bug bash testing"


class TestR2ExactAndWinnerMerges:
    async def test_casing_hits_tier0_and_variants_merge_through_model(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        model = scripted_model({
            "qa": ("same", "Quality Assurance"),
            "bug bash testing session": ("same", "Bug bash testing"),
            "release checklist": ("new", ""),
        })
        meta = metadata_factory(
            categories=["QA"], sub_category_level_1="testing",
            topics=["BUG BASH TESTING ", "Bug bash testing session", "Release checklist"],
            summary="Notes from the March bug bash.",
        )
        ctx = ctx_factory("r2", "acme", meta, connector_id="conn-2", record_group_id="rg-2")
        resolution = await run(ctx)

        assert len(model.calls) == 1
        assert [i["name"] for i in model.calls[0]] == ["QA", "Bug bash testing session", "Release checklist"]
        assert resolution.stats.tier0_hits == 2
        assert resolution.stats.merges == 2
        assert resolution.stats.new_nodes == 1

        topic_key = k("acme", TOPICS, "bug bash testing")
        assert fake_graph.node(TOPICS, topic_key)["aliases"] == ["Bug bash testing session"]
        assert fake_graph.node(CATEGORIES, k("acme", CATEGORIES, "quality assurance"))["aliases"] == ["QA"]
        assert (TOPICS, k("acme", TOPICS, "release checklist")) in fake_graph.nodes

        topic_edges = {e["to_id"]: e for e in fake_graph.edges_from("r2", BELONGS_TO_TOPIC)}
        assert len(topic_edges) == 2
        assert topic_edges[topic_key]["extractedName"] == "BUG BASH TESTING "
        assert len(fake_graph.edges_from("r2", BELONGS_TO_CATEGORY)) == 2

        assert meta.categories == ["Quality Assurance"]
        assert meta.sub_category_level_1 == "Testing"
        assert meta.topics == ["Bug bash testing", "Release checklist"]

        cat_point = fake_store.point("acme", "category", k("acme", CATEGORIES, "quality assurance"))
        assert cat_point["aliases"] == ["QA"]
        assert cat_point["page_content"] == "Quality Assurance"
        assert set(cat_point["connectorIds"]) == {"conn-1", "conn-2"}
        assert len(fake_store.points) == 8


class TestR3SimilarButDistinct:
    async def test_model_says_new_and_kinds_never_cross(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        model = scripted_model({"integration testing": ("new", "")})
        meta = metadata_factory(
            categories=["Quality Assurance"], sub_category_level_1="Testing",
            sub_category_level_2="Integration Testing", topics=["Integration testing"],
        )
        await run(ctx_factory("r3", "acme", meta))

        (items,) = model.calls
        kinds = {i["name"]: i["kind"] for i in items}
        assert kinds == {"Integration Testing": "subcategory level 2", "Integration testing": "topic"}
        level2 = next(i for i in items if i["kind"] == "subcategory level 2")
        assert level2["match"]["name"] == "Manual Testing"

        topic_key = k("acme", TOPICS, "integration testing")
        sub2_key = k("acme", SUB2, "integration testing")
        assert topic_key != sub2_key
        assert (TOPICS, topic_key) in fake_graph.nodes
        assert (SUB2, sub2_key) in fake_graph.nodes
        assert (HIERARCHY, f"{SUB2}/{sub2_key}", f"{SUB1}/{k('acme', SUB1, 'testing')}") in fake_graph.edges


class TestR4Reordering:
    async def test_word_order_variant_merges_and_keeps_alias(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        scripted_model({"testing bug bash": ("same", "Bug bash testing")})
        meta = metadata_factory(categories=["Quality Assurance"], topics=["Testing bug bash"])
        await run(ctx_factory("r4", "acme", meta))
        topic_key = k("acme", TOPICS, "bug bash testing")
        assert fake_graph.node(TOPICS, topic_key)["aliases"] == ["Testing bug bash"]
        (edge,) = fake_graph.edges_from("r4", BELONGS_TO_TOPIC)
        assert edge["to_id"] == topic_key and edge["extractedName"] == "Testing bug bash"
        assert meta.topics == ["Bug bash testing"]


class TestR5ModelDisplayForm:
    async def test_new_node_keyed_on_display_form_and_reachable_by_tier0_later(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        scripted_model({"release checklist": ("new", "")})
        await run(ctx_factory("r2", "acme", metadata_factory(categories=["Quality Assurance"], topics=["Release checklist"])))

        scripted_model({"release-checklist v2 (draft)": ("new", "Release Checklist v2")})
        meta = metadata_factory(categories=["Quality Assurance"], topics=["release-checklist v2 (draft)"])
        await run(ctx_factory("r5", "acme", meta))
        key = k("acme", TOPICS, "release checklist v2")
        node = fake_graph.node(TOPICS, key)
        assert node["name"] == "Release Checklist v2"
        assert node["aliases"] == ["release-checklist v2 (draft)"]
        assert meta.topics == ["Release Checklist v2"]

        scripted_model()
        resolution = await run(ctx_factory("r6", "acme", metadata_factory(categories=["Quality Assurance"], topics=["release checklist v2"])))
        assert resolution.entries[(TOPICS, "release checklist v2")].key == key
        assert resolution.stats.tier0_hits == 2


class TestR6LevelsNeverCross:
    async def test_same_name_at_two_levels_is_two_nodes(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        model = scripted_model({"legal": ("new", ""), "contract": ("new", "")})
        meta = metadata_factory(categories=["Legal"], sub_category_level_1="Contract", sub_category_level_2="Contract")
        await run(ctx_factory("r6", "acme", meta))
        (items,) = model.calls
        assert {i["kind"] for i in items} == {"category", "subcategory level 1", "subcategory level 2"}
        sub1_key, sub2_key = k("acme", SUB1, "contract"), k("acme", SUB2, "contract")
        assert (SUB1, sub1_key) in fake_graph.nodes and (SUB2, sub2_key) in fake_graph.nodes
        assert (HIERARCHY, f"{SUB2}/{sub2_key}", f"{SUB1}/{sub1_key}") in fake_graph.edges
        assert (HIERARCHY, f"{SUB1}/{sub1_key}", f"{CATEGORIES}/{k('acme', CATEGORIES, 'legal')}") in fake_graph.edges


class TestR7InRecordCollisionWithExisting:
    async def test_two_spellings_resolve_to_one_existing_node(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        scripted_model({"nda": ("new", "")})
        await run(ctx_factory("r7a", "acme", metadata_factory(categories=["Quality Assurance"], topics=["NDA"])))

        fake_store.force_winner["non-disclosure agreement"] = "nda"
        scripted_model({"non-disclosure agreement": ("same", "NDA")})
        meta = metadata_factory(categories=["Quality Assurance"], topics=["NDA", "Non-disclosure agreement"])
        await run(ctx_factory("r7b", "acme", meta))
        nda_key = k("acme", TOPICS, "nda")
        assert fake_graph.node(TOPICS, nda_key)["aliases"] == ["Non-disclosure agreement"]
        (edge,) = fake_graph.edges_from("r7b", BELONGS_TO_TOPIC)
        assert edge["to_id"] == nda_key and edge["extractedName"] == "NDA"
        assert meta.topics == ["NDA"]


class TestR8PerOrgIsolation:
    async def test_other_org_gets_its_own_node_and_point(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        model = scripted_model()
        await run(ctx_factory("g1", "globex", metadata_factory(categories=["Ops"], topics=["bug bash testing"])))
        assert model.calls == []
        acme_key, globex_key = k("acme", TOPICS, "bug bash testing"), k("globex", TOPICS, "bug bash testing")
        assert acme_key != globex_key
        assert fake_graph.node(TOPICS, globex_key)["orgId"] == "globex"
        assert fake_store.point("globex", "topic", globex_key) is not None
        assert fake_store.point("acme", "topic", acme_key)["aliases"] == []
        assert all(org == "globex" for _n, org, _t, _l in fake_store.match_calls[-2:])


class TestR9Reindex:
    async def test_edges_reconciled_and_orphan_left_in_place(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        scripted_model({"regression suite": ("new", "")})
        meta = metadata_factory(
            departments=["Engineering"], categories=["Quality Assurance"],
            sub_category_level_1="Testing", sub_category_level_2="Manual Testing",
            languages=["English"], topics=["Bug Bash Testing", "Regression suite"],
        )
        await run(ctx_factory("r1", "acme", meta))
        edges = {e["to_id"]: e for e in fake_graph.edges_from("r1", BELONGS_TO_TOPIC)}
        assert set(edges) == {k("acme", TOPICS, "bug bash testing"), k("acme", TOPICS, "regression suite")}
        assert edges[k("acme", TOPICS, "bug bash testing")]["extractedName"] == "Bug bash testing"
        test_plan_key = k("acme", TOPICS, "test plan")
        assert (TOPICS, test_plan_key) in fake_graph.nodes
        assert fake_graph.edges_to(f"{TOPICS}/{test_plan_key}") == []
        assert fake_store.point("acme", "topic", test_plan_key) is not None


class TestR10LanguagesAndDepartments:
    async def test_iso_mapping_and_unchanged_department_matching(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        scripted_model()
        meta = metadata_factory(
            departments=["Engineering", "Platform Eng"], categories=["Quality Assurance"],
            languages=["english", "en-US", "Français"],
        )
        resolution = await run(ctx_factory("r10", "acme", meta))
        assert meta.languages == ["English", "French"]
        assert len(fake_graph.edges_from("r10", BELONGS_TO_DEPARTMENT)) == 1
        assert len(fake_graph.nodes_in(LANGUAGES)) == 2
        assert resolution.entries[(LANGUAGES, "english")].is_new is False
        assert resolution.entries[(LANGUAGES, "french")].is_new is True


class TestR11InRecordDuplicatesInEmptyOrg:
    async def test_model_groups_siblings_into_one_node(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        model = scripted_model({"bug bash testing": ("same_as", "Bug bash")})
        run = pipeline("apply")
        meta = metadata_factory(categories=["QA"], topics=["Bug bash", "Bug bash testing"])
        await run(ctx_factory("f1", "fresh", meta))
        assert len(model.calls) == 1
        key = k("fresh", TOPICS, "bug bash")
        assert fake_graph.node(TOPICS, key)["aliases"] == ["Bug bash testing"]
        assert len(fake_graph.nodes_in(TOPICS)) == 1
        (edge,) = fake_graph.edges_from("f1", BELONGS_TO_TOPIC)
        assert edge["extractedName"] == "Bug bash"
        assert meta.topics == ["Bug bash"]
        assert fake_store.point("fresh", "topic", key)["aliases"] == ["Bug bash testing"]


class TestFailures:
    async def test_vector_store_down_still_enriches_with_new_nodes(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        model = scripted_model()
        fake_store.fail_matches = True
        meta = metadata_factory(categories=["QA"], topics=["Bug bash testing session"])
        resolution = await run(ctx_factory("r2", "acme", meta))
        assert resolution.stats.vector_failures == 2
        assert model.calls == []
        assert (TOPICS, k("acme", TOPICS, "bug bash testing session")) in fake_graph.nodes
        assert len(fake_graph.edges_from("r2", BELONGS_TO_TOPIC)) == 1

    async def test_model_down_makes_names_new(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        model = scripted_model()
        model.raise_error = True
        meta = metadata_factory(categories=["QA"], topics=["Bug bash testing session"])
        resolution = await run(ctx_factory("r2", "acme", meta))
        assert resolution.stats.model_failures == 1
        assert (TOPICS, k("acme", TOPICS, "bug bash testing session")) in fake_graph.nodes
        assert (CATEGORIES, k("acme", CATEGORIES, "qa")) in fake_graph.nodes

    async def test_bad_target_is_rejected(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        scripted_model({"bug bash testing session": "bad_target"})
        meta = metadata_factory(categories=["Quality Assurance"], topics=["Bug bash testing session"])
        resolution = await run(ctx_factory("r2", "acme", meta))
        assert resolution.stats.rejected_decisions == 1
        assert (TOPICS, k("acme", TOPICS, "bug bash testing session")) in fake_graph.nodes
        assert fake_graph.node(TOPICS, k("acme", TOPICS, "bug bash testing"))["aliases"] == []

    async def test_graph_down_fails_enrichment_before_any_point_is_written(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        run = pipeline("apply")
        fake_graph.fail_find = True
        with pytest.raises(RuntimeError):
            await run(ctx_factory("r1", "acme", metadata_factory(categories=["QA"], topics=["x y"])))
        assert fake_store.upserts == []
        assert fake_graph.nodes == {}


class TestShadowAndIdempotency:
    async def test_shadow_mode_leaves_legacy_write_path_untouched(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        run = pipeline("shadow")
        meta = metadata_factory(categories=["QA"], topics=["Bug bash testing"])
        ctx = ctx_factory("r1", "acme", meta)
        await run(ctx)
        assert ctx.entity_resolution is None
        assert meta.topics == ["Bug bash testing"]
        (topic,) = fake_graph.nodes_in(TOPICS)
        assert "orgId" not in topic and topic["name"] == "Bug bash testing"
        assert not any(name == "create_taxonomy_node_if_absent" for name, _ in fake_graph.calls)
        assert len(fake_store.points) == 2

    async def test_rerunning_the_same_record_changes_nothing(
        self, pipeline, fake_graph, fake_store, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        run, _ = await _seed_r1(pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model)
        nodes_before = {key: dict(v) for key, v in fake_graph.nodes.items()}
        edges_before = set(fake_graph.edges)
        points_before = len(fake_store.points)
        scripted_model()
        await run(ctx_factory("r1", "acme", _r1_metadata(metadata_factory)))
        assert {key: dict(v) for key, v in fake_graph.nodes.items()} == nodes_before
        assert set(fake_graph.edges) == edges_before
        assert len(fake_store.points) == points_before

    async def test_concurrent_records_with_the_same_new_name_converge(
        self, pipeline, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        run = pipeline("apply")
        ctx_a = ctx_factory("a1", "acme", metadata_factory(categories=["Ops"], topics=["Onboarding checklist"]))
        ctx_b = ctx_factory("b1", "acme", metadata_factory(categories=["Ops"], topics=["onboarding checklist"]))
        await asyncio.gather(run(ctx_a), run(ctx_b))
        key = k("acme", TOPICS, "onboarding checklist")
        assert len(fake_graph.nodes_in(TOPICS)) == 1
        assert len(fake_graph.edges_to(f"{TOPICS}/{key}")) == 2

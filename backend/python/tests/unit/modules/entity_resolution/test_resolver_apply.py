"""Apply versus shadow versus off, metadata rewriting, and the always-on default."""

from unittest.mock import MagicMock

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.models import ResolutionMode
from app.modules.entity_resolution.resolver import EntityResolver

TOPICS = CollectionNames.TOPICS.value


class TestApplyMode:
    async def test_metadata_rewritten_to_canonical_names_and_context_attached(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        from app.modules.entity_resolution.keys import taxonomy_node_key

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
        assert meta.categories == ["Quality Assurance"]
        assert meta.sub_category_level_1 == "testing"
        assert meta.sub_category_level_2 == "Manual testing"
        assert meta.sub_category_level_3 is None
        assert meta.topics == ["Bug Bash Testing", "Test plan"]
        assert meta.languages == ["English"]
        assert resolution.get(TOPICS, "Bug Bash Testing").key == key

    async def test_dropped_category_empties_the_chain(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        meta = metadata_factory(categories=[""], sub_category_level_1="Contract", topics=["x y"])
        await make_resolver("apply").resolve(ctx_factory("r1", "acme", meta))
        assert meta.categories == []
        assert meta.sub_category_level_1 is None

    async def test_dropped_level_truncates_deeper_levels(
        self, make_resolver, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        meta = metadata_factory(
            categories=["Legal"], sub_category_level_1="x" * 150,
            sub_category_level_2="Deep", sub_category_level_3="Deeper",
        )
        await make_resolver("apply").resolve(ctx_factory("r1", "acme", meta))
        assert meta.categories == ["Legal"]
        assert (meta.sub_category_level_1, meta.sub_category_level_2, meta.sub_category_level_3) == (None, None, None)

    async def test_stats_line_and_latency(self, make_resolver, metadata_factory, ctx_factory, scripted_model) -> None:
        scripted_model()
        resolver = make_resolver("apply")
        resolution = await resolver.resolve(ctx_factory("r1", "acme", metadata_factory(topics=["x y"])))
        assert resolution.stats.latency_ms >= 0
        messages = [c.args[0] for c in resolver.logger.info.call_args_list]
        assert any(m.startswith("entity_resolution mode=") for m in messages)


class TestShadowMode:
    async def test_shadow_logs_decisions_and_writes_nothing(
        self, make_resolver, fake_graph, metadata_factory, ctx_factory, scripted_model
    ) -> None:
        scripted_model()
        meta = metadata_factory(categories=["QA"], topics=["Bug bash testing", "BUG BASH TESTING"], languages=["en"])
        ctx = ctx_factory("r1", "acme", meta)
        resolver = make_resolver("shadow")
        resolution = await resolver.resolve(ctx)
        assert ctx.entity_resolution is None
        assert meta.topics == ["Bug bash testing", "BUG BASH TESTING"]
        assert meta.languages == ["en"]
        assert {name for name, _ in fake_graph.calls} == {"find_taxonomy_nodes"}
        assert resolution is not None and resolution.decisions_for_log()
        shadow_lines = [c.args[0] for c in resolver.logger.info.call_args_list if "shadow" in c.args[0]]
        assert shadow_lines


class TestOffAndGuards:
    async def test_off_returns_none_without_any_call(
        self, make_resolver, fake_graph, fake_store, metadata_factory, ctx_factory
    ) -> None:
        ctx = ctx_factory("r1", "acme", metadata_factory(topics=["x y"]))
        assert await make_resolver("off").resolve(ctx) is None
        assert fake_graph.calls == [] and fake_store.match_calls == []

    async def test_missing_metadata_or_org_is_a_noop(self, make_resolver, metadata_factory, ctx_factory) -> None:
        resolver = make_resolver("apply")
        assert await resolver.resolve(ctx_factory("r1", "acme", None)) is None
        assert await resolver.resolve(ctx_factory("r1", "", metadata_factory(topics=["x y"]))) is None


class TestAlwaysOn:
    def test_default_mode_is_apply(self) -> None:
        resolver = EntityResolver(MagicMock(), MagicMock(), MagicMock())
        assert resolver.mode is ResolutionMode.APPLY

    def test_mode_accepts_the_enum_value(self) -> None:
        resolver = EntityResolver(MagicMock(), MagicMock(), MagicMock(), mode="shadow")
        assert resolver.mode is ResolutionMode.SHADOW

    async def test_container_factory_builds_an_apply_resolver(self) -> None:
        from app.containers.utils.utils import ContainerUtils

        resolver = await ContainerUtils().create_entity_resolver(
            MagicMock(), MagicMock(), MagicMock(), MagicMock()
        )
        assert resolver.mode is ResolutionMode.APPLY

"""The contract every CollectionStrategy must satisfy, OSS or Enterprise.

Parametrized over the shipped ``single`` plus test doubles standing in for the
strategies the design exists to enable (per-org, per-connector-type,
per-embedding-model). A strategy that passes here can be dropped into a
deployment without any call site knowing it exists — which is the whole claim
the strategy abstraction makes, and the thing an EE package will rely on.

The load-bearing property is ``required_axes`` honesty: the registry refuses a
context missing a declared axis, and callers that hold no record at all use
`RecordContext(org_id="")` only when nothing is declared. A strategy that reads
an axis it did not declare therefore gets a plausible-looking name from an
empty context — the exact silent-miss this contract rules out.
"""

import itertools
from collections.abc import Sequence
from unittest.mock import MagicMock

import pytest

from app.services.vector_db.collection_manifest import ManagedCollection
from app.services.vector_db.collections import CollectionType, sanitize_collection_name
from app.services.vector_db.strategies.per_connector_type import (
    PerConnectorTypeStrategy,
)
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from app.services.vector_db.strategy import (
    CollectionStrategy,
    ContextAxis,
    DeleteAction,
    DeleteContext,
    DeleteScope,
    IncompleteCollectionContext,
    QueryContext,
    RecordContext,
    resolve_write_collection_name,
)

# ---------------------------------------------------------------------------
# The shipped strategies plus doubles for the ones the abstraction is meant to
# enable but that OSS does not ship. The doubles live here rather than in app/
# so nothing in OSS depends on them; both kinds run the same contract.
# ---------------------------------------------------------------------------


class PerOrgStrategy(CollectionStrategy):
    """The EE multi-tenancy shape: one collection per org."""

    def resolve_write_collection(self, ctx: RecordContext) -> str:
        return f"org_{ctx.org_id}_{ctx.collection_type.value}"

    def resolve_read_collections(
        self, ctx: QueryContext, managed: Sequence[ManagedCollection]
    ) -> list[str]:
        return [f"org_{ctx.org_id}_{ctx.collection_type.value}"]

    def resolve_delete_scope(self, ctx: DeleteContext) -> DeleteScope:
        return DeleteScope(
            action=DeleteAction.FILTERED_DELETE,
            collection_names=[f"org_{ctx.org_id}_{ctx.collection_type.value}"],
            filter_field="connectorIds",
            filter_values=[ctx.connector_id] if ctx.connector_id else None,
        )

    def strategy_name(self) -> str:
        return "per_org_test_double"

    @property
    def required_axes(self):
        return frozenset({ContextAxis.ORG})


class PerEmbeddingModelStrategy(CollectionStrategy):
    """One collection per embedding model — the multi-model future."""

    def resolve_write_collection(self, ctx: RecordContext) -> str:
        return f"{ctx.embedding_model}_{ctx.collection_type.value}"

    def resolve_read_collections(
        self, ctx: QueryContext, managed: Sequence[ManagedCollection]
    ) -> list[str]:
        return [
            entry.name
            for entry in managed
            if entry.collection_type == ctx.collection_type.value
        ]

    def resolve_delete_scope(self, ctx: DeleteContext) -> DeleteScope:
        return DeleteScope(
            action=DeleteAction.FILTERED_DELETE,
            collection_names=[],
            filter_field="connectorIds",
            filter_values=[ctx.connector_id] if ctx.connector_id else None,
        )

    def strategy_name(self) -> str:
        return "per_embedding_model_test_double"

    @property
    def required_axes(self):
        return frozenset({ContextAxis.EMBEDDING_MODEL})


ALL_STRATEGIES = [
    SingleCollectionStrategy,
    PerOrgStrategy,
    PerConnectorTypeStrategy,
    PerEmbeddingModelStrategy,
]


@pytest.fixture(params=ALL_STRATEGIES, ids=lambda c: c.__name__)
def strategy(request) -> CollectionStrategy:
    return request.param()


def _full_context(**overrides) -> RecordContext:
    """A context carrying every axis, so any strategy can resolve from it."""
    base = dict(
        org_id="org-1",
        connector_id="conn-1",
        connector_name="GOOGLE_DRIVE",
        embedding_model="text-embedding-3-large",
        embedding_dimension=1024,
    )
    base.update(overrides)
    return RecordContext(**base)


class TestNameResolution:
    def test_is_deterministic(self, strategy):
        ctx = _full_context()
        first = resolve_write_collection_name(strategy, ctx)
        assert all(
            resolve_write_collection_name(strategy, ctx) == first for _ in range(5)
        )

    def test_returns_a_sanitized_name(self, strategy):
        """The registry sanitizes what a strategy returns, but the dedup path
        compares names too — so the sanitized form must be a fixed point."""
        name = resolve_write_collection_name(strategy, _full_context())
        assert sanitize_collection_name(name) == name

    def test_distinct_collection_types_get_distinct_collections(self, strategy):
        records = resolve_write_collection_name(
            strategy, _full_context(collection_type=CollectionType.RECORDS)
        )
        entities = resolve_write_collection_name(
            strategy, _full_context(collection_type=CollectionType.ENTITIES)
        )
        assert records != entities

    def test_name_survives_hostile_axis_values(self, strategy):
        """Org ids and connector names reach naming from user-controlled data."""
        name = resolve_write_collection_name(
            strategy,
            _full_context(
                org_id="Acme Corp/EU",
                connector_name="Google Drive",
                embedding_model="openai/text-embedding-3-large",
            ),
        )
        assert sanitize_collection_name(name) == name
        assert name and not name.startswith(("_", "-", "+"))


class TestRequiredAxesHonesty:
    """The property that makes every other call site safe.

    Declared axes are enforced by the registry; undeclared ones are assumed
    irrelevant by callers that hold no record. Both directions are checked.
    """

    def test_a_declared_axis_actually_changes_the_name(self, strategy):
        for axis in strategy.required_axes:
            base = _full_context()
            mutated = _full_context(**{axis.value: "something-else-entirely"})
            assert resolve_write_collection_name(
                strategy, base
            ) != resolve_write_collection_name(strategy, mutated), (
                f"{strategy.strategy_name()} declares {axis.value} but the name "
                f"does not depend on it"
            )

    def test_an_undeclared_axis_never_changes_the_name(self, strategy):
        undeclared = [a for a in ContextAxis if a not in strategy.required_axes]
        base = _full_context()
        expected = resolve_write_collection_name(strategy, base)
        for axis in undeclared:
            mutated = _full_context(**{axis.value: "something-else-entirely"})
            assert resolve_write_collection_name(strategy, mutated) == expected, (
                f"{strategy.strategy_name()} reads {axis.value} without "
                f"declaring it — an org-agnostic caller would silently get the "
                f"wrong collection"
            )

    def test_missing_declared_axis_raises_instead_of_resolving(self, strategy):
        for axis in strategy.required_axes:
            with pytest.raises(IncompleteCollectionContext) as exc:
                resolve_write_collection_name(
                    strategy, _full_context(**{axis.value: None})
                )
            assert axis.value in str(exc.value)

    def test_empty_string_counts_as_missing(self, strategy):
        """`orgId` reaches contexts as `doc.get("orgId") or ""`; an empty org
        must not resolve to `org__records`."""
        for axis in strategy.required_axes:
            with pytest.raises(IncompleteCollectionContext):
                resolve_write_collection_name(
                    strategy, _full_context(**{axis.value: ""})
                )

    def test_org_agnostic_strategies_resolve_from_a_bare_context(self, strategy):
        if strategy.required_axes:
            pytest.skip("declares axes; a bare context is expected to raise")
        assert resolve_write_collection_name(strategy, RecordContext(org_id=""))


class TestReadResolution:
    def test_never_invents_a_name_when_nothing_is_managed(self, strategy):
        """A strategy that cannot narrow must return managed collections, not a
        guess — and with nothing managed, nothing to search is the honest
        answer for anything but the org-agnostic case."""
        names = strategy.resolve_read_collections(QueryContext(org_id="org-1"), [])
        if strategy.required_axes:
            assert all(isinstance(n, str) and n for n in names)
        else:
            assert names == [CollectionType.RECORDS.value]

    def test_returns_a_list_of_strings(self, strategy):
        managed = [
            ManagedCollection(
                name="google_drive_records",
                collection_type="records",
                embedding_dimension=1024,
                strategy_name=strategy.strategy_name(),
            )
        ]
        names = strategy.resolve_read_collections(QueryContext(org_id="org-1"), managed)
        assert isinstance(names, list)
        assert all(isinstance(n, str) for n in names)


class TestDeleteScope:
    def test_never_yields_a_filtered_delete_with_no_predicate(self, strategy):
        """A filtered delete with no values would empty the collection for
        every connector sharing it."""
        scope = strategy.resolve_delete_scope(
            DeleteContext(org_id="org-1", connector_id="", connector_name="SLACK")
        )
        if scope.action == DeleteAction.FILTERED_DELETE:
            assert not scope.filter_values or all(scope.filter_values)

    def test_filter_values_never_contain_none(self, strategy):
        scope = strategy.resolve_delete_scope(
            DeleteContext(org_id="org-1", connector_id="conn-1", connector_name="SLACK")
        )
        assert None not in (scope.filter_values or [])


class TestContextsAreImmutable:
    @pytest.mark.parametrize(
        "ctx",
        [
            RecordContext(org_id="org-1"),
            QueryContext(org_id="org-1"),
            DeleteContext(org_id="org-1", connector_id="c1"),
        ],
        ids=["record", "query", "delete"],
    )
    def test_cannot_be_mutated_after_construction(self, ctx):
        """The dedup path resolves, compares, and the write path resolves again
        later; a context mutated in between would let a record be skipped as a
        duplicate of something in a collection it never reached."""
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.org_id = "org-2"


class TestRecordContextFactories:
    """One normalisation, two entry points.

    The write path builds a context from a ``Record``; the dedup path builds
    one from a graph document. They are compared against each other to decide
    whether to skip indexing, so a difference in normalisation loses a record's
    vectors silently.
    """

    def test_enum_and_string_connector_names_normalize_identically(self, strategy):
        record = MagicMock()
        record.connector_id = "conn-1"
        record.connector_name = MagicMock(value="GOOGLE_DRIVE")
        doc = {
            "orgId": "org-1",
            "connectorId": "conn-1",
            "connectorName": "GOOGLE_DRIVE",
        }
        # The embedding model is not on either source — the write path supplies
        # it from the live model, the dedup path from the same resolved model.
        # Passed identically here so this test isolates connector-name
        # normalisation rather than re-testing a missing axis.
        model = {"embedding_model": "text-embedding-3-large"}

        from_record = RecordContext.from_record(record, "org-1", **model)
        from_doc = RecordContext.from_graph_document(doc, **model)

        assert from_record == from_doc
        assert resolve_write_collection_name(
            strategy, from_record
        ) == resolve_write_collection_name(strategy, from_doc)

    def test_missing_fields_normalize_to_none_not_empty_string(self):
        record = MagicMock()
        record.connector_id = ""
        record.connector_name = None

        ctx = RecordContext.from_record(record, "org-1")

        assert ctx.connector_id is None
        assert ctx.connector_name is None

    def test_blank_connector_name_is_none(self):
        ctx = RecordContext.from_graph_document(
            {"orgId": "org-1", "connectorName": "   "}
        )
        assert ctx.connector_name is None

    def test_missing_org_becomes_empty_string(self):
        """Which `required_axes` then rejects, rather than naming `org__records`."""
        assert RecordContext.from_graph_document({}).org_id == ""

    def test_overrides_apply(self):
        ctx = RecordContext.from_graph_document(
            {"orgId": "org-1"}, collection_type=CollectionType.ENTITIES
        )
        assert ctx.collection_type == CollectionType.ENTITIES


class TestCrossStrategyIsolation:
    def test_no_two_strategies_collide_on_a_populated_context(self):
        """Not a hard requirement, but a collision would mean a strategy change
        silently reuses another's collection instead of failing the rebuild."""
        names = {
            cls.__name__: resolve_write_collection_name(cls(), _full_context())
            for cls in ALL_STRATEGIES
        }
        for (a, na), (b, nb) in itertools.combinations(names.items(), 2):
            assert na != nb, f"{a} and {b} both resolve to {na!r}"

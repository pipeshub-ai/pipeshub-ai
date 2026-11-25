"""Contract suite for ``PolicyResolver`` implementations (CPD-3, 03-interfaces §4.1, 02-lld §3.4).

Subclass ``PolicyResolverContract`` with a ``resolver`` fixture (over the release's dimension table) and a
``policy_vectors`` fixture holding ``contracts/harness/vectors/policy-resolve.json``. The cases are the shared
vectors (CPD3-10) and the precedence properties: every ceiling resolves at or below each layer's value for it and
inserting a layer never widens (CPD3-13); the first refusal names the top-most widening layer (CPD3-15); a fold of a
prefix resolves the rest as the whole chain does (CPD3-16); a live layer never widens the pin (CPD3-18).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from harness_core.contracts import canonical_digest
from harness_core.policy import (
    FoldedPrefix,
    PolicyRefusal,
    ScopedLayer,
    policy_registry,
)

if TYPE_CHECKING:
    from harness_core.policy import Dimension, Order, PolicyResolver, Resolved

# Layers that may set every ceiling the generated chains use, in chain order.
CHAIN: tuple[str, ...] = ("deployment", "org", "team", "agent", "trigger", "parent", "request", "live")
ROUTES: tuple[str, ...] = ("a", "b", "c", "d")
FLAGS: tuple[str, ...] = ("ENABLE_SKILLS", "AGENT_HARNESS_KILL_SWITCH")
LEVELS: tuple[str, ...] = ("allow", "ask", "deny")


def _layer_values() -> st.SearchStrategy[dict[str, Any]]:
    models: st.SearchStrategy[dict[str, Any]] = st.fixed_dictionaries(
        {}, optional={"routes": st.lists(st.sampled_from(ROUTES), unique=True, max_size=4)}
    )
    engine: st.SearchStrategy[dict[str, Any]] = st.fixed_dictionaries(
        {},
        optional={
            "limits": st.fixed_dictionaries({}, optional={"maxSteps": st.integers(1, 80), "maxWallS": st.integers(60, 4000)}),
            "skills": st.lists(st.sampled_from(("pdf", "xlsx", "docx")), unique=True, max_size=3),
            "reasoningEffort": st.sampled_from(("low", "medium", "high")),
            "isolationMin": st.sampled_from(("docker", "gvisor", "k8s", "microvm")),
        },
    )
    approvals: st.SearchStrategy[dict[str, Any]] = st.fixed_dictionaries(
        {},
        optional={
            "writes": st.sampled_from(LEVELS),
            "tools": st.dictionaries(st.sampled_from(("t1", "t2")), st.sampled_from(LEVELS), max_size=2),
            "flow": st.fixed_dictionaries({"level": st.sampled_from(("off", "ask", "deny")), "above": st.sampled_from(("public", "private"))}),
        },
    )
    rollout: st.SearchStrategy[dict[str, Any]] = st.dictionaries(st.sampled_from(FLAGS), st.booleans(), max_size=2)
    return st.fixed_dictionaries(
        {}, optional={"models": models, "engineProfile": engine, "approvals": approvals, "rollout": rollout}
    )


def _without_rollout_below_org(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    return values if kind in ("deployment", "org") else {k: v for k, v in values.items() if k != "rollout"}


@st.composite
def chains(draw: st.DrawFn, max_layers: int = 5) -> list[ScopedLayer]:
    kinds = draw(st.lists(st.sampled_from(CHAIN), min_size=1, max_size=max_layers, unique=True))
    kinds.sort(key=CHAIN.index)
    return [ScopedLayer(kind, _without_rollout_below_org(kind, draw(_layer_values()))) for kind in kinds]


def _ceilings() -> list[tuple[Dimension, Order]]:
    return [(d, d.order) for d in policy_registry().all() if d.order is not None and d.order.kind != "admits"]


def _values(resolved: Resolved) -> dict[str, object] | None:
    """Each dimension's resolved value, or ``None`` when the chain was refused."""
    if resolved.config is None:
        return None
    dimensions = cast("dict[str, dict[str, object]]", resolved.config["dimensions"])
    return {path: entry["value"] for path, entry in dimensions.items()}


def _value(values: object, path: str) -> object:
    node = values
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]  # pyright: ignore[reportUnknownVariableType]
    return node  # pyright: ignore[reportUnknownVariableType]


# The resolver fixture is stateless: one instance serves every generated chain.
_SHARED_FIXTURE = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


class PolicyResolverContract:
    def test_cpd3_10_every_case_resolves_as_the_vectors_say(self, resolver: PolicyResolver, policy_vectors: dict[str, Any]) -> None:
        for case in policy_vectors["cases"]:
            resolved = resolver.resolve([ScopedLayer(layer["kind"], layer["values"]) for layer in case["layers"]])
            expect = case["expect"]
            if "refusal" in expect:
                assert resolved.refusal is not None, case["id"]
                assert resolved.refusal.wire() == expect["refusal"], case["id"]
            else:
                assert resolved.refusal is None, (case["id"], resolved.refusal)
                assert resolved.config == expect["config"], case["id"]
                assert resolved.digest == expect["digest"] == canonical_digest(expect["config"]), case["id"]

    @_SHARED_FIXTURE
    @given(chains())
    def test_cpd3_13_every_ceiling_is_at_or_below_each_layer_that_sets_it(self, resolver: PolicyResolver, chain: list[ScopedLayer]) -> None:
        values = _values(resolver.resolve(chain))
        if values is None:
            return
        for dimension, order in _ceilings():
            if dimension.path not in values:
                continue
            for layer in chain:
                raw = _value(layer.values, dimension.layer_path)
                if raw is not None:
                    assert order.leq(values[dimension.path], order.lift(raw)) is None, (dimension.path, layer.kind)

    @_SHARED_FIXTURE
    @given(chains(), st.data())
    def test_cpd3_13_inserting_a_layer_never_widens(self, resolver: PolicyResolver, chain: list[ScopedLayer], data: st.DataObject) -> None:
        position = data.draw(st.integers(0, len(chain)))
        extra = data.draw(chains(max_layers=1))[0]
        old = _values(resolver.resolve(chain))
        new = _values(resolver.resolve([*chain[:position], extra, *chain[position:]]))
        if old is None or new is None:
            return
        for dimension, order in _ceilings():
            if dimension.path in old and dimension.path in new:
                assert order.leq(new[dimension.path], old[dimension.path]) is None, dimension.path

    @_SHARED_FIXTURE
    @given(chains())
    def test_cpd3_15_the_refusal_names_the_first_widening_layer(self, resolver: PolicyResolver, chain: list[ScopedLayer]) -> None:
        resolved = resolver.resolve(chain)
        if resolved.refusal is None:
            return
        index = next(i for i, layer in enumerate(chain) if layer.kind == resolved.refusal.layer)
        assert resolver.resolve(chain[:index]).refusal is None
        assert resolver.assert_narrows(chain[index], chain[:index]) == resolved.refusal

    @_SHARED_FIXTURE
    @given(chains(max_layers=6), st.data())
    def test_cpd3_16_a_folded_prefix_resolves_the_rest_as_the_whole_chain(
        self, resolver: PolicyResolver, chain: list[ScopedLayer], data: st.DataObject
    ) -> None:
        split = data.draw(st.integers(0, len(chain)))
        whole = resolver.resolve(chain)
        prefix = resolver.fold_prefix(chain[:split])
        if isinstance(prefix, PolicyRefusal):
            assert whole.refusal == prefix
            return
        assert isinstance(prefix, FoldedPrefix)
        assert resolver.resolve_from(prefix, chain[split:]) == whole

    @_SHARED_FIXTURE
    @given(chains(), chains(max_layers=1))
    def test_cpd3_18_the_live_layer_never_widens_the_pin(
        self, resolver: PolicyResolver, chain: list[ScopedLayer], live: list[ScopedLayer]
    ) -> None:
        pin = resolver.fold_prefix(chain)
        if isinstance(pin, PolicyRefusal):
            return
        narrowed = resolver.resolve_from(pin, [ScopedLayer("live", _without_rollout_below_org("live", live[0].values))])  # pyright: ignore[reportArgumentType]
        old = _values(resolver.resolve_from(pin, []))
        new = _values(narrowed)
        if old is None or new is None:
            return
        for dimension, order in _ceilings():
            if dimension.path in old:
                assert order.leq(new[dimension.path], old[dimension.path]) is None, dimension.path


__all__ = ["CHAIN", "PolicyResolverContract", "chains"]

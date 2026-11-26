"""Contract suites for the egress proxy's ports (P5.4 BC, TC, RC).

Subclass and provide the fixture each suite names: ``egress_bindings``
(``EgressBindings``; also for ``EgressRevocationsContract``, an adapter of both ports), ``token_source`` plus ``token_clock`` (``TokenSource``
and the clock its issuer reads; optionally ``token_binding``, the binding a
sandbox of that adapter carries), ``host_resolver`` (``HostResolver``) plus
``resolvable_host`` (a name that adapter resolves), ``state_store``
(``EgressStateStore``, empty) plus ``state_elapse`` (an async callable that lets
that many seconds pass for the store's TTL).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

import pytest

from harness_core.sandbox.egress.bindings import EGRESS_REVOKED, Revocation
from harness_core.sandbox.egress.errors import HostUnresolvable
from harness_core.sandbox.egress.types import EgressBinding
from harness_core.sandbox.errors import InvalidSandboxSpec, SandboxRefusal
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.clock import Clock
    from harness_core.sandbox.egress.ports import (
        EgressBindings,
        EgressStateStore,
        HostResolver,
        TokenSource,
    )

_PH = "ph-contract-0123456789"
_POLICY = EgressPolicy(
    destinations=(EgressDestination("model-gateway", (8097,)), EgressDestination("tool-gateway", (8097,))),
    token_routes=(TokenRoute(_PH, "model-gateway", "gateway:model"), TokenRoute(_PH + "-t", "tool-gateway", "gateway:tools")),
)


def contract_binding(sandbox_id: str, index: int, policy: EgressPolicy = _POLICY) -> EgressBinding:
    return EgressBinding(sandbox_id, f"10.213.{index}.2", f"10.213.{index}.3", policy, {"runId": f"run-{sandbox_id}"})


class _Both(Protocol):
    async def register(self, binding: EgressBinding) -> None: ...

    async def unregister(self, sandbox_id: str) -> None: ...

    async def bound(self) -> frozenset[str]: ...

    async def revoke(self, revocation: Revocation) -> tuple[str, ...]: ...


def _run_binding(sandbox_id: str, index: int, org: str, session: str, *, run: bool = True) -> EgressBinding:
    policy = EgressPolicy(
        destinations=_POLICY.destinations, token_routes=(TokenRoute(f"{_PH}-{sandbox_id}", "model-gateway", "m"),)
    )
    labels = {"orgId": org, "sessionId": session, **({"runId": f"r-{sandbox_id}", "attemptId": "a1"} if run else {})}
    return EgressBinding(sandbox_id, f"10.213.{index}.2", f"10.213.{index}.3", policy, labels)


class EgressRevocationsContract:
    """RV (C6.14): an incident stop unbinds the live bindings of harness runs it names, for good."""

    async def test_rv1_a_revocation_unbinds_only_the_runs_it_names(self, egress_bindings: object) -> None:
        bindings = cast("_Both", egress_bindings)
        items = [
            _run_binding("sbx-1", 1, "org-a", "ses-1"),
            _run_binding("sbx-2", 2, "org-a", "ses-2"),
            _run_binding("sbx-3", 3, "org-b", "ses-3"),
            _run_binding("sbx-4", 4, "org-a", "ses-1", run=False),
        ]
        for item in items:
            await bindings.register(item)
        try:
            assert await bindings.revoke(Revocation(org_id="org-a", session_id="ses-1")) == ("sbx-1",)
            assert await bindings.revoke(Revocation(org_id="org-a")) == ("sbx-2",)
            assert await bindings.bound() == {"sbx-3", "sbx-4"}
            assert await bindings.revoke(Revocation(org_id="org-none")) == ()
        finally:
            for sandbox_id in ("sbx-3", "sbx-4"):
                await bindings.unregister(sandbox_id)

    async def test_rv2_a_revoked_sandbox_is_never_bound_again(self, egress_bindings: object) -> None:
        bindings = cast("_Both", egress_bindings)
        item = _run_binding("sbx-1", 1, "org-a", "ses-1")
        await bindings.register(item)
        assert await bindings.revoke(Revocation()) == ("sbx-1",)
        with pytest.raises(SandboxRefusal) as refused:
            await bindings.register(item)
        assert refused.value.reason == EGRESS_REVOKED
        assert await bindings.bound() == frozenset()


class EgressBindingsContract:
    async def test_bc1_register_is_idempotent(self, egress_bindings: EgressBindings) -> None:
        await egress_bindings.register(contract_binding("sbx-1", 1))
        await egress_bindings.register(contract_binding("sbx-1", 1))
        assert await egress_bindings.bound() == {"sbx-1"}
        await egress_bindings.unregister("sbx-1")

    async def test_bc2_a_placeholder_held_by_another_live_sandbox_is_refused(self, egress_bindings: EgressBindings) -> None:
        await egress_bindings.register(contract_binding("sbx-1", 1))
        try:
            with pytest.raises(InvalidSandboxSpec) as refused:
                await egress_bindings.register(contract_binding("sbx-2", 2))
            assert refused.value.reason == "placeholder_in_use"
            assert await egress_bindings.bound() == {"sbx-1"}
        finally:
            await egress_bindings.unregister("sbx-1")

    async def test_bc3_unregister_is_idempotent_and_frees_the_placeholder(self, egress_bindings: EgressBindings) -> None:
        await egress_bindings.register(contract_binding("sbx-1", 1))
        await egress_bindings.unregister("sbx-1")
        await egress_bindings.unregister("sbx-1")
        await egress_bindings.register(contract_binding("sbx-2", 2))
        assert await egress_bindings.bound() == {"sbx-2"}
        await egress_bindings.unregister("sbx-2")

    async def test_bc4_re_registering_a_sandbox_replaces_its_binding(self, egress_bindings: EgressBindings) -> None:
        await egress_bindings.register(contract_binding("sbx-1", 1))
        changed = EgressPolicy(destinations=_POLICY.destinations, token_routes=(TokenRoute(_PH + "-new", "model-gateway", "m"),))
        await egress_bindings.register(contract_binding("sbx-1", 1, changed))
        await egress_bindings.register(contract_binding("sbx-2", 2))
        assert await egress_bindings.bound() == {"sbx-1", "sbx-2"}
        for sandbox_id in ("sbx-1", "sbx-2"):
            await egress_bindings.unregister(sandbox_id)


    async def test_bc5_a_new_sandbox_on_a_stale_address_replaces_its_binding(self, egress_bindings: EgressBindings) -> None:
        other = EgressPolicy(destinations=_POLICY.destinations, token_routes=(TokenRoute(_PH + "-x", "model-gateway", "m"),))
        await egress_bindings.register(contract_binding("sbx-old", 1))
        await egress_bindings.register(contract_binding("sbx-new", 1, other))
        assert await egress_bindings.bound() == {"sbx-new"}
        await egress_bindings.register(contract_binding("sbx-3", 3))
        await egress_bindings.unregister("sbx-new")
        await egress_bindings.unregister("sbx-3")


class TokenSourceContract:
    @pytest.fixture
    def token_binding(self) -> Callable[[str, int], EgressBinding]:
        """A production source issues only for a sandbox bound to a live run attempt; it overrides this."""
        return contract_binding

    async def test_tc1_tokens_differ_per_sandbox(
        self, token_source: TokenSource, token_binding: Callable[[str, int], EgressBinding]
    ) -> None:
        # The port binds a token to the sandbox's run, not to the route: PipesHub's Node authority (E2) issues one
        # attempt token that both gateways verify, so distinct tokens per credential ref is the in-memory adapter's.
        a, b = token_binding("sbx-1", 1), token_binding("sbx-2", 2)
        model, tools = _POLICY.token_routes
        mine = {(await token_source.issue(model, a)).value, (await token_source.issue(tools, a)).value}
        theirs = {(await token_source.issue(model, b)).value, (await token_source.issue(tools, b)).value}
        assert mine.isdisjoint(theirs)

    async def test_tc2_a_token_expires_within_its_ttl(
        self, token_source: TokenSource, token_clock: Clock, token_binding: Callable[[str, int], EgressBinding]
    ) -> None:
        before = token_clock.now()
        issued = await token_source.issue(_POLICY.token_routes[0], token_binding("sbx-1", 1))
        after = token_clock.now()
        assert before < issued.expires_at
        assert (issued.expires_at - after).total_seconds() <= 600

    async def test_tc3_a_token_is_never_the_placeholder(
        self, token_source: TokenSource, token_binding: Callable[[str, int], EgressBinding]
    ) -> None:
        route = _POLICY.token_routes[0]
        issued = await token_source.issue(route, token_binding("sbx-1", 1))
        assert issued.value and route.placeholder not in issued.value


class HostResolverContract:
    async def test_rc1_an_ip_literal_resolves_to_itself(self, host_resolver: HostResolver) -> None:
        assert await host_resolver.resolve("93.184.216.34") == ("93.184.216.34",)
        assert await host_resolver.resolve("2606:4700:4700::1111") == ("2606:4700:4700::1111",)

    async def test_rc2_names_resolve_or_raise(self, host_resolver: HostResolver, resolvable_host: str) -> None:
        assert await host_resolver.resolve(resolvable_host)
        with pytest.raises(HostUnresolvable):
            _ = await host_resolver.resolve("no-such-host.invalid")


class EgressStateStoreContract:
    async def test_es1_put_overwrites_and_entries_list_everything(self, state_store: EgressStateStore) -> None:
        await state_store.put("binding/a", "v1", ttl_s=60)
        await state_store.put("binding/a", "v2", ttl_s=60)
        await state_store.put("grant/a/x", "g", ttl_s=None)
        assert dict(await state_store.entries()) == {"binding/a": "v2", "grant/a/x": "g"}

    async def test_es2_an_entry_past_its_ttl_is_gone(
        self, state_store: EgressStateStore, state_elapse: Callable[[float], Awaitable[None]]
    ) -> None:
        await state_store.put("binding/short", "v", ttl_s=1)
        await state_store.put("binding/long", "v", ttl_s=60)
        await state_elapse(2.5)
        assert dict(await state_store.entries()) == {"binding/long": "v"}

    async def test_es3_put_if_absent_keeps_the_first_value(self, state_store: EgressStateStore) -> None:
        assert await state_store.put_if_absent("instance", "first") == "first"
        assert await state_store.put_if_absent("instance", "second") == "first"
        await state_store.put("instance", "rotated", ttl_s=None)
        assert await state_store.put_if_absent("instance", "third") == "rotated"

    async def test_es4_delete_is_idempotent(self, state_store: EgressStateStore) -> None:
        await state_store.put("binding/a", "v", ttl_s=60)
        await state_store.delete("binding/a")
        await state_store.delete("binding/a")
        await state_store.delete("binding/never")
        assert await state_store.entries() == ()

    async def test_es5_replace_writes_only_over_the_expected_value_and_keeps_the_expiry(
        self, state_store: EgressStateStore, state_elapse: Callable[[float], Awaitable[None]]
    ) -> None:
        # ADR-KEY-01: the key rotation's reseal must neither undo a proxy's newer write nor make a record permanent.
        await state_store.put("binding/a", "old", ttl_s=2)
        assert await state_store.replace("binding/a", "stale", "new") is False
        assert await state_store.replace("binding/a", "old", "new") is True
        assert dict(await state_store.entries()) == {"binding/a": "new"}
        assert await state_store.replace("binding/never", "old", "new") is False
        assert dict(await state_store.entries()) == {"binding/a": "new"}
        await state_elapse(3.0)
        assert await state_store.entries() == ()
        assert await state_store.replace("binding/a", "new", "newer") is False
        assert await state_store.entries() == ()


__all__ = [
    "EgressBindingsContract",
    "EgressRevocationsContract",
    "EgressStateStoreContract",
    "HostResolverContract",
    "TokenSourceContract",
    "contract_binding",
]

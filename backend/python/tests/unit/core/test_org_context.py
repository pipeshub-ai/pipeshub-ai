"""OrgContext primitives: binding rules, fail-closed access, explicit task propagation."""

import asyncio

import pytest

from app.core.org_context import (
    MissingOrgContext,
    OrgContext,
    OrgMismatch,
    SystemContextNotAllowed,
    bind,
    current,
    current_or_system,
    for_org,
    spawn_detached,
    spawn_in_request,
    system,
)


def _ctx(org: str) -> OrgContext:
    return OrgContext(org, "u1", "user", "jwt", "req-1")


def test_current_without_context_fails_closed() -> None:
    with pytest.raises(MissingOrgContext):
        current()
    with pytest.raises(MissingOrgContext):
        current_or_system()


def test_bind_resets_and_nested_bind_must_keep_org() -> None:
    with bind(_ctx("org_a")):
        assert current().org_id == "org_a"
        with bind(_ctx("org_a")):
            pass
        with pytest.raises(OrgMismatch):
            with bind(_ctx("org_b")):
                pass
        assert current().org_id == "org_a"
    with pytest.raises(MissingOrgContext):
        current()


def test_only_system_kind_may_lack_an_org() -> None:
    with pytest.raises(ValueError):
        OrgContext(None, None, "user", "jwt", "r")
    with pytest.raises(ValueError):
        OrgContext("org_a", None, "system", "boot", "r")


def test_system_context_is_refused_by_current() -> None:
    with system("boot"):
        assert current_or_system().kind == "system"
        with pytest.raises(SystemContextNotAllowed):
            current()


async def test_per_org_job_iteration_does_not_leak_between_orgs() -> None:
    seen = []
    with system("purge"):
        for org in ("org_a", "org_b", "org_c"):
            try:
                with for_org(org, job="purge"):
                    seen.append(current().org_id)
                    if org == "org_a":
                        raise RuntimeError("org A iteration crashed")
            except RuntimeError:
                pass
        assert current_or_system().kind == "system"
    assert seen == ["org_a", "org_b", "org_c"]


async def test_context_flows_into_child_tasks_only_when_explicit() -> None:
    async def read() -> str:
        return current().org_id

    with bind(_ctx("org_a")):
        assert await spawn_in_request(read()) == "org_a"
        with pytest.raises(MissingOrgContext):
            await spawn_detached(read())


class _LazyClient:
    """Stands in for a broker/Redis client that starts a background loop when first used."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._events: asyncio.Queue = asyncio.Queue()
        self.task = None

    def start(self, start) -> None:
        self.task = start(self._loop())

    async def _loop(self) -> None:
        while True:
            await self._events.get()
            try:
                self.seen.append(current().org_id)
            except MissingOrgContext:
                self.seen.append("MissingOrgContext")

    async def feed(self, n) -> None:
        for _ in range(n):
            await self._events.put(object())
        while len(self.seen) < n:  # noqa: ASYNC110
            await asyncio.sleep(0)


async def test_lazy_client_created_in_request_never_captures_that_orgs_context() -> None:
    # the failure being guarded: a bare create_task copies request A's context
    leaky = _LazyClient()
    with bind(_ctx("org_a")):
        leaky.start(asyncio.get_running_loop().create_task)
    await leaky.feed(3)
    assert leaky.seen == ["org_a"] * 3  # fail-open: every later event is "org A"
    leaky.task.cancel()

    safe = _LazyClient()
    with bind(_ctx("org_a")):
        safe.start(spawn_detached)
    await safe.feed(3)
    assert safe.seen == ["MissingOrgContext"] * 3  # fail-closed until a wrapper binds an org
    safe.task.cancel()


def test_shadow_mode_logs_counts_and_lets_missing_and_mismatched_access_through(monkeypatch, caplog) -> None:
    from app.core import org_context as oc

    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    oc._logged.clear()
    before = oc.VIOLATIONS._metric.labels("missing", "site-a")._value.get()
    with caplog.at_level("WARNING"):
        assert oc.ambient_org("org_x", site="site-a") == "org_x"
        assert oc.ambient_org("org_x", site="site-a") == "org_x"
        with bind(_ctx("org_a")):
            assert oc.ambient_org("org_b", site="site-b") == "org_b"  # mismatch, still allowed
            assert oc.ambient_org("org_a", site="site-b") == "org_a"  # consistent: silent
            assert oc.ambient_org(None, site="site-b") == "org_a"
    assert oc.VIOLATIONS._metric.labels("missing", "site-a")._value.get() == before + 2
    assert caplog.text.count("org_context_missing site=site-a") == 1  # logged once per site
    assert "org_context_mismatch site=site-b" in caplog.text


def test_enforce_mode_fails_closed_on_missing_and_mismatched_access(monkeypatch) -> None:
    from app.core import org_context as oc

    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    with pytest.raises(MissingOrgContext):
        oc.ambient_org("org_x", site="s")
    with system("job"), pytest.raises(MissingOrgContext):
        oc.ambient_org("org_x", site="s")
    with bind(_ctx("org_a")):
        assert oc.ambient_org("org_a", site="s") == "org_a"
        with pytest.raises(OrgMismatch):
            oc.ambient_org("org_b", site="s")


async def test_graph_provider_methods_are_checked_at_the_org_id_argument(monkeypatch) -> None:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    class Fake(IGraphDBProvider):
        async def get_users(self, org_id, active=True) -> list:
            return [org_id]

        async def no_org(self) -> str:
            return "x"

    Fake.__abstractmethods__ = frozenset()  # only the wrapping is under test
    assert getattr(Fake.get_users, "_org_checked", False) and not getattr(Fake.no_org, "_org_checked", False)
    monkeypatch.delenv("ORG_CONTEXT_MODE", raising=False)
    with bind(_ctx("org_a")):
        assert await Fake().get_users("org_b") == ["org_b"]  # shadow: mismatch passes
        assert await Fake().get_users(org_id="org_a") == ["org_a"]
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    with bind(_ctx("org_a")):
        with pytest.raises(OrgMismatch):
            await Fake().get_users("org_b")
        assert await Fake().get_users("org_a") == ["org_a"]
    with pytest.raises(MissingOrgContext):
        await Fake().get_users("org_a")


def test_unknown_org_context_mode_is_rejected(monkeypatch) -> None:
    from app.core import org_context as oc

    for bad in ("enforced", "true", "off"):
        monkeypatch.setenv("ORG_CONTEXT_MODE", bad)
        with pytest.raises(ValueError):
            oc.validate_mode()
        with pytest.raises(ValueError):
            oc.ambient_org("org_x", site="s")
    monkeypatch.setenv("ORG_CONTEXT_MODE", " Enforce ")
    assert oc.validate_mode() is True
    monkeypatch.delenv("ORG_CONTEXT_MODE")
    assert oc.validate_mode() is False


def test_for_org_without_an_org_is_a_typed_error() -> None:
    for missing in (None, ""):
        with pytest.raises(MissingOrgContext), for_org(missing, job="j"):
            pass


def test_bootstrap_system_org_is_counted_apart_and_refused_in_enforce(monkeypatch) -> None:
    from app.core import org_context as oc

    with bind(OrgContext(oc.SYSTEM_ORG_ID, None, "service", "scoped_jwt", "r")):
        mismatches = oc.VIOLATIONS._metric.labels("mismatch", "s")._value.get()
        system = oc.VIOLATIONS._metric.labels("system_token", "s")._value.get()
        monkeypatch.setenv("ORG_CONTEXT_MODE", "shadow")
        assert oc.ambient_org("org_b", site="s") == "org_b"
        assert oc.VIOLATIONS._metric.labels("mismatch", "s")._value.get() == mismatches
        assert oc.VIOLATIONS._metric.labels("system_token", "s")._value.get() == system + 1
        monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
        with pytest.raises(OrgMismatch):
            oc.ambient_org("org_b", site="s")

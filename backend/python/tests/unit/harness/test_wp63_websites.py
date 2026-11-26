"""WP-63 on the worker: a run's allowed websites from the org's live layer, checked again, narrowed by the flow rule
and the conversation's classification level, and given to the engine's sandbox, shell and instructions.

Without an edition's layers (``edition_config.org_network_layers``) no list in the live layer opens a site.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override

import pytest

from app import edition_config
from app.harness.classification import ClassificationPort, NoClassification
from app.harness.websites import (
    FlowFacts,
    RunSites,
    live_sites,
    narrowed_for_flow,
    private_allow_names,
    site_destinations,
    sites_instructions,
)
from harness_core.edition import NO_HARNESS_EDITION, HarnessEdition
from harness_core.effects.flow import FlowRule
from harness_core.engines.sites import site_env
from harness_core.sandbox.egress.policy import policy_errors
from harness_core.sandbox.egress.trust import PROXY_URL, TRUST_BUNDLE
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    SiteAccess,
    SiteMode,
)
from harness_core.security.classification import UNCONFIGURED

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent
    from harness_core.security.classification import Classification, EgressRule

GITHUB = {"host": "docs.example.com", "access": "read"}
MIRROR = {"host": "mirror.example.org", "access": "read-write", "readWriteConfirmed": True}
WEBSITES = {"deployment": {"sites": [GITHUB]}, "org": {"sites": [GITHUB, MIRROR]}, "agents": {"a7": {"sites": [GITHUB]}}}


def test_c11_04_without_an_edition_s_layers_no_list_opens_a_site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_config, "org_network_layers", None, raising=False)
    member = cast("Mapping[str, JsonValue]", WEBSITES)
    assert live_sites(member, "a7") == []
    assert live_sites(member, None) == []
    assert live_sites(None, None) == []


@dataclass
class _Layers:
    seen: list[tuple[object, str | None]]

    def sites(self, websites: Mapping[str, JsonValue], agent_key: str | None) -> list[Mapping[str, JsonValue]]:
        self.seen.append((websites, agent_key))
        return [MIRROR]

    def repository_hosts(
        self, repositories: Mapping[str, JsonValue], agent_key: str | None, default: JsonValue | None
    ) -> list[Mapping[str, JsonValue]]:
        return []


def test_c11_04_an_edition_s_layers_fold_the_live_member(monkeypatch: pytest.MonkeyPatch) -> None:
    layers = _Layers([])
    monkeypatch.setattr(edition_config, "org_network_layers", layers, raising=False)
    member = cast("Mapping[str, JsonValue]", WEBSITES)
    assert live_sites(member, "a7") == [MIRROR]
    assert layers.seen == [(member, "a7")]
    # A layer that sets no member is not asked.
    assert live_sites(None, "a7") == []
    assert len(layers.seen) == 1


@pytest.mark.parametrize(
    "site",
    [
        {"host": "*.com", "access": "read"},
        {"host": "*.co.uk", "access": "read"},
        {"host": "*.github.io", "access": "read"},
        {"host": "10.0.0.1", "access": "read"},
        {"host": "metadata.google.internal", "access": "read"},
        {"host": "localhost", "access": "read"},
        {"host": "GitHub.com", "access": "read"},
        {"host": "mirror.example.org", "access": "read-write"},
        {"host": "pastebin.com", "access": "read-write", "readWriteConfirmed": True},
        {"host": "pypi.org", "access": "read-write", "readWriteConfirmed": True, "opaque": True},
        {"host": "docs.example.com", "access": "read", "paths": ["no-slash"]},
        # Git hosts are never websites.
        {"host": "github.com", "access": "read"},
        {"host": "*.githubusercontent.com", "access": "read"},
        {"host": "*.github.com", "access": "read"},
        {"host": "gitlab.com", "access": "read"},
        {"host": "docs.example.com", "access": "write"},
    ],
)
def test_sec_eg_07_a_site_node_would_refuse_never_becomes_a_destination(site: dict[str, JsonValue]) -> None:
    assert site_destinations([site]) == ()


def test_c6_11_sites_become_https_destinations_the_broker_accepts() -> None:
    destinations = site_destinations(
        [
            {**GITHUB, "paths": ["/api/"]},
            MIRROR,
            {"host": "pinned.example.org", "access": "read-write", "readWriteConfirmed": True, "opaque": True},
            {"host": "git.corp.internal", "access": "read"},
        ],
        private_names=frozenset({"git.corp.internal"}),
    )
    assert [(d.host, d.ports, d.site) for d in destinations] == [
        ("docs.example.com", (443,), SiteAccess(SiteMode.READ, ("/api/",))),
        ("mirror.example.org", (443,), SiteAccess(SiteMode.READ_WRITE)),
        ("pinned.example.org", (443,), SiteAccess(SiteMode.READ_WRITE, opaque=True)),
        ("git.corp.internal", (443,), SiteAccess(SiteMode.READ)),
    ]
    assert policy_errors(EgressPolicy(destinations=destinations)) == []


def test_ad_03_egress_private_allow_names_open_private_sites() -> None:
    env = {"EGRESS_PRIVATE_ALLOW": "git.corp.internal:443, 10.0.0.0/8:443, [fd00::1]:443"}
    assert private_allow_names(env) == frozenset({"git.corp.internal"})


READ_D = EgressDestination("docs.example.com", (443,), site=SiteAccess(SiteMode.READ))
WRITE_D = EgressDestination("mirror.example.org", (443,), site=SiteAccess(SiteMode.READ_WRITE))


def test_sec_eg_07_after_a_private_read_every_site_is_held() -> None:
    # SITEUP-01: held, not dropped; SEC-A-01: a read site too, since a URL carries data.
    held = EgressDestination("mirror.example.org", (443,), site=SiteAccess(SiteMode.READ_WRITE, held=True))
    read_held = EgressDestination("docs.example.com", (443,), site=SiteAccess(SiteMode.READ, held=True))
    assert narrowed_for_flow((READ_D, WRITE_D), FlowFacts()) == (READ_D, WRITE_D)
    assert narrowed_for_flow((READ_D, WRITE_D), FlowFacts(private_read=True)) == (read_held, held)
    assert narrowed_for_flow((READ_D, WRITE_D), FlowFacts(egress_forbidden=True)) == ()


@dataclass
class FakePolicy:
    sites: list[Mapping[str, JsonValue]]

    async def allowed_sites(self) -> list[Mapping[str, JsonValue]]:
        return self.sites


@dataclass
class FakeLedger:
    records: frozenset[str] | None = frozenset()

    def apply(self, events: Sequence[JournalEvent]) -> None:
        return None

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self.records


async def test_c6_11_each_turn_reads_the_live_fold_and_the_flow_rule_as_they_stand() -> None:
    policy = FakePolicy([GITHUB, MIRROR])
    ledger = FakeLedger()
    sites = RunSites(policy, UNCONFIGURED, ledger, "r1", (FlowRule("ask"),))
    assert [d.host for d in await sites()] == ["docs.example.com", "mirror.example.org"]
    ledger.records = frozenset({"rec-1"})
    held = [d.host for d in await sites() if d.site is not None and (d.site.mode is SiteMode.READ or d.site.held)]
    assert held == ["docs.example.com", "mirror.example.org"], "a private read holds the read-write site to reads"
    policy.sites = []
    assert await sites() == (), "a site the admin removed is gone at the next turn"


async def test_c6_11_without_a_flow_rule_a_private_read_holds_nothing() -> None:
    sites = RunSites(FakePolicy([GITHUB, MIRROR]), UNCONFIGURED, FakeLedger(frozenset({"r"})), "r1", ())
    assert len(await sites()) == 2


@dataclass(frozen=True)
class _Configured:
    configured: bool = True


class _LevelEgress(NoClassification):
    """An edition's classification whose conversation level is "restricted" with no ledger, else "internal"."""

    @override
    def level_source(
        self, classification: Classification, ledger: object, run_id: str, *, record_level: object = None
    ) -> Callable[[], str]:
        level = "restricted" if cast("FakeLedger", ledger).records is None else "internal"
        return lambda: level

    @override
    def egress(self, classification: Classification, level: str) -> EgressRule:
        return {"restricted": "deny", "internal": "ask"}[level]  # type: ignore[return-value]


async def test_owd_15_the_editions_egress_rule_narrows_the_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.edition_services.harness_edition", lambda: HarnessEdition(ports={ClassificationPort: _LevelEgress()})
    )
    unknown = RunSites(FakePolicy([GITHUB, MIRROR]), _Configured(), FakeLedger(None), "r1", ())
    assert await unknown() == ()
    internal = RunSites(FakePolicy([GITHUB, MIRROR]), _Configured(), FakeLedger(frozenset({"rec"})), "r1", ())
    writable = [d.host for d in await internal() if d.site is not None and d.site.mode is SiteMode.READ_WRITE and not d.site.held]
    assert writable == [], "a level that asks writes nowhere until the person allows it"


async def test_owd_15_by_default_no_level_narrows_the_sites(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.edition_services.harness_edition", lambda: NO_HARNESS_EDITION)
    sites = RunSites(FakePolicy([GITHUB, MIRROR]), _Configured(), FakeLedger(None), "r1", ())
    assert len(await sites()) == 2


def test_c6_11_the_engine_is_told_its_sites_and_its_shell_reaches_them_through_the_proxy() -> None:
    text = sites_instructions((READ_D, WRITE_D))
    assert "https://docs.example.com (read-only: GET, HEAD and OPTIONS)" in text
    assert "https://mirror.example.org (read and write)" in text
    assert sites_instructions(()) == ""
    env = site_env()
    assert env["HTTPS_PROXY"] == env["https_proxy"] == PROXY_URL
    assert "HTTP_PROXY" not in env, "plain http (the gateways) keeps its own route"
    for key in ("GIT_SSL_CAINFO", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS", "PIP_CERT", "npm_config_cafile"):
        assert env[key] == TRUST_BUNDLE

"""Contract suites for the gateway's ``CitationSink`` and ``GatewayKeySource`` ports.

``CitationSinkContract``: provide a ``citation_harness`` fixture
(``CitationSinkHarness``): the sink under test and ``label(url)``, which does
what a knowledge tool does to the run's mapper (gives ``url`` a ``refN``).
``GatewayKeySourceContract``: provide a ``key_harness`` fixture
(``KeySourceHarness``): the source under test and ``publish(keys)``, which is
what the issuer does when it provisions or rotates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.gateway.ports import PublishedKey, PublishedKeys

if TYPE_CHECKING:
    from harness_core.gateway.ports import CitationSink, GatewayKeySource


class CitationSinkHarness(Protocol):
    @property
    def sink(self) -> CitationSink: ...

    def label(self, url: str) -> str: ...


class CitationSinkContract:
    @pytest.fixture
    def citation_harness(self) -> CitationSinkHarness:
        raise NotImplementedError("provide a citation_harness fixture")

    def test_cs1_cite_resolves_the_refs_the_run_holds(self, citation_harness: CitationSinkHarness) -> None:
        first = citation_harness.label("https://kb.example/a")
        second = citation_harness.label("https://kb.example/b")
        cited = citation_harness.sink.cite(f"See [{second}] and [{first}], then [{second}] again.")
        assert [(c.ref, c.url) for c in cited] == [
            (second, "https://kb.example/b"),
            (first, "https://kb.example/a"),
        ]

    def test_cs2_an_invented_ref_is_dropped(self, citation_harness: CitationSinkHarness) -> None:
        known = citation_harness.label("https://kb.example/a")
        assert [c.ref for c in citation_harness.sink.cite(f"[{known}] and [ref9999]")] == [known]
        assert citation_harness.sink.cite("no references here") == ()

    def test_cs3_labels_are_stable(self, citation_harness: CitationSinkHarness) -> None:
        assert citation_harness.label("https://kb.example/a") == citation_harness.label("https://kb.example/a")

    def test_cs4_record_reports_each_addition_once(self, citation_harness: CitationSinkHarness) -> None:
        citation_harness.label("https://kb.example/a")
        first = citation_harness.sink.record("inv-1")
        assert citation_harness.sink.record("inv-2") == ()
        assert isinstance(first, tuple)


class KeySourceHarness(Protocol):
    @property
    def source(self) -> GatewayKeySource: ...

    async def publish(self, keys: PublishedKeys) -> None: ...


KEYS = PublishedKeys(
    issuer="issuer",
    audience="audience",
    keys=(PublishedKey("hgk_one", "-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----\n"),),
)
ROTATED = PublishedKeys(
    issuer="issuer",
    audience="audience",
    keys=(
        PublishedKey("hgk_one", KEYS.keys[0].public_key_pem, retired_at_ms=1_790_000_000_000),
        PublishedKey("hgk_two", "-----BEGIN PUBLIC KEY-----\nBBBB\n-----END PUBLIC KEY-----\n"),
    ),
)


class GatewayKeySourceContract:
    @pytest.fixture
    def key_harness(self) -> KeySourceHarness:
        raise NotImplementedError("provide a key_harness fixture")

    async def test_ks1_unprovisioned_is_none(self, key_harness: KeySourceHarness) -> None:
        assert await key_harness.source.keys() is None
        assert await key_harness.source.keys(refresh=True) is None

    async def test_ks2_published_keys_are_read_back(self, key_harness: KeySourceHarness) -> None:
        await key_harness.publish(KEYS)
        assert await key_harness.source.keys(refresh=True) == KEYS

    async def test_ks3_a_refresh_sees_a_rotation(self, key_harness: KeySourceHarness) -> None:
        await key_harness.publish(KEYS)
        assert await key_harness.source.keys() == KEYS
        await key_harness.publish(ROTATED)
        assert await key_harness.source.keys(refresh=True) == ROTATED


__all__ = [
    "KEYS",
    "ROTATED",
    "CitationSinkContract",
    "CitationSinkHarness",
    "GatewayKeySourceContract",
    "KeySourceHarness",
]

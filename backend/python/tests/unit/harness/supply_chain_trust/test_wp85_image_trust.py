"""C6.31 / SEC-SC-01 (ADR-SEC-01 D3, D4): the sandbox broker starts an engine image the check applies to only on an
attestation over the image's own digest, by a key trusted for its kind, before anything is created.

IT-1..IT-4, IT-10. Keys are generated per test: signing the shipped images waits on the new release key (OB-1).
"""

from __future__ import annotations

import logging
from typing import Any, override

import pytest

from app.harness.supply_chain.images import image_trust_from_env
from harness_core.sandbox.errors import BrokerUnavailable, ImageNotAllowed
from harness_core.supply_chain.images import ImageTrust, StaticAttestations
from harness_core.supply_chain.trust import ArtefactKind, TrustRoots
from tests.unit.harness.sandbox_broker.test_docker_broker import (
    LEASE,
    _broker_on,  # pyright: ignore[reportPrivateUsage]
    _FakeApi,  # pyright: ignore[reportPrivateUsage]
    _FakeProvider,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness_core.supply_chain.keys import TestKey, new_key

ENGINE = "pipeshub/engine-opencode:1.2.3-ph5"
CUSTOM = "corp.example/tools:7"
ENGINE_ID = "sha256:" + "e" * 64
CUSTOM_ID = "sha256:" + "c" * 64
BASE_LAYERS = ("sha256:" + "1" * 64, "sha256:" + "2" * 64)


class _ImageProvider(_FakeProvider):
    def __init__(
        self, digests: dict[str, tuple[str, ...]], layers: dict[str, tuple[str, ...]]
    ) -> None:
        super().__init__()
        self.digests = digests
        self.layers = layers
        self.api = _FakeApi()
        self.client = type("Client", (), {"api": self.api})()

    async def image_digests(self, image: str) -> tuple[str, ...]:
        return self.digests.get(image, ())

    async def image_layers(self, image: str) -> tuple[str, ...]:
        return self.layers.get(image, ())

    @override
    async def image_labels(self, _: str) -> dict[str, str]:
        return {}


def _provider() -> _ImageProvider:
    return _ImageProvider(
        {ENGINE: (ENGINE_ID,), CUSTOM: (CUSTOM_ID, "sha256:" + "d" * 64)},
        {ENGINE: BASE_LAYERS, CUSTOM: (*BASE_LAYERS, "sha256:" + "3" * 64)},
    )


def _trust(
    *attestations: object,
    release: TestKey | None = None,
    required: bool = True,
    **extra: Any,  # noqa: ANN401
) -> ImageTrust:
    keys: dict[ArtefactKind, dict[str, bytes]] = {}
    if release is not None:
        keys[ArtefactKind.ENGINE_IMAGE] = {release.key_id: release.public}
    return ImageTrust(
        roots=TrustRoots(keys),
        attestations=StaticAttestations(tuple(attestations)),  # type: ignore[arg-type]
        release_images=frozenset({ENGINE}),
        engine_signatures_required=required,
        base_images=frozenset({ENGINE}),
        **extra,
    )


async def _acquire(provider: _ImageProvider, trust: ImageTrust, image: str) -> None:
    broker = _broker_on(provider, image_trust=trust)
    await broker.acquire(
        _spec(image_digest=image, labels={"runId": "r-sc"}),
        lease=LEASE,
        idempotency_key="k",
    )


class _Refused:
    """The run's one code (``image_untrusted``) and the cause the broker logged for the operator."""

    def __init__(self, caplog: pytest.LogCaptureFixture) -> None:
        self.caplog = caplog
        caplog.set_level(logging.WARNING, logger="app.harness.sandbox.broker")

    async def __call__(
        self, provider: _ImageProvider, trust: ImageTrust, image: str
    ) -> str:
        self.caplog.clear()
        with pytest.raises(ImageNotAllowed) as refused:
            await _acquire(provider, trust, image)
        assert refused.value.reason == "image_untrusted"
        assert provider.api.host_configs == []
        [line] = [
            r.getMessage()
            for r in self.caplog.records
            if r.getMessage().startswith("sandbox.image_untrusted")
        ]
        return line.split("reasonCode=", 1)[1].split()[0]


@pytest.fixture
def refused(caplog: pytest.LogCaptureFixture) -> _Refused:
    return _Refused(caplog)


async def test_it1_sec_sc_01_a_signed_engine_image_reaches_create() -> None:
    release = new_key("release-test")
    provider = _provider()
    with pytest.raises(BrokerUnavailable):  # the fake API stops at create
        await _acquire(
            provider,
            _trust(
                release.attest(ArtefactKind.ENGINE_IMAGE, ENGINE, ENGINE_ID),
                release=release,
            ),
            ENGINE,
        )
    assert len(provider.api.host_configs) == 1


async def test_it2_an_unsigned_engine_image_is_refused_before_anything_is_created(
    refused: _Refused, caplog: pytest.LogCaptureFixture
) -> None:
    assert (
        await refused(_provider(), _trust(release=new_key("release-test")), ENGINE)
        == "image_unsigned"
    )
    assert any(
        "reason=image_untrusted" in r.getMessage() and "runId=r-sc" in r.getMessage()
        for r in caplog.records
    )


async def test_it3_a_signature_for_other_bytes_or_by_another_key_is_refused(
    refused: _Refused,
) -> None:
    release, stranger = new_key("release-test"), new_key("stranger")
    for attestation, reason in (
        (
            stranger.attest(ArtefactKind.ENGINE_IMAGE, ENGINE, ENGINE_ID),
            "artefact_unknown_key",
        ),
        (
            release.attest(ArtefactKind.SKILL, ENGINE, ENGINE_ID),
            "image_unsigned",
        ),
        (
            release.attest(ArtefactKind.ENGINE_IMAGE, ENGINE, "sha256:" + "0" * 64),
            "image_unsigned",
        ),
    ):
        assert (
            await refused(_provider(), _trust(attestation, release=release), ENGINE)
            == reason
        )


async def test_it4_engine_signatures_wait_on_the_release_key_and_are_off_by_default() -> (
    None
):
    provider = _provider()
    with pytest.raises(BrokerUnavailable):
        await _acquire(provider, _trust(required=False), ENGINE)
    assert image_trust_from_env(frozenset({ENGINE}), frozenset({ENGINE}), {}) is None


async def test_it10_a_re_signed_image_logs_what_moved(
    caplog: pytest.LogCaptureFixture,
) -> None:
    release = new_key("release-test")
    first = release.attest(ArtefactKind.ENGINE_IMAGE, ENGINE, ENGINE_ID, {"build": "1"})
    newer_id = "sha256:" + "a" * 64
    second = release.attest(ArtefactKind.ENGINE_IMAGE, ENGINE, newer_id, {"build": "2"})
    provider = _provider()
    broker = _broker_on(provider, image_trust=_trust(first, second, release=release))
    caplog.set_level(logging.INFO, logger="app.harness.sandbox.broker")
    for key in ("k1", "k2"):
        with pytest.raises(BrokerUnavailable):
            await broker.acquire(
                _spec(image_digest=ENGINE), lease=LEASE, idempotency_key=key
            )
        provider.digests[ENGINE] = (newer_id,)
    changed = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("sandbox.image_changed")
    ]
    assert len(changed) == 1 and "digest" in changed[0] and "build" in changed[0]


async def test_it11_an_image_the_release_does_not_list_is_not_checked_here() -> None:
    provider = _provider()
    trust = _trust(release=new_key("release-test"))
    assert trust.kind_of(CUSTOM) is None and trust.added_images == frozenset()
    with pytest.raises(
        BrokerUnavailable
    ):  # past the check, the fake API stops at create
        await _acquire(provider, trust, CUSTOM)

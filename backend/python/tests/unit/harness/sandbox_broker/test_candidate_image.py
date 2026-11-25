"""EH-6..EH-8 (EH1, SBX-31, SBX-32): the broker never runs a candidate build, whatever tag it carries."""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import override

import pytest

from harness_core.sandbox.errors import BrokerUnavailable, ImageNotAllowed
from harness_core.sandbox.pool import shape_of
from harness_engines.release import CANDIDATE_LABEL
from tests.unit.harness.engines.lane_releases import (
    CANDIDATE_ENV,
    lane_admits_candidates,
)

from .test_docker_broker import (
    LEASE,
    _broker_on,  # pyright: ignore[reportPrivateUsage]
    _FakeApi,  # pyright: ignore[reportPrivateUsage]
    _FakeProvider,  # pyright: ignore[reportPrivateUsage]
    _spec,  # pyright: ignore[reportPrivateUsage]
)

APP = Path(__file__).resolve().parents[4] / "app"


class _LabelledProvider(_FakeProvider):
    def __init__(self, labels: dict[str, str]) -> None:
        super().__init__()
        self.labels = labels
        self.api = _FakeApi()
        self.client = type("Client", (), {"api": self.api})()

    @override
    async def image_labels(self, _: str) -> dict[str, str]:
        return dict(self.labels)


def _created(provider: _LabelledProvider) -> int:
    return len(provider.api.host_configs)


async def test_eh6_a_candidate_image_is_refused_at_acquire_and_nothing_is_created(caplog: pytest.LogCaptureFixture) -> None:
    provider = _LabelledProvider({CANDIDATE_LABEL: "true", "other": "x"})
    caplog.set_level(logging.WARNING, logger="app.harness.sandbox.broker")
    with pytest.raises(ImageNotAllowed) as refused:
        await _broker_on(provider).acquire(_spec(labels={"runId": "r-eh1"}), lease=LEASE, idempotency_key="k")
    assert refused.value.reason == "candidate_image"
    assert _created(provider) == 0
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("sandbox.refused")]
    assert "reason=candidate_image" in line and "runId=r-eh1" in line


async def test_eh6_a_candidate_image_is_never_made_a_warm_sandbox() -> None:
    provider = _LabelledProvider({CANDIDATE_LABEL: "true"})
    shape = shape_of(_spec())
    assert shape is not None
    with pytest.raises(ImageNotAllowed) as refused:
        await _broker_on(provider).create_warm(shape, {})
    assert refused.value.reason == "candidate_image"
    assert _created(provider) == 0


@pytest.mark.parametrize(
    ("labels", "admit"),
    [({}, False), ({"io.pipeshub.engine.binary-digest": "sha256:" + "a" * 64}, False), ({CANDIDATE_LABEL: "true"}, True)],
    ids=["unlabelled", "release-labels", "candidate-under-the-lane-override"],
)
async def test_eh7_other_images_and_the_lane_override_reach_create(labels: dict[str, str], admit: bool) -> None:
    provider = _LabelledProvider(labels)
    with pytest.raises(BrokerUnavailable):  # the fake API stops at create
        await _broker_on(provider, admit_candidate_images=admit).acquire(_spec(), lease=LEASE, idempotency_key="k")
    assert _created(provider) == 1


def _passes_override(path: Path) -> bool:
    text = path.read_text(encoding="utf-8-sig")
    if "admit_candidate_images" not in text:
        return False
    tree = ast.parse(text, filename=str(path))
    return any(
        isinstance(node, ast.keyword) and node.arg == "admit_candidate_images" for node in ast.walk(tree)
    )


def test_eh8_production_never_admits_candidates() -> None:
    callers = sorted(str(p.relative_to(APP)) for p in APP.rglob("*.py") if _passes_override(p))
    assert callers == ["harness/sandbox/docker_broker.py"]



def test_eh7_lanes_admit_candidates_only_while_one_is_under_test() -> None:
    assert lane_admits_candidates({}) is False
    assert lane_admits_candidates({CANDIDATE_ENV: " "}) is False
    assert lane_admits_candidates({CANDIDATE_ENV: "claude_code=9.9.9@sha256:" + "e" * 64}) is True

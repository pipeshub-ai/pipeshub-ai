"""C2.07 / C2.20 (SL-14): an engine run runs only on the image it was admitted under.

Node pins the image digest (``pins.sandboxImage``) and the ``/pipeshub`` contract (``x.pipeshub.release``) at
claim and never re-resolves them; the engine controller holds every sandbox of the run to that digest
(``SandboxSpec.image_pin``) and refuses a turn whose image changed since (``engine_image_changed``): a rebuilt
image, or one that now declares another contract. The broker checks the image a sandbox was made from, whatever
the tag names (``image_digest_mismatch``).
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.errors import EngineImageChanged
from harness_core.engines.pins import ImagePins, image_pins
from harness_core.engines.tool_folder import ImageContract
from harness_core.sandbox.errors import ImageNotAllowed
from harness_core.sandbox.types import (
    ENGINE_IMAGES,
    BrokerHealth,
    RuntimeMode,
    SandboxSpec,
    engine_image_digests,
    engine_images_detail,
)
from harness_core.testing.engines.fake_engine import FAKE_VERSION
from harness_engines.pipeshub import FOLDER, pipeshub_tool_folder
from tests.unit.harness_core.engines.test_controller import IMAGE, World, task

PINNED = "sha256:" + "1" * 64
REBUILT = "sha256:" + "2" * 64


def _spec(pins: dict[str, object] | None = None, release: dict[str, object] | None = None) -> RunSpec:
    wire: dict[str, object] = {
        "schemaVersion": 3,
        "harnessMajor": 2,
        "foldVersion": 8,
        "runKind": "child",
        "principalRef": "p_user-1",
        "model": {"policy": [{"provider": "openai", "model": "gpt-x"}], "promptVersion": "p-1"},
        "grants": {"tools": [], "sandboxProfile": "none", "egress": "none"},
        "budgets": {
            "maxTurns": 5,
            "maxTokens": 1000,
            "maxCostUsd": 1,
            "maxChildren": 0,
            "activeTimeS": 600,
            "calendarDeadline": "2026-10-26T12:00:00.000Z",
        },
        "capabilitiesDigest": "sha256:" + "c" * 64,
    }
    if pins is not None:
        wire["pins"] = pins
    if release is not None:
        wire["extensions"] = {"x.pipeshub": {"release": release}}
    return RunSpec.model_validate(wire)


def _pinned_world(**world: object) -> World:
    built = World(**world)  # type: ignore[arg-type]
    built.broker.image_digests[IMAGE] = PINNED
    built.broker.engine_images[FAKE_VERSION.ref] = PINNED
    return built


class TestTheSpecPin:
    def test_sl14_a_spec_names_its_image_digest_and_contract(self) -> None:
        spec = _spec({"sandboxImage": PINNED}, {"pipeshubContract": 1, "catalogDigest": "sha256:" + "d" * 64})
        assert image_pins(spec) == ImagePins(image=PINNED, contract=1)

    def test_a_spec_pinned_before_c207_pins_nothing(self) -> None:
        assert image_pins(_spec()) == ImagePins()


class TestTheBrokerReport:
    def test_health_names_each_engine_s_digest_and_drops_malformed_entries(self) -> None:
        raw = f"{engine_images_detail({'a@1': PINNED})},b@2=sha256:short,c@3"
        health = BrokerHealth(ok=True, modes=frozenset(), detail={ENGINE_IMAGES: raw})
        assert engine_image_digests(health) == {"a@1": PINNED}
        assert engine_image_digests(BrokerHealth(ok=True, modes=frozenset())) is None

    def test_a_pin_is_a_sha256_digest(self) -> None:
        spec = SandboxSpec(image_digest=IMAGE, mode=RuntimeMode.DOCKER_HARDENED, image_pin="latest")
        assert any("image_pin" in error for error in spec.validation_errors())
        assert SandboxSpec(image_digest=IMAGE, mode=RuntimeMode.DOCKER_HARDENED, image_pin=PINNED).validation_errors() == []


class TestTheController:
    async def test_sl14_c220_a_pinned_run_s_sandbox_is_held_to_its_digest(self) -> None:
        world = _pinned_world()
        outcome = await world.controller.run_turn(world.run(image_pin=PINNED), task(0, "say ok"))
        assert outcome.reason == "completed"
        [(spec, _)] = world.broker.acquired
        assert (spec.image_digest, spec.image_pin) == (IMAGE, PINNED)

    async def test_c220_an_unpinned_run_is_held_to_the_digest_the_broker_reports(self) -> None:
        world = _pinned_world()
        _ = await world.controller.run_turn(world.run(), task(0, "say ok"))
        [(spec, _)] = world.broker.acquired
        assert spec.image_pin == PINNED

    async def test_c220_the_release_s_digest_wins_over_the_broker_s_report(self) -> None:
        world = _pinned_world(config_overrides={"image_pins": {FAKE_VERSION: PINNED}})
        world.broker.engine_images.clear()
        _ = await world.controller.run_turn(world.run(), task(0, "say ok"))
        [(spec, _)] = world.broker.acquired
        assert spec.image_pin == PINNED

    async def test_a_broker_that_reports_no_digest_leaves_an_unpinned_run_unpinned(self) -> None:
        world = World()
        _ = await world.controller.run_turn(world.run(), task(0, "say ok"))
        [(spec, _)] = world.broker.acquired
        assert spec.image_pin is None

    async def test_sl14_contract_pin_a_rebuilt_image_refuses_the_run_before_anything_is_created(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The contract-pin test (image): the run was admitted under one digest, the broker now has another under
        the same tag (resumed after a rebuild). Nothing is created; the person reads that the engine was updated."""
        world = _pinned_world()
        world.broker.image_digests[IMAGE] = REBUILT
        world.broker.engine_images[FAKE_VERSION.ref] = REBUILT
        with caplog.at_level(logging.WARNING), pytest.raises(EngineImageChanged) as refused:
            _ = await world.controller.run_turn(world.run(image_pin=PINNED), task(0, "say ok"))
        assert refused.value.reason_code == "engine_image_changed"
        assert world.broker.acquired == [] and world.host.mounted == 0
        assert "reasonCode=engine_image_changed" in caplog.text

    async def test_c220_the_broker_refuses_a_sandbox_of_another_image_whatever_the_worker_saw(self) -> None:
        """The broker's own check: a worker whose broker said nothing about the digest still never runs another image."""
        world = World()
        world.broker.image_digests[IMAGE] = REBUILT
        with pytest.raises(ImageNotAllowed) as refused:
            _ = await world.controller.run_turn(world.run(image_pin=PINNED), task(0, "say ok"))
        assert refused.value.reason == "image_digest_mismatch"

    async def test_sl14_contract_pin_an_image_declaring_another_contract_refuses_the_run(self) -> None:
        """The contract-pin test (contract): this worker writes contracts 1 and 2, the run was pinned to 1 and the
        image now declares 2. The engine never starts."""
        both = dataclasses.replace(
            pipeshub_tool_folder(), contract=ImageContract(f"{FOLDER}/VERSION", frozenset({"1", "2"}))
        )
        world = World(config_overrides={"tool_folder": both}, image_files={IMAGE: {f"{FOLDER}/VERSION": b"2\n"}})
        with pytest.raises(EngineImageChanged) as refused:
            _ = await world.controller.run_turn(world.run(contract_pin=1), task(0, "say ok"))
        assert "pinned to contract 1, the image declares 2" in str(refused.value)
        assert world.adapter.files_at_open == []  # pyright: ignore[reportAttributeAccessIssue]
        assert len(world.broker.released) == 1

    async def test_sl14_the_pinned_contract_the_image_declares_runs(self) -> None:
        world = World(
            config_overrides={"tool_folder": pipeshub_tool_folder()}, image_files={IMAGE: {f"{FOLDER}/VERSION": b"1\n"}}
        )
        outcome = await world.controller.run_turn(world.run(contract_pin=1), task(0, "say ok"))
        assert outcome.reason == "completed"

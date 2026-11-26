"""Contract suite for ``EngineInputSource`` (E1b, EV2-26): conversation artifacts as an engine run's inputs.

Subclass :class:`EngineInputSourceContract` and provide ``input_world``: an :class:`InputWorld` whose
``source`` resolves for the run ``binding()`` as that run's actor, with a per-input cap of ``MAX_INPUT_BYTES``, and whose helpers make artifacts the
actor can read, cannot read, or that live in another conversation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.artifacts.types import ArtifactVersionRef
from harness_core.engines.errors import (
    EngineInputConflict,
    EngineInputTooLarge,
    EngineInputUnavailable,
)
from harness_core.interactions.ports import RunBinding
from harness_core.journal.types import InputArtifactRef

if TYPE_CHECKING:
    from harness_core.engines.durable.ports import EngineInputSource
    from harness_core.workspace.types import ObjectRef

TENANT = "org-in"
SESSION = "conv-in"
OTHER_SESSION = "conv-elsewhere"
# The per-input cap a world's source is built with (C3.08).
MAX_INPUT_BYTES = 64


def binding(run_id: str = "run-in") -> RunBinding:
    return RunBinding(TENANT, SESSION, run_id)


class InputWorld(Protocol):
    @property
    def source(self) -> EngineInputSource: ...

    async def add(self, name: str, content: bytes, *, session: str = SESSION, readable: bool = True) -> str:
        """A new artifact (version 1) of ``session``; ``readable``: whether the run's actor may read it."""
        ...

    async def add_version(self, artifact_id: str, content: bytes) -> int: ...

    async def discard(self, artifact_id: str) -> None: ...

    async def stored(self, ref: ObjectRef) -> bytes:
        """The bytes of an input's object, read from the run's session object scope."""
        ...


class EngineInputSourceContract:
    @pytest.fixture
    def input_world(self) -> InputWorld:
        raise NotImplementedError("provide an input_world fixture")

    async def test_in1_the_latest_version_by_default_the_named_one_otherwise(self, input_world: InputWorld) -> None:
        deck = await input_world.add("deck.pptx", b"v1")
        assert await input_world.add_version(deck, b"v2") == 2
        sheet = await input_world.add("sheet.xlsx", b"s1")
        latest, pinned = await input_world.source.resolve(
            binding(), [InputArtifactRef(deck), InputArtifactRef(sheet, 1)]
        )
        assert (latest.name, latest.source) == ("deck.pptx", ArtifactVersionRef(deck, 2))
        assert await input_world.stored(latest.content) == b"v2"
        assert (pinned.name, pinned.source) == ("sheet.xlsx", ArtifactVersionRef(sheet, 1))
        assert await input_world.stored(pinned.content) == b"s1"
        (older,) = await input_world.source.resolve(binding(), [InputArtifactRef(deck, 1)])
        assert await input_world.stored(older.content) == b"v1"

    async def test_in1_nothing_is_resolved_for_no_refs(self, input_world: InputWorld) -> None:
        assert await input_world.source.resolve(binding(), []) == ()

    @pytest.mark.parametrize("case", ["unknown", "discarded", "other_session", "unreadable", "no_such_version"])
    async def test_in1_an_artifact_the_run_cannot_take_is_refused_as_unavailable(
        self, input_world: InputWorld, case: str
    ) -> None:
        if case == "unknown":
            ref = InputArtifactRef("no-such-artifact")
        elif case == "discarded":
            ref = InputArtifactRef(await input_world.add("gone.md", b"x"))
            await input_world.discard(ref.artifact_id)
        elif case == "other_session":
            ref = InputArtifactRef(await input_world.add("theirs.md", b"x", session=OTHER_SESSION))
        elif case == "unreadable":
            ref = InputArtifactRef(await input_world.add("secret.md", b"x", readable=False))
        else:
            ref = InputArtifactRef(await input_world.add("short.md", b"x"), 2)
        fine = InputArtifactRef(await input_world.add("fine.md", b"ok"))
        with pytest.raises(EngineInputUnavailable) as refused:
            _ = await input_world.source.resolve(binding(), [fine, ref])
        assert refused.value.reason_code == "engine_input_unavailable"

    async def test_in1_two_inputs_with_one_file_name_conflict(self, input_world: InputWorld) -> None:
        first = await input_world.add("q3/report.md", b"a")
        second = await input_world.add("q4/report.md", b"b")
        with pytest.raises(EngineInputConflict) as refused:
            _ = await input_world.source.resolve(binding(), [InputArtifactRef(first), InputArtifactRef(second)])
        assert refused.value.reason_code == "engine_input_conflict"
        (one,) = await input_world.source.resolve(binding(), [InputArtifactRef(first), InputArtifactRef(first)])
        assert one.source.artifact_id == first


    async def test_sec_pi_07_a_hostile_file_name_is_staged_under_a_safe_name(self, input_world: InputWorld) -> None:
        """SEC-PI-07 (WS-10): the engine's shell sees a name it cannot read as syntax."""
        hostile = await input_world.add("$(rm -rf ~).pdf", b"x")
        (staged,) = await input_world.source.resolve(binding(), [InputArtifactRef(hostile)])
        assert staged.name == "rm_-rf_.pdf"
        (again,) = await input_world.source.resolve(binding(), [InputArtifactRef(hostile)])
        assert again.name == staged.name, "the same name always stages as the same file"

    async def test_c3_08_an_input_over_the_cap_is_refused_and_one_at_the_cap_is_taken(
        self, input_world: InputWorld
    ) -> None:
        """C3.08 (WS-10): one input's bytes are capped; the refusal names the file and the limit."""
        exact = await input_world.add("exact.md", b"a" * MAX_INPUT_BYTES)
        (taken,) = await input_world.source.resolve(binding(), [InputArtifactRef(exact)])
        assert await input_world.stored(taken.content) == b"a" * MAX_INPUT_BYTES
        large = await input_world.add("large.md", b"a" * (MAX_INPUT_BYTES + 1))
        with pytest.raises(EngineInputTooLarge) as refused:
            _ = await input_world.source.resolve(binding(), [InputArtifactRef(large)])
        assert refused.value.reason_code == "engine_input_too_large"
        assert "large.md is too large" in str(refused.value) and "the limit is 64 bytes" in str(refused.value)

    async def test_c3_08_an_older_version_over_the_cap_is_refused_once_read(self, input_world: InputWorld) -> None:
        """The cap holds for a pinned older version, whose size the current metadata does not tell."""
        doc = await input_world.add("doc.md", b"a" * (MAX_INPUT_BYTES + 1))
        _ = await input_world.add_version(doc, b"small")
        (current,) = await input_world.source.resolve(binding(), [InputArtifactRef(doc)])
        assert await input_world.stored(current.content) == b"small"
        with pytest.raises(EngineInputTooLarge):
            _ = await input_world.source.resolve(binding(), [InputArtifactRef(doc, 1)])


__all__ = ["MAX_INPUT_BYTES", "OTHER_SESSION", "SESSION", "TENANT", "EngineInputSourceContract", "InputWorld", "binding"]

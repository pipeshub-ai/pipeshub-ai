"""R50-JFIX item 2 (MR-18): a ``sandbox:`` link survived in an engine's own message. ``link_workspace_files`` ran only
where the job's answer was finalized (``presenter.finished``) and where ``code_task`` handed the result to the loop, so
the engine's journaled ``message.completed`` (what the job trail and the parent's relayed answer read) kept the link
the chat cannot open (13 artifacts journeys on opencode, codex, claude_code and ACP).

The kernel now links the final text once (``LinksFiles``) before anything journals it. These cases drive the engine
kernel on the in-memory world with PipesHub's presenter and publication log, as the worker wires them.
"""

from __future__ import annotations

import base64
from typing import Any, cast

from app.harness.contracts.generated.pipeshub.event_catalog import (
    EVENT_MODELS,
    EVENT_WRITERS,
)
from app.harness.engines.presenter import ANSWER
from harness_core.interactions.ports import RunBinding
from harness_core.testing.engines.durable import ORG, SESSION, EngineWorld
from tests.unit.harness.engines.test_durable_presenter import (
    _PresentingAssembler,  # pyright: ignore[reportPrivateUsage]
)

RUN = "run-mr18"


def _world() -> EngineWorld:
    return EngineWorld(extension_writers=cast("Any", EVENT_WRITERS), extension_models=cast("Any", EVENT_MODELS))


async def _run(world: EngineWorld, script: str) -> None:
    world.admit(RUN, script)
    kernel = world.kernel(assembler=_PresentingAssembler(world))
    assert (await world.attempt(RUN, kernel=kernel)).status == "completed"


async def _message(world: EngineWorld) -> str:
    [final] = [e for e in world.events(RUN) if e.type == "message.completed" and e.data.get("isFinal")]
    ref = cast("dict[str, Any]", final.data["textRef"])
    raw = str(ref["ref"])
    if raw.startswith("inline:"):
        return base64.b64decode(raw[len("inline:"):]).decode("utf-8")
    from harness_core.contracts.generated.common import ContentRef

    return (await world.content.get(ContentRef.model_validate(ref))).decode("utf-8")


def _answer(world: EngineWorld) -> str:
    [answer] = [e for e in world.events(RUN) if e.type == ANSWER]
    return str(answer.data["answer"])


async def test_mr18_1_the_engines_message_links_the_file_it_saved_never_its_sandbox_path() -> None:
    world = _world()
    await _run(world, "write out/fib.py print(1)\nsay Done. [Download fib.py](sandbox:/mnt/data/out/fib.py)")
    stored = await world.artifacts.find_by_name(RunBinding(ORG, SESSION, RUN), "fib.py")
    assert stored is not None
    linked = f"Done. [Download fib.py](/record/{stored.artifact_id})"
    message = await _message(world)
    assert "sandbox:" not in message and linked in message
    # The answer reads the same words: linked once, by the kernel, not again by the presenter.
    assert _answer(world) == message


async def test_mr18_2_a_link_to_a_file_it_did_not_save_keeps_only_its_words() -> None:
    world = _world()
    await _run(world, "say See [the notes](file:///workspace/out/notes.txt) and [log](sandbox:/mnt/data/run.log).")
    message = await _message(world)
    assert message == "See the notes and log."
    assert _answer(world) == message

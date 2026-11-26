"""D-6 (core): a scoped block runs on explicit bindings, never the installed ones."""

from __future__ import annotations

import asyncio

from harness_core import bindings


def _named(tool: str) -> bindings.KernelBindings:
    return bindings.KernelBindings(streamed_answer_argument=lambda name: tool)


def test_scoped_bindings_win_over_installed_and_are_restored() -> None:
    installed = _named("legacy")
    previous = bindings.current()
    bindings.install(installed)
    try:
        durable = _named("durable")
        with bindings.scoped(durable):
            assert bindings.current() is durable
            # A legacy install inside the block changes nothing for the block.
            bindings.install(_named("late-legacy"))
            assert bindings.current() is durable
        assert bindings.current() is not durable
    finally:
        bindings.install(previous)


async def test_tasks_inherit_their_scope_and_concurrent_scopes_stay_apart() -> None:
    seen: dict[str, bindings.KernelBindings] = {}

    async def run(name: str, scoped: bindings.KernelBindings) -> None:
        with bindings.scoped(scoped):
            child = asyncio.create_task(asyncio.sleep(0, result=bindings.current()))
            await asyncio.sleep(0)
            seen[name] = await child
            assert bindings.current() is scoped

    a, b = _named("a"), _named("b")
    await asyncio.gather(run("a", a), run("b", b))
    assert seen == {"a": a, "b": b}


def test_neutral_bindings_bind_nothing() -> None:
    assert bindings.NEUTRAL == bindings.KernelBindings()

"""The scope generation moves by itself: once per switch from off to on, however many processes start, and never for a deployment that
never turned the fields on."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from app.services.permissions.generation import (
    ENABLED_KEY,
    current_generation,
    init_scope_generation,
)
from app.services.permissions.mode import ENV_SCOPE_FIELDS
from app.services.permissions.scope_fields import (
    ENV_SCOPE_GENERATION,
    UNAVAILABLE_GENERATION,
    ScopeFields,
    current_scope_generation,
    set_resolved_generation,
)
from tests.support.embedding_config import another_process, config_service

if TYPE_CHECKING:
    from collections.abc import Iterator

log = logging.getLogger("t")
ON, OFF = {ENV_SCOPE_FIELDS: "on"}, {}


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    set_resolved_generation(None)
    yield
    set_resolved_generation(None)


async def test_on_off_on_across_two_processes_bumps_once_per_switch() -> None:
    a = config_service()
    b = another_process(a)
    assert await init_scope_generation(a, log, ON) == 1
    assert await init_scope_generation(b, log, ON) == 1  # a second process that starts with it on bumps nothing
    assert await init_scope_generation(a, log, OFF) is None
    assert await a.get_config(ENABLED_KEY, use_cache=False) is False
    assert await current_generation(b) == 1  # off moves nothing
    assert await init_scope_generation(b, log, ON) == 2  # off to on: one bump
    assert await init_scope_generation(a, log, ON) == 2
    assert current_scope_generation() == 2


async def test_processes_starting_together_bump_once() -> None:
    a = config_service()
    procs = [another_process(a) for _ in range(6)]
    got = await asyncio.gather(*(init_scope_generation(p, log, ON) for p in procs))
    assert got == [1] * 6 and await current_generation(a) == 1
    await init_scope_generation(procs[0], log, OFF)
    got = await asyncio.gather(*(init_scope_generation(p, log, ON) for p in procs))
    assert got == [2] * 6 and await current_generation(a) == 2


async def test_a_deployment_that_never_enabled_the_fields_writes_nothing() -> None:
    a = config_service()
    assert await init_scope_generation(a, log, OFF) is None
    assert a.store.store == {}
    assert current_scope_generation() == 0


async def test_the_env_value_is_only_a_floor_that_forces_a_bump() -> None:
    a = config_service()
    assert await init_scope_generation(a, log, {**ON, ENV_SCOPE_GENERATION: "7"}) == 7
    assert await init_scope_generation(another_process(a), log, {**ON, ENV_SCOPE_GENERATION: "3"}) == 7  # lower: ignored


async def test_an_unreadable_state_makes_the_stamps_of_this_process_untrusted() -> None:
    a = config_service()

    async def down(*args: object, **kwargs: object) -> None:
        raise RuntimeError("config store down")

    a.get_config = down  # type: ignore[method-assign]
    assert await init_scope_generation(a, log, ON) == UNAVAILABLE_GENERATION
    assert not ScopeFields.container().trusted


async def test_a_stamp_of_the_generation_before_the_last_enable_is_not_trusted() -> None:
    a = config_service()
    await init_scope_generation(a, log, ON)
    old = ScopeFields.container().to_payload()
    assert ScopeFields.from_payload(old).trusted
    await init_scope_generation(a, log, OFF)
    await init_scope_generation(another_process(a), log, ON)
    assert not ScopeFields.from_payload(old).trusted and ScopeFields.container().trusted


async def test_a_process_that_starts_after_another_created_the_generation_but_before_it_recorded_enabled_creates_nothing_new() -> None:
    """The interleaving that used to make two generations: A has created generation 1 and not yet recorded enabled=True when B reads."""
    a = config_service()
    b = another_process(a)
    at_gate, release = asyncio.Event(), asyncio.Event()
    real_set = a.set_config

    async def gated(key: str, value: object, *args: object, **kwargs: object) -> bool:
        if key == ENABLED_KEY and value is True:
            at_gate.set()
            await release.wait()
        return await real_set(key, value, *args, **kwargs)

    a.set_config = gated  # type: ignore[method-assign]
    first = asyncio.create_task(init_scope_generation(a, log, ON))
    await at_gate.wait()
    assert await current_generation(b) == 1 and await b.get_config(ENABLED_KEY, use_cache=False) is not True
    second = await init_scope_generation(b, log, ON)
    release.set()
    assert (await first, second) == (1, 1) and await current_generation(a) == 1

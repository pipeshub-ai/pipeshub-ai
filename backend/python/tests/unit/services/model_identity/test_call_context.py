from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.usage_source import llm_usage_source
from app.services.model_identity import metrics
from app.services.model_identity.call_context import (
    ModelCallContext,
    bind_call_context,
    current_call_context,
    current_llm_purpose,
    peek_call_context,
    workload_scope,
)
from app.services.model_identity.principal import (
    PrincipalKind,
    service_principal,
    user_principal,
)
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _ctx(principal, workload=Workload.CHAT, **kw) -> ModelCallContext:
    return ModelCallContext(principal=principal, workload=workload, **kw)


def test_unbound_context_is_a_counted_service_identity() -> None:
    ctx = current_call_context()
    assert ctx.principal.kind is PrincipalKind.SERVICE
    assert ctx.workload is Workload.SYSTEM
    assert metrics.counter_value(metrics.IDENTITY_MISSING, "system") == 1
    assert peek_call_context() is None
    assert metrics.counter_value(metrics.IDENTITY_MISSING, "system") == 1


def test_bind_and_restore(alice, bob) -> None:
    with bind_call_context(_ctx(alice)):
        assert current_call_context().principal is alice
        with bind_call_context(_ctx(bob)):
            assert current_call_context().principal is bob
        assert current_call_context().principal is alice
    assert peek_call_context() is None


async def test_async_bind_and_restore(alice) -> None:
    async with bind_call_context(_ctx(alice)):
        assert current_call_context().principal is alice
    assert peek_call_context() is None


async def test_binding_survives_create_task_and_to_thread(alice) -> None:
    seen = {}

    async def child() -> None:
        seen["task"] = current_call_context().principal.subject_id
        seen["thread"] = (await asyncio.to_thread(lambda: current_call_context().principal.subject_id))

    async with bind_call_context(_ctx(alice)):
        await asyncio.create_task(child())
    assert seen == {"task": "u-alice", "thread": "u-alice"}


async def test_binding_inside_a_streaming_generator_is_scoped_to_the_stream(alice, bob) -> None:
    async def stream() -> AsyncIterator[object]:
        async with bind_call_context(_ctx(alice)):
            for _ in range(3):
                yield current_call_context().principal.subject_id
                await asyncio.sleep(0)

    async with bind_call_context(_ctx(bob)):
        out = [item async for item in stream()]
        assert current_call_context().principal is bob
    assert out == ["u-alice"] * 3


async def test_abandoned_generator_does_not_leave_its_binding_behind(alice, bob) -> None:
    async def stream() -> AsyncIterator[object]:
        async with bind_call_context(_ctx(alice)):
            yield 1
            yield 2

    gen = stream()
    await gen.__anext__()
    async with bind_call_context(_ctx(bob)):
        await gen.aclose()
        assert current_call_context().principal is bob


def test_out_of_order_exit_neither_drops_nor_revives_an_identity(alice, bob) -> None:
    first, second = bind_call_context(_ctx(alice)), bind_call_context(_ctx(bob))
    first.__enter__()
    second.__enter__()
    first.__exit__(None, None, None)
    assert current_call_context().principal is bob
    second.__exit__(None, None, None)
    assert peek_call_context() is None


def test_exit_without_enter_and_double_exit_are_harmless(alice) -> None:
    binding = bind_call_context(_ctx(alice))
    binding.__exit__(None, None, None)
    with binding:
        pass
    binding.__exit__(None, None, None)
    assert peek_call_context() is None


async def test_50_concurrent_users_never_see_each_others_identity() -> None:
    async def request(n: int) -> list:
        principal = user_principal({"userId": f"user-{n}"}, f"org-{n % 3}")
        async with bind_call_context(_ctx(principal, request_id=f"req-{n}")):
            seen = []
            for _ in range(5):
                await asyncio.sleep(0)
                ctx = current_call_context()
                seen.append((ctx.principal.subject_id, ctx.principal.org_id, ctx.request_id))
                seen.append(await asyncio.to_thread(_read))
                seen.append(await asyncio.create_task(_read_async()))
            return seen

    def _read() -> tuple[str, str, str | None]:
        ctx = current_call_context()
        return (ctx.principal.subject_id, ctx.principal.org_id, ctx.request_id)

    async def _read_async() -> tuple[str, str, str | None]:
        return _read()

    results = await asyncio.gather(*(request(n) for n in range(50)))
    for n, seen in enumerate(results):
        assert set(seen) == {(f"user-{n}", f"org-{n % 3}", f"req-{n}")}
    assert peek_call_context() is None
    assert metrics.counter_value(metrics.IDENTITY_MISSING, "system") == 0


async def test_sub_agent_tasks_inherit_the_callers_identity(alice) -> None:
    async def sub_agent() -> str:
        return current_call_context().principal.subject_id

    async with bind_call_context(_ctx(alice)):
        results = await asyncio.gather(*(asyncio.create_task(sub_agent()) for _ in range(4)))
    assert results == ["u-alice"] * 4


def test_workload_scope_narrows_within_a_class(alice) -> None:
    with bind_call_context(_ctx(alice, request_id="r1")):
        with workload_scope(Workload.QUERY_EMBEDDING):
            ctx = current_call_context()
            assert ctx.workload is Workload.QUERY_EMBEDDING
            assert ctx.principal is alice and ctx.request_id == "r1"
        assert current_call_context().workload is Workload.CHAT


def test_workload_scope_never_changes_class(alice) -> None:
    with bind_call_context(_ctx(alice)):
        with pytest.raises(ValueError):
            with workload_scope(Workload.INDEXING):
                pass
    with bind_call_context(_ctx(service_principal("indexing"), Workload.INDEXING)):
        with pytest.raises(ValueError):
            with workload_scope(Workload.CHAT):
                pass


def test_narrowed_context_does_not_reuse_the_wider_contexts_memo(alice) -> None:
    parent = _ctx(alice)
    parent.memo["k"] = "rendered"
    with bind_call_context(parent):
        with workload_scope(Workload.QUERY_EMBEDDING):
            assert current_call_context().memo == {}


def test_purpose_comes_from_the_usage_source_label(alice) -> None:
    with bind_call_context(_ctx(alice)):
        assert current_llm_purpose() == "main"
        with llm_usage_source("compaction"):
            assert current_llm_purpose() == "compaction"


async def test_a_task_that_outlives_its_binding_does_not_inherit_it(alice) -> None:
    release = asyncio.Event()
    seen = {}

    async def child() -> None:
        await release.wait()
        seen["peek"] = peek_call_context()
        seen["current"] = current_call_context()

    async with bind_call_context(_ctx(alice)):
        task = asyncio.create_task(child())
        await asyncio.sleep(0)
    release.set()
    await task
    assert seen["peek"] is None
    assert seen["current"].principal.kind is PrincipalKind.SERVICE


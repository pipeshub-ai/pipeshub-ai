"""Unit tests for the `input_staging.py` `ContextVar` staging API."""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    add_staged_skill_resources,
    peek_staged_input_files,
    peek_staged_skill_resources,
    set_staged_input_files_for_task,
    stage_input_files,
)


@pytest.fixture(autouse=True)
def _reset_staged_skill_resources():
    """`add_staged_skill_resources` deliberately has no `with`-block reset
    (see the module docstring) — tests must clean up after themselves so
    one test's staged files can't leak into the next."""
    token = input_staging._staged_skill_resources.set(None)
    yield
    input_staging._staged_skill_resources.reset(token)


@pytest.fixture(autouse=True)
def _reset_staged_input_files():
    """`set_staged_input_files_for_task` is likewise a bare `.set()` with
    no `with`-block reset (that's the whole point of it) — clean up so
    one test's task-local set can't leak into the next test's baseline
    within this same test-runner task."""
    token = input_staging._staged_input_files.set(None)
    yield
    input_staging._staged_input_files.reset(token)


class TestStageInputFiles:
    def test_peek_returns_none_outside_any_staging_block(self) -> None:
        assert peek_staged_input_files() is None

    def test_peek_returns_staged_files_inside_the_block(self) -> None:
        files = {"input/data.json": b'{"a": 1}'}
        with stage_input_files(files):
            assert peek_staged_input_files() == files

    def test_peek_returns_none_again_after_the_block_exits(self) -> None:
        with stage_input_files({"input/data.json": b"x"}):
            pass
        assert peek_staged_input_files() is None

    def test_none_files_is_a_no_op(self) -> None:
        with stage_input_files(None):
            assert peek_staged_input_files() is None

    def test_empty_dict_is_a_no_op(self) -> None:
        with stage_input_files({}):
            assert peek_staged_input_files() is None

    def test_nested_blocks_merge_and_restore_outer_value_on_exit(self) -> None:
        """The real nesting this pins: the spawn scheduler stages a
        prerequisite's artifacts around the whole dependent child's run,
        and inside that span `AgentTool.handle()` stages
        `parent_tool_results.json` for a grandchild — both sets must be
        visible to the grandchild's fresh sandbox, not just the inner one
        (see `stage_input_files`'s docstring)."""
        outer = {"input/outer.json": b"1"}
        inner = {"input/inner.json": b"2"}
        with stage_input_files(outer):
            with stage_input_files(inner):
                assert peek_staged_input_files() == {**outer, **inner}
            assert peek_staged_input_files() == outer
        assert peek_staged_input_files() is None

    def test_nested_block_with_colliding_path_inner_wins_then_outer_restored(self) -> None:
        outer = {"input/data.json": b"outer"}
        inner = {"input/data.json": b"inner"}
        with stage_input_files(outer):
            with stage_input_files(inner):
                assert peek_staged_input_files() == inner
            assert peek_staged_input_files() == outer

    async def test_visible_across_an_await_in_the_same_task(self) -> None:
        """The whole point of using a `ContextVar`: staging survives
        `await` points within the SAME task, which is how a real child
        agent's `Agent.run()` (many turns, each with several `await`s)
        stays inside the `with` block `AgentTool.handle()` establishes
        around `await runtime.run_child(...)`."""
        files = {"input/data.json": b"payload"}
        with stage_input_files(files):
            await asyncio.sleep(0)
            assert peek_staged_input_files() == files

    async def test_not_visible_in_a_task_created_before_staging_started(self) -> None:
        """A task created (and whose context was captured) BEFORE
        `stage_input_files` was entered never sees the staged files —
        `contextvars` propagation is determined at task-creation time."""
        started = asyncio.Event()
        seen: list[dict[str, bytes] | None] = []

        async def _reader() -> None:
            await started.wait()
            seen.append(peek_staged_input_files())

        task = asyncio.create_task(_reader())
        with stage_input_files({"input/data.json": b"x"}):
            started.set()
            await task
        assert seen == [None]


class TestSetStagedInputFilesForTask:
    """Pins the fix for `coding_sandbox_artifact_staging`'s PRE_TOOL_USE
    bug: a `with stage_input_files(...): await next_fn()` block resets the
    ContextVar before `tool.execute()` ever runs (PRE middleware's
    `next_fn()` only advances to the next middleware, never into the
    tool) — `set_staged_input_files_for_task` is a bare `.set()` instead,
    so the value survives past the point the `with` block would have
    reset it."""

    def test_peek_returns_none_before_any_set(self) -> None:
        assert peek_staged_input_files() is None

    def test_set_then_peek_returns_the_files(self) -> None:
        files = {"input/artifacts/chart.png": b"pngbytes"}
        set_staged_input_files_for_task(files)
        assert peek_staged_input_files() == files

    def test_value_survives_past_where_a_with_block_would_have_reset_it(self) -> None:
        """The exact bug this exists to fix: simulate the PRE_TOOL_USE
        pipeline finishing (as it would after `next_fn()` returns from
        the last middleware) and confirm the staged files are STILL
        visible afterward — unlike `stage_input_files`, which resets on
        `with`-block exit (see `test_peek_returns_none_again_after_the_block_exits`)."""
        def _pre_tool_use_pipeline_finishes() -> None:
            set_staged_input_files_for_task({"input/artifacts/chart.png": b"pngbytes"})
            # PRE_TOOL_USE dispatch returning here is the moment a
            # `with stage_input_files(...):` block would have reset the
            # var back to `None` — this function has no such reset.

        _pre_tool_use_pipeline_finishes()
        assert peek_staged_input_files() == {"input/artifacts/chart.png": b"pngbytes"}

    def test_none_is_a_no_op_against_an_empty_baseline(self) -> None:
        set_staged_input_files_for_task(None)
        assert peek_staged_input_files() is None

    def test_empty_dict_is_a_no_op_against_an_empty_baseline(self) -> None:
        set_staged_input_files_for_task({})
        assert peek_staged_input_files() is None

    def test_second_call_merges_with_and_can_override_the_first_calls_files(self) -> None:
        """Same merge semantics as `stage_input_files`'s nesting (inner
        wins on a path collision) — this scenario doesn't actually arise
        in production (each `run_code` call gets its own freshly copied
        task, see the isolation test below), but the function's own
        contract is a merge, not a replace, matching an enclosing
        `stage_input_files` block's semantics exactly."""
        set_staged_input_files_for_task({"input/artifacts/a.png": b"a"})
        set_staged_input_files_for_task({"input/artifacts/b.png": b"b"})
        assert peek_staged_input_files() == {
            "input/artifacts/a.png": b"a",
            "input/artifacts/b.png": b"b",
        }

    def test_merges_with_an_enclosing_stage_input_files_block(self) -> None:
        """A PRE hook using this nested inside a `run_child`-staged span
        (parent -> child handoff) must still see the parent's files —
        same merge semantics `stage_input_files` documents for nesting."""
        parent_files = {"input/parent_tool_results.json": b"{}"}
        with stage_input_files(parent_files):
            set_staged_input_files_for_task({"input/artifacts/chart.png": b"pngbytes"})
            assert peek_staged_input_files() == {
                "input/parent_tool_results.json": b"{}",
                "input/artifacts/chart.png": b"pngbytes",
            }

    async def test_visible_across_an_await_in_the_same_task(self) -> None:
        files = {"input/artifacts/chart.png": b"pngbytes"}
        set_staged_input_files_for_task(files)
        await asyncio.sleep(0)
        assert peek_staged_input_files() == files

    async def test_not_visible_in_a_sibling_task_created_before_the_set(self) -> None:
        """Pins the `asyncio.gather`-per-tool-call isolation this relies
        on: every tool call (parallel or solo) is dispatched through the
        turn loop's `asyncio.gather`, which copies context at task-
        creation time — a set made inside ONE call's task is invisible to
        a sibling task created before that set happened, exactly like
        `stage_input_files`'s equivalent isolation test."""
        started = asyncio.Event()
        seen: list[dict[str, bytes] | None] = []

        async def _sibling_tool_call() -> None:
            await started.wait()
            seen.append(peek_staged_input_files())

        task = asyncio.create_task(_sibling_tool_call())
        set_staged_input_files_for_task({"input/artifacts/chart.png": b"pngbytes"})
        started.set()
        await task
        assert seen == [None]


class TestAddStagedSkillResources:
    def test_peek_returns_none_when_nothing_staged(self) -> None:
        assert peek_staged_skill_resources() is None

    def test_add_then_peek_returns_the_files(self) -> None:
        files = {"skills/office-utils/scripts/unpack.py": b"print(1)"}
        add_staged_skill_resources(files)
        assert peek_staged_skill_resources() == files

    def test_second_call_merges_rather_than_clobbers(self) -> None:
        add_staged_skill_resources({"skills/office-utils/scripts/unpack.py": b"a"})
        add_staged_skill_resources({"skills/xlsx/scripts/verify_formulas.py": b"b"})
        assert peek_staged_skill_resources() == {
            "skills/office-utils/scripts/unpack.py": b"a",
            "skills/xlsx/scripts/verify_formulas.py": b"b",
        }

    def test_empty_dict_is_a_no_op(self) -> None:
        add_staged_skill_resources({"skills/a/x.py": b"1"})
        add_staged_skill_resources({})
        assert peek_staged_skill_resources() == {"skills/a/x.py": b"1"}

    def test_independent_of_stage_input_files(self) -> None:
        """The two staging mechanisms are separate ContextVars with
        different lifetimes (see the module docstring) — neither clears
        the other."""
        add_staged_skill_resources({"skills/a/x.py": b"1"})
        with stage_input_files({"input/data.json": b"x"}):
            assert peek_staged_skill_resources() == {"skills/a/x.py": b"1"}
        assert peek_staged_skill_resources() == {"skills/a/x.py": b"1"}
        assert peek_staged_input_files() is None

"""org_context's request-context scoping and detached roots, which give job and loop lines a root and an org."""

from __future__ import annotations

import asyncio
import logging
import re

import pytest

from app.connectors.core.sync.task_manager import SyncTaskManager
from app.core import org_context as oc
from app.utils import request_context as rc
from app.utils.logging_fields import current_log_fields, log_fields

_HEX = "[0-9a-f]{32}"


class TestBindScopesTheRequestContext:
    def test_for_org_without_a_root_mints_one_and_resets(self) -> None:
        assert rc.get_context() is None
        with oc.for_org("o1", job="j") as ctx:
            inner = rc.get_context()
            assert inner is not None and inner.org_id == "o1" and inner.root_id.startswith("sys-")
            assert ctx.request_id == inner.root_id
            envelope = rc.inject_envelope({})
            assert envelope == {"requestId": inner.root_id, "orgId": "o1"}
        assert rc.get_context() is None

    def test_inside_a_request_keeps_the_root_and_restores_the_org(self) -> None:
        token = rc.set_context("u1-req")
        try:
            rc.set_context_org("o1")
            outer = rc.get_context()
            with oc.system("enumerate"):
                assert rc.get_context().root_id == "u1-req" and rc.get_context().org_id is None
                with oc.for_org("o2", job="j"):
                    assert (rc.get_context().root_id, rc.get_context().org_id) == ("u1-req", "o2")
                assert rc.get_context().org_id is None
            assert rc.get_context() is outer and outer.org_id == "o1"
        finally:
            rc.reset_context(token)

    def test_same_sanitized_org_does_not_rebind(self) -> None:
        token = rc.set_context("u1-req")
        try:
            rc.set_context_org("o 1")  # sanitized to "o1"
            outer = rc.get_context()
            with oc.bind(oc.OrgContext("o 1", "u", "user", "jwt", "u1-req")):
                assert rc.get_context() is outer
        finally:
            rc.reset_context(token)

    def test_an_exception_in_the_body_still_resets_both(self) -> None:
        with pytest.raises(RuntimeError), oc.for_org("o1", job="j"):
            raise RuntimeError("x")
        assert rc.get_context() is None and oc.peek() is None

    def test_peek_never_raises(self) -> None:
        assert oc.peek() is None
        with oc.system("x") as ctx:
            assert oc.peek() is ctx


class TestSpawnDetached:
    def test_named_and_unnamed_roots_and_nothing_inherited(self) -> None:
        async def read() -> tuple[str, object, dict]:
            return rc.get_context().root_id, oc.peek(), dict(current_log_fields())

        async def main() -> tuple:
            with oc.for_org("o1", job="j"), log_fields(run_id="r1"):
                named = await oc.spawn_detached(read(), name="kh-keeper")
                unnamed = await oc.spawn_detached(read())
            return named, unnamed

        (named_root, named_org, named_fields), (unnamed_root, _, _) = asyncio.run(main())
        assert re.fullmatch(f"sys-kh-keeper-{_HEX}", named_root)
        assert re.fullmatch(f"sys-{_HEX}", unnamed_root)
        assert named_org is None and named_fields == {}

    def test_long_unsafe_name_gives_a_root_that_survives_sanitizing(self) -> None:
        async def root() -> str:
            return rc.get_context().root_id

        async def main() -> str:
            return await oc.spawn_detached(root(), name="dispose connector_" + "a" * 24)

        value = asyncio.run(main())
        assert len(value) <= 64 and rc.sanitize_root_id(value) == value


class TestSyncRoots:
    def test_syncs_under_a_detached_root_get_their_own_and_keep_the_org(self) -> None:
        manager = SyncTaskManager()
        seen: list[tuple[str, str | None]] = []

        async def sync() -> None:
            ctx = rc.get_context()
            seen.append((ctx.root_id, ctx.org_id))

        async def post_startup() -> str:
            with oc.for_org("o1", job="resume_sync"):
                await (await manager.start_sync("c1", sync()))
                await (await manager.start_sync("c2", sync()))
            return rc.get_context().root_id

        async def main() -> str:
            return await oc.spawn_detached(post_startup(), name="connector_post_startup")

        detached_root = asyncio.run(main())
        (root_a, org_a), (root_b, org_b) = seen
        assert len({root_a, root_b, detached_root}) == 3 and org_a == org_b == "o1"

    def test_a_sync_under_a_request_or_message_root_keeps_it(self) -> None:
        manager = SyncTaskManager()
        seen: list[str] = []

        async def sync() -> None:
            seen.append(rc.get_context().root_id)

        async def main() -> None:
            for root in ("u1-req", "sys-from-node-scheduler"):
                token = rc.set_context(root)
                try:
                    await (await manager.start_sync("c1", sync()))
                finally:
                    rc.reset_context(token)

        asyncio.run(main())
        assert seen == ["u1-req", "sys-from-node-scheduler"]

    @pytest.mark.parametrize("detached", [True, False], ids=["detached-root", "no-root"])
    def test_start_body_and_failure_lines_share_the_syncs_root(self, detached: bool) -> None:
        manager = SyncTaskManager()
        roots: dict[str, str] = {}

        # Keyed by level, not text: the start line is INFO, the done callback's failure line ERROR.
        class RootByLevel(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                roots[record.levelname] = rc.current_display_id()

        async def sync() -> None:
            roots["body"] = rc.current_display_id()
            raise RuntimeError("boom")

        async def trigger() -> None:
            task = await manager.start_sync("c1", sync())
            with pytest.raises(RuntimeError):
                await task
            await asyncio.sleep(0)  # let the done callback run

        async def main() -> None:
            await (oc.spawn_detached(trigger(), name="post_startup") if detached else trigger())

        # A private logger: the module's level or handlers, set by other tests, cannot drop a line.
        manager.logger = logging.getLogger(f"test.sync_roots.{detached}")
        manager.logger.setLevel(logging.DEBUG)
        manager.logger.propagate = False
        manager.logger.addHandler(RootByLevel())
        asyncio.run(main())
        assert roots["INFO"] == roots["body"] == roots["ERROR"] and roots["body"].startswith("sys-")

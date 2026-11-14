"""qa stack up|down and qa run --session: one managed stack across several runs."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qa import config, run, session, stack, testing

HEAD = "0123456789abcdef" + "0" * 24


class SessionCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = testing.make_repo()
        self.addCleanup(tmp.cleanup)
        self.cfg = config.load(self.root)
        out = tempfile.TemporaryDirectory()
        self.addCleanup(out.cleanup)
        self.out = Path(out.name)
        env = mock.patch.dict(os.environ, {"QA_RUN_ID": "9", "QA_COMPOSE_OVERRIDES": "", "QA_PORT_OFFSET": ""})
        env.start()
        self.addCleanup(env.stop)
        for k in ("QA_COMPOSE_PROJECT", "PIPESHUB_TEST_USER_PASSWORD", run.LEAVE_MODELS, "QA_KEEP_IMAGE"):
            os.environ.pop(k, None)

    def up(self, **kw):
        with mock.patch.object(stack.Stack, "start") as start:
            data = session.up(self.cfg, self.out, {"broker": "kafka", "compose_profiles": ["kafka"]}, head=HEAD, **kw)
        return data, start


class TestUp(SessionCase):
    def test_up_starts_one_stack_and_saves_how_to_reach_it(self) -> None:
        data, start = self.up()
        start.assert_called_once()
        saved = session.load(session.path(self.out))
        self.assertEqual(saved, data)
        self.assertEqual((saved["project"], saved["broker"], saved["compose_profiles"]), ("qa-0123456789ab-9-neo4j", "kafka", ["kafka"]))
        self.assertEqual(saved["image"], "pipeshub-ai:qa-0123456789ab")
        self.assertEqual(saved["env"]["QA_STACK_GRAPH_DB"], "neo4j")
        self.assertEqual(saved["env"]["PIPESHUB_TEST_USER_PASSWORD"], os.environ["PIPESHUB_TEST_USER_PASSWORD"])
        self.assertEqual(saved["env"][run.LEAVE_MODELS], "1")
        self.assertEqual(saved["extra_env"]["PLUS_STACK"], "1")  # the overlay hook ran
        self.assertEqual(stat.S_IMODE(session.path(self.out).stat().st_mode), 0o600)

    def test_a_stack_that_fails_to_start_is_removed_and_no_session_is_left(self) -> None:
        with mock.patch.object(stack.Stack, "start", side_effect=RuntimeError("unhealthy")), \
             mock.patch.object(stack.Stack, "stop") as stop, mock.patch.object(stack.Stack, "collect_logs"):
            with self.assertRaises(RuntimeError):
                session.up(self.cfg, self.out, head=HEAD)
        stop.assert_called_once()
        self.assertFalse(session.path(self.out).exists())

    def test_a_failing_setup_hook_starts_nothing(self) -> None:
        with mock.patch.dict(os.environ, {"PLUS_FAIL": "1"}), mock.patch.object(stack.Stack, "start") as start:
            with self.assertRaisesRegex(RuntimeError, "stack setup"):
                session.up(self.cfg, self.out, head=HEAD)
        start.assert_not_called()


class TestUseAndDown(SessionCase):
    def test_use_points_the_process_at_the_saved_stack(self) -> None:
        data, _ = self.up()
        os.environ.pop("QA_STACK_GRAPH_DB", None)
        self.assertEqual(session.use(session.path(self.out)), data["base_url"])
        self.assertEqual(os.environ["QA_STACK_GRAPH_DB"], "neo4j")

    def test_down_stops_the_saved_project_removes_its_image_and_the_session(self) -> None:
        self.up()
        seen = []
        with mock.patch.object(stack.Stack, "stop", autospec=True, side_effect=lambda s: seen.append((s.project, s.overrides))), \
             mock.patch.object(stack.Stack, "collect_logs"), mock.patch.object(run, "_remove_image") as rm:
            session.down(self.cfg, self.out)
        self.assertEqual(seen[0][0], "qa-0123456789ab-9-neo4j")
        self.assertTrue(seen[0][1])  # the overlay and image files compose was started with
        rm.assert_called_once()
        self.assertEqual(rm.call_args.args[0], "pipeshub-ai:qa-0123456789ab")
        self.assertFalse(session.path(self.out).exists())
        self.assertEqual(session.down(self.cfg, self.out), ["no session"])

    def test_down_keeps_the_image_when_asked(self) -> None:
        self.up()
        with mock.patch.dict(os.environ, {"QA_KEEP_IMAGE": "1"}), mock.patch.object(stack.Stack, "stop"), \
             mock.patch.object(stack.Stack, "collect_logs"), mock.patch.object(run, "_remove_image") as rm:
            session.down(self.cfg, self.out)
        rm.assert_not_called()

    def test_run_with_a_session_runs_external_against_the_saved_url(self) -> None:
        import contextlib, io
        from qa import __main__ as qa_main
        data, _ = self.up()
        seen = {}

        def fake_run(cfg, plan, rows, out, **kw):
            seen.update(kw, graph=os.environ.get("QA_STACK_GRAPH_DB"))
            return {"schema": "qa-results/v1", "tests": [], "seconds": 0}
        with mock.patch.dict(os.environ, {"QA_ROOT": str(self.root)}), mock.patch.object(run, "run", side_effect=fake_run), \
             contextlib.redirect_stdout(io.StringIO()):
            code = qa_main.main(["--out", str(self.out / "r1"), "run", "--ids", "py:tests/test_a.py", "--session", str(session.path(self.out))])
        self.assertEqual(code, 0)
        self.assertEqual((seen["stack_mode"], seen["base_url"], seen["graph"]), ("external", data["base_url"], "neo4j"))

    def test_load_refuses_a_file_that_is_not_a_session(self) -> None:
        p = self.out / "x.json"
        p.write_text('{"schema": "other"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            session.load(p)


if __name__ == "__main__":
    unittest.main()

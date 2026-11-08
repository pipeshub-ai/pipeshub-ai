"""WHO on every line: the record factory reads OrgContext, the model call context and the
bound fields, and the formatters render them through ``log_record_fields``."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.core import org_context as oc
from app.services.model_identity.call_context import ModelCallContext, bind_call_context
from app.services.model_identity.principal import service_principal, user_principal
from app.services.model_identity.workload import Workload
from app.utils.logger import JsonFormatter, TextFormatter, log_record_fields
from app.utils.logging_fields import JSON_STANDARD_KEYS, log_fields
from app.utils.request_context import reset_context, set_context, set_context_org
from app.utils.structured_logging import DefaultLoggingProvider

if TYPE_CHECKING:
    from collections.abc import Callable

BACKEND = Path(__file__).resolve().parents[3]
_SCRUBBED_ENV = ("LOG_LEVEL", "LOG_LEVELS", "LOG_FORMAT", "DATA_STORE", "PYTHONIOENCODING", "NO_COLOR", "FORCE_COLOR")


class _HashOnly:
    """The EE provider's shape: hash_org_id only, no log_identity override."""

    def __init__(self, raises: str = "") -> None:
        self.raises = raises

    def hash_org_id(self, org_id: str) -> str:
        if self.raises == "hash":
            raise RuntimeError("hash hook failed")
        return f"h:{org_id}"

    def log_identity(self, ids: dict) -> dict:
        if self.raises == "identity":
            raise RuntimeError("identity hook failed")
        return {}


class _Hashing(DefaultLoggingProvider):
    def log_identity(self, ids: dict) -> dict:
        return {"org_id": f"h:{ids['org_id']}"} if "org_id" in ids else {}


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Callable[[object], None]:
    def use(p) -> None:
        monkeypatch.setattr("app.utils.logger.get_provider", lambda: p)

    use(DefaultLoggingProvider())
    return use


def _record(msg: str = "m", **extra: object) -> logging.LogRecord:
    record = logging.getLogRecordFactory()("app.t", logging.INFO, "x.py", 7, msg, (), None)
    record.__dict__.update(extra)
    return record


def _user_call(org: str = "o1", user: str = "u1", **kw: object) -> ModelCallContext:
    return ModelCallContext(principal=user_principal({"userId": user, "orgId": org}), workload=Workload.CHAT, **kw)


class TestIdentitySource:
    def test_org_context_user_request(self, provider) -> None:
        with oc.bind(oc.OrgContext("o1", "u1", "user", "jwt", "-")):
            assert log_record_fields(_record()) == {"org_id": "o1", "user_id": "u1"}

    def test_job_has_kind_and_source(self, provider) -> None:
        with oc.for_org("o2", job="kh_restamp"):
            fields = log_record_fields(_record())
        assert fields == {"org_id": "o2", "ctx_kind": "job", "ctx_source": "job:kh_restamp"}

    def test_system_has_neither_id(self, provider) -> None:
        with bind_call_context(_user_call()), oc.system("boot"):
            fields = log_record_fields(_record())
        assert "org_id" not in fields and "user_id" not in fields
        assert fields["ctx_kind"] == "system"

    def test_call_context_only(self, provider) -> None:
        with bind_call_context(_user_call(conversation_id="c1", agent_id="a1", record_id=None)):
            fields = log_record_fields(_record())
        assert fields == {"org_id": "o1", "user_id": "u1", "conversation_id": "c1", "agent_id": "a1", "workload": "chat"}

    def test_service_principal_has_no_user(self, provider) -> None:
        call = ModelCallContext(principal=service_principal(org_id="o9"), workload=Workload.INDEXING, record_id="r1")
        with bind_call_context(call):
            fields = log_record_fields(_record())
        assert fields == {"org_id": "o9", "record_id": "r1", "workload": "indexing"}

    def test_one_source_never_mixes_scopes(self, provider) -> None:
        with bind_call_context(_user_call("o1", "u1")), oc.for_org("o7", job="j"):
            fields = log_record_fields(_record())
        assert fields["org_id"] == "o7" and "user_id" not in fields

    def test_request_context_org_is_the_fallback(self, provider) -> None:
        token = set_context("r-1")
        try:
            set_context_org("o5")
            assert log_record_fields(_record()) == {"org_id": "o5"}
        finally:
            reset_context(token)

    def test_nothing_bound_no_context_attribute(self, provider) -> None:
        assert not hasattr(_record(), "_log_ctx")


class TestProvider:
    def test_hashing_provider_hides_user(self, provider) -> None:
        provider(_Hashing())
        with oc.bind(oc.OrgContext("o1", "u1", "user", "jwt", "-")):
            assert log_record_fields(_record()) == {"org_id": "h:o1"}

    def test_ee_shaped_provider_keeps_organization_id_hash_as_today(self, provider) -> None:
        provider(_HashOnly())
        token = set_context("r-2")
        try:
            set_context_org("o1")
            request_line = _record()
        finally:
            reset_context(token)
        with oc.for_org("o2", job="j"), log_fields(run_id="r"):
            job_line = _record(token="t0k3n")
        assert request_line.organization_id_hash == "h:o1"
        assert job_line.organization_id_hash == "h:o2"
        assert "organization_id_hash" not in log_record_fields(request_line)
        fields = log_record_fields(job_line)
        assert "org_id" not in fields and "user_id" not in fields
        assert fields["token"] == "[REDACTED]" and fields["run_id"] == "r"
        assert "organization_id_hash" not in TextFormatter().format(job_line)

    @pytest.mark.parametrize("hook", ["identity", "hash"])
    def test_a_failing_hook_never_stops_the_line(self, provider, capsys, monkeypatch, hook: str) -> None:
        from app.utils import logging_fields

        monkeypatch.setattr(logging_fields, "_log_once_last", {})
        provider(_HashOnly(raises=hook))
        with oc.for_org("o1", job="j"), log_fields(run_id="r1", connector_id="c1"):
            first, second = _record("a"), _record("b")
        assert first.getMessage() == "a" and second.getMessage() == "b"
        assert capsys.readouterr().err.count("log context not added") == 1
        fields = log_record_fields(first)
        # One failing hook drops only what it makes: the bound and context fields stay.
        assert fields["run_id"] == "r1" and fields["connector_id"] == "c1" and fields["ctx_kind"] == "job"
        if hook == "identity":
            assert first.organization_id_hash == "h:o1"
        else:
            assert not hasattr(first, "organization_id_hash")

    def test_a_failing_context_read_keeps_the_bound_fields(self, provider, monkeypatch) -> None:
        from app.utils import logging_fields

        monkeypatch.setattr(logging_fields, "_log_once_last", {})
        monkeypatch.setattr("app.utils.logger.peek_call_context", lambda: 1 / 0)
        with log_fields(run_id="r1"):
            assert log_record_fields(_record()) == {"run_id": "r1"}


class TestRender:
    def test_order_and_extra_never_touch_identity(self, provider) -> None:
        with oc.bind(oc.OrgContext("o1", "u1", "user", "jwt", "-")), bind_call_context(_user_call(agent_id="a1")):
            with log_fields(connector_id="bound", run_id="r1"):
                record = _record(connector_id="extra", org_id="fake", user_id="fake", status="ok")
        assert list(log_record_fields(record).items()) == [
            ("org_id", "o1"), ("user_id", "u1"), ("agent_id", "a1"), ("workload", "chat"),
            ("connector_id", "extra"), ("run_id", "r1"), ("status", "ok"),
        ]

    def test_text_and_json_carry_the_same_fields(self, provider) -> None:
        with oc.bind(oc.OrgContext("o1", "u1", "user", "jwt", "-")), log_fields(run_id="r 1", attempt=2):
            record = _record("tool_call.end", ok=True, skipped=None)
        text = TextFormatter().format(record)
        assert text.endswith(' | org_id=o1 user_id=u1 run_id="r 1" attempt=2 ok=true')
        out = json.loads(JsonFormatter().format(record))
        assert {k: out[k] for k in ("org_id", "user_id", "run_id", "attempt", "ok")} == {
            "org_id": "o1", "user_id": "u1", "run_id": "r 1", "attempt": 2, "ok": True}
        assert "skipped" not in text and out.get("skipped") is None

    def test_json_standard_keys_are_what_the_formatter_writes(self) -> None:
        out = json.loads(JsonFormatter().format(_record(stack="s", timestamp="t")))
        assert set(out) == JSON_STANDARD_KEYS - {"stack"}
        assert "stack=" not in TextFormatter().format(_record(stack="s"))

    def test_factory_does_no_frame_walk(self, provider, monkeypatch: pytest.MonkeyPatch) -> None:
        import inspect

        def boom(*_a: object, **_k: object) -> None:
            raise AssertionError("frame walk in the record factory")

        with oc.bind(oc.OrgContext("o1", "u1", "user", "jwt", "-")), log_fields(run_id="r"):
            monkeypatch.setattr(sys, "_getframe", boom)
            monkeypatch.setattr(inspect, "stack", boom)
            monkeypatch.setattr(inspect, "currentframe", boom)
            record = _record()
        assert log_record_fields(record)["run_id"] == "r"


def _run(code: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
    env["PYTHONPATH"] = str(BACKEND)
    prelude = "import sys, asyncio, logging\nimport app.utils.logger as L\nfrom app.core import org_context as oc\n" \
              "from app.utils import request_context as rc\nlog = logging.getLogger('app.p1b')\n"
    result = subprocess.run([sys.executable, "-c", prelude + textwrap.dedent(code)], cwd=BACKEND, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout


def _line(out: str, marker: str) -> str:
    lines = [line for line in out.splitlines() if marker in line]
    assert lines, out
    return lines[0]


class TestRenderedLineFreshInterpreter:
    """The line a service prints, with nothing but the import-time wiring."""

    def test_request_line_carries_org_and_user(self) -> None:
        line = _line(_run("""
            rc.set_context('u1-abc')
            oc.set_request_context(oc.OrgContext('org1', 'user1', 'user', 'jwt', 'u1-abc'))
            rc.set_context_org('org1')
            log.info('marker')
        """), "marker")
        assert line.endswith("marker | org_id=org1 user_id=user1"), line

    def test_job_line_gets_a_sys_root_org_and_kind(self) -> None:
        line = _line(_run("""
            with oc.for_org('org2', job='kh_restamp'):
                log.info('marker')
        """), "marker")
        assert "[req:sys-" in line and line.endswith("| org_id=org2 ctx_kind=job ctx_source=job:kh_restamp"), line

    def test_job_publish_envelope_carries_root_and_org(self) -> None:
        out = _run("""
            with oc.for_org('org3', job='x'):
                m = rc.inject_envelope({})
            print('ENV', m.get('orgId'), m.get('requestId', '')[:4])
        """)
        assert "ENV org3 sys-" in out

    def test_agent_turn_line(self) -> None:
        line = _line(_run("""
            from app.services.model_identity.call_context import ModelCallContext, bind_call_context
            from app.services.model_identity.principal import user_principal
            from app.services.model_identity.workload import Workload
            ctx = ModelCallContext(principal=user_principal({'userId': 'user4', 'orgId': 'org4'}),
                                   workload=Workload.CHAT, conversation_id='conv4', agent_id='agent4')
            with bind_call_context(ctx):
                log.info('marker')
        """), "marker")
        assert line.endswith("| org_id=org4 user_id=user4 conversation_id=conv4 agent_id=agent4 workload=chat"), line

    def test_detached_task_gets_a_named_sys_root(self) -> None:
        line = _line(_run("""
            async def work():
                log.info('marker')
            async def main():
                await oc.spawn_detached(work(), name='kh-keeper')
            asyncio.run(main())
        """), "marker")
        assert "[req:sys-kh-keeper-" in line, line

    def test_bound_fields_render(self) -> None:
        line = _line(_run("""
            from app.utils.logging_fields import log_fields
            with log_fields(connector_id='c5', run_id='r5'):
                log.info('marker')
        """), "marker")
        assert line.endswith("| connector_id=c5 run_id=r5"), line


class TestImportGraph:
    def test_logger_does_not_load_the_failure_stack(self) -> None:
        out = _run("print('LOADED', sorted(m for m in ('app.utils.error_class', 'app.agents.mcp', 'openai', 'anthropic') if m in sys.modules))")
        assert "LOADED []" in out

    def test_contexts_do_not_import_the_logger(self) -> None:
        env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
        env["PYTHONPATH"] = str(BACKEND)
        code = ("import sys, app.core.org_context, app.services.model_identity.call_context, app.utils.logging_fields;"
                "print('LOGGER', 'app.utils.logger' in sys.modules)")
        result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=120)
        assert "LOGGER False" in result.stdout, result.stderr


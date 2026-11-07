"""One logging pipeline: levels from env, one stdout handler, text and JSON formats.

In-process tests use ``logging_state``, which puts back the root handlers, every
logger level and the module's env snapshot, so they do not depend on test order.
What the module does at import is checked in a fresh interpreter, because under
pytest the root logger already holds pytest's handlers when it is imported.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.utils.logger as L
from app.utils.logger import TEXT_FORMAT, JsonFormatter, TextFormatter, create_logger
from app.utils.request_context import reset_context, set_context

if TYPE_CHECKING:
    from collections.abc import Iterator

BACKEND = Path(__file__).resolve().parents[3]
DOCS = BACKEND.parents[1] / "docs" / "logging.md"
_SCRUBBED_ENV = ("LOG_LEVEL", "LOG_LEVELS", "LOG_FORMAT", "DATA_STORE", "PYTHONIOENCODING", "NO_COLOR", "FORCE_COLOR")


@pytest.fixture
def logging_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    root = logging.getLogger()
    loggers = [root, *(lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger))]
    levels = {lg: lg.level for lg in loggers}
    handlers = root.handlers[:]
    synced, services = dict(L._synced), set(L._SERVICE_LOGGERS)
    for name in ("LOG_LEVEL", "LOG_LEVELS", "LOG_FORMAT", "DATA_STORE"):
        monkeypatch.delenv(name, raising=False)
    L._parse_levels.cache_clear()
    L._parse_format.cache_clear()
    # Start from a snapshot of this (cleared) env: one taken under another test's patched
    # os.getenv would make the first create_logger re-apply levels over caplog's.
    L._synced.clear()
    L._sync_from_env()
    yield
    root.handlers[:] = handlers
    for lg in [root, *(lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger))]:
        level = levels.get(lg, logging.NOTSET)
        if lg.level != level:
            lg.setLevel(level)
    L._synced.clear()
    L._synced.update(synced)
    L._SERVICE_LOGGERS.clear()
    L._SERVICE_LOGGERS.update(services)


@pytest.fixture
def console(logging_state, monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """An empty root with the module's own console handler, writing to a StringIO.
    pytest adds its capture handlers to root again for the test's call phase."""
    logging.getLogger().handlers[:] = []
    monkeypatch.setattr("app.utils.logger.get_provider", lambda: _Provider())
    L._install_console_handler()
    stream = io.StringIO()
    _tagged()[0].setStream(stream)
    return stream


class _Provider:
    def __init__(self, files: bool = False, formatter: logging.Formatter | None = None) -> None:
        self.files = files
        self.formatter = formatter
        self.defaults_seen: list[logging.Formatter] = []

    def console_formatter(self, default: logging.Formatter) -> logging.Formatter:
        self.defaults_seen.append(default)
        return self.formatter or default

    def file_logging_enabled(self) -> bool:
        return self.files

    def hash_org_id(self, org_id: str) -> str | None:
        return None


def _record(msg: str = "m", level: int = logging.INFO, args: tuple = (), exc_info=None, **extra: object) -> logging.LogRecord:
    record = logging.getLogRecordFactory()("t.log", level, "/x/f.py", 7, msg, args, exc_info)
    record.__dict__.update(extra)
    return record


def _tagged() -> list[logging.Handler]:
    return [h for h in logging.getLogger().handlers if getattr(h, "_pipeshub_console", False)]


def _run(code: str, *, path: Path | None = None, **env: str) -> subprocess.CompletedProcess:
    full = {k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV}
    full.update(PYTHONPATH=str(BACKEND), **env)
    argv = [sys.executable, str(path)] if path else [sys.executable, "-c", textwrap.dedent(code)]
    return subprocess.run(argv, cwd=BACKEND, env=full, capture_output=True, timeout=120)


def _out(result: subprocess.CompletedProcess) -> tuple[str, str]:
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.decode("utf-8"), result.stderr.decode("utf-8")


_PRELUDE = """
import logging, app.utils.logger as L
svc = L.create_logger("p1a_svc")
mod = logging.getLogger("app.p1a.mod")
"""


# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------


class TestParseLevels:
    @pytest.mark.parametrize(
        ("name", "level"),
        [("debug", 10), ("INFO", 20), ("Warning", 30), ("warn", 30), ("error", 40), ("CRITICAL", 50), ("fatal", 50),
         (" debug ", 10)],
    )
    def test_every_standard_name(self, logging_state, name: str, level: int) -> None:
        assert L._parse_levels(name, "") == (level, {})

    @pytest.mark.parametrize("name", ["verbose", "notset", "10"])
    def test_unknown_or_notset_means_info_with_one_warning(self, logging_state, caplog, name: str) -> None:
        with caplog.at_level(logging.WARNING, logger="app.utils.logger"):
            assert L._parse_levels(name, "")[0] == logging.INFO
            L._parse_levels(name, "")
        assert len([r for r in caplog.records if "LOG_LEVEL" in r.getMessage()]) == 1

    def test_unset_means_info_without_a_warning(self, logging_state, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="app.utils.logger"):
            assert L._parse_levels("", "") == (logging.INFO, {})
        assert not caplog.records

    def test_overrides_trim_skip_empty_and_later_wins(self, logging_state) -> None:
        assert L._parse_levels("", " a.b=DEBUG , ,c=warning,a.b=error,root=debug") == (
            logging.INFO, {"a.b": logging.ERROR, "c": logging.WARNING, "root": logging.DEBUG},
        )

    def test_malformed_entries_are_skipped_with_a_warning_each(self, logging_state, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="app.utils.logger"):
            assert L._parse_levels("", "noequals,c=loud,=DEBUG,d=INFO") == (logging.INFO, {"d": logging.INFO})
        assert [r.getMessage().split("'")[1] for r in caplog.records] == ["noequals", "c=loud", "=DEBUG"]


class TestSyncFromEnv:
    def _sync(self, monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        L._sync_from_env()

    def test_default_leaves_app_inheriting_info(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVELS="")
        assert logging.getLogger().level == logging.INFO
        assert logging.getLogger("app").level == logging.NOTSET
        assert logging.getLogger("app.x.y").getEffectiveLevel() == logging.INFO

    def test_debug_opens_app_and_service_loggers_not_third_party(self, logging_state, monkeypatch) -> None:
        create_logger("t_sync_debug_svc")
        self._sync(monkeypatch, LOG_LEVEL="debug")
        assert logging.getLogger("app.x.y").getEffectiveLevel() == logging.DEBUG
        assert logging.getLogger("t_sync_debug_svc").getEffectiveLevel() == logging.DEBUG
        assert logging.getLogger().level == logging.INFO
        for name in ("openai", "httpcore", "botocore.endpoint"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.INFO

    def test_warning_quiets_everything(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVEL="warning")
        for name in ("app.x", "httpcore", "t_any"):
            assert logging.getLogger(name).getEffectiveLevel() == logging.WARNING

    def test_root_override_opens_third_party(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVELS="root=DEBUG")
        assert logging.getLogger("httpcore").getEffectiveLevel() == logging.DEBUG

    def test_back_to_info_resets_app(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVEL="debug")
        self._sync(monkeypatch, LOG_LEVEL="info")
        assert logging.getLogger("app").level == logging.NOTSET

    def test_hard_coded_quietening_unless_overridden(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, DATA_STORE="neo4j")
        assert logging.getLogger("opensearch").level == logging.WARNING
        assert logging.getLogger("neo4j.notifications").level == logging.ERROR
        self._sync(monkeypatch, LOG_LEVELS="opensearch=DEBUG,neo4j.notifications=WARNING")
        assert logging.getLogger("opensearch").level == logging.DEBUG
        assert logging.getLogger("neo4j.notifications").level == logging.WARNING

    def test_unchanged_env_sets_no_level(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVEL="debug")
        calls: list[int] = []
        monkeypatch.setattr(logging.Logger, "setLevel", lambda self, level: calls.append(level))
        L._sync_from_env()
        create_logger("t_sync_unchanged_svc")
        assert calls == [logging.DEBUG]  # only the new service logger's own level

    def test_equal_levels_are_not_set_again(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVEL="info")
        calls: list[str] = []
        real = logging.Logger.setLevel
        monkeypatch.setattr(logging.Logger, "setLevel", lambda self, level: (calls.append(self.name), real(self, level)))
        self._sync(monkeypatch, LOG_LEVEL="INFO")  # a different string, the same levels
        assert calls == []

    def test_caplog_level_survives_create_logger(self, logging_state, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        create_logger("t_caplog_existing")
        create_logger("t_caplog_new_name")
        assert logging.getLogger().level == logging.DEBUG
        logging.getLogger("app.t.caplog").debug("still captured")
        assert "still captured" in caplog.text

    def test_changed_env_between_calls_is_applied(self, logging_state, monkeypatch) -> None:
        create_logger("t_env_change")
        monkeypatch.setenv("LOG_LEVELS", "t_env_change=ERROR")
        create_logger("t_env_change")
        assert logging.getLogger("t_env_change").level == logging.ERROR

    def test_create_logger_levels(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVEL="debug", LOG_LEVELS="t_cl_override=WARNING")
        assert create_logger("t_cl_debug").level == logging.DEBUG
        assert create_logger("t_cl_override").level == logging.WARNING
        assert create_logger("app.t_cl_module").level == logging.NOTSET

    def test_startup_sync_reapplies_overrides_uvicorn_reset(self, logging_state, monkeypatch) -> None:
        self._sync(monkeypatch, LOG_LEVELS="uvicorn.access=WARNING")
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)  # what uvicorn's dictConfig does
        logging.getLogger().setLevel(logging.DEBUG)
        L.install_log_redaction()
        assert logging.getLogger("uvicorn.access").level == logging.WARNING
        assert logging.getLogger().level == logging.DEBUG  # only the LOG_LEVELS entries are forced


class TestLevelsAtImport:
    def test_warning_hides_info_of_every_kind(self) -> None:
        out, err = _out(_run(_PRELUDE + "mod.info('mod-info'); svc.info('svc-info'); "
                             "logging.getLogger('httpcore').info('lib-info'); mod.warning('mod-warn')",
                             LOG_LEVEL="warning"))
        assert "mod-warn" in out
        assert not {"mod-info", "svc-info", "lib-info"} & set(re.findall(r"\w+-info", out + err))

    def test_debug_opens_app_but_not_third_party(self) -> None:
        code = _PRELUDE + "".join(
            f"logging.getLogger('{name}').debug('{tag}'); "
            for name, tag in (("app.p1a.mod", "app-debug"), ("p1a_svc", "svc-debug"), ("openai", "openai-debug"),
                              ("httpcore", "httpcore-debug"), ("botocore.endpoint", "boto-debug"))
        )
        out, _ = _out(_run(code, LOG_LEVEL="debug"))
        assert "app-debug" in out and "svc-debug" in out
        assert "openai-debug" not in out and "httpcore-debug" not in out and "boto-debug" not in out
        out, _ = _out(_run(code, LOG_LEVEL="debug", LOG_LEVELS="root=DEBUG"))
        assert "httpcore-debug" in out and "openai-debug" in out

    def test_notset_falls_back_to_info_with_a_warning(self) -> None:
        out, _ = _out(_run(_PRELUDE + "mod.debug('mod-debug'); mod.info('mod-info')", LOG_LEVEL="notset"))
        assert "mod-info" in out and "mod-debug" not in out
        assert out.count("LOG_LEVEL") == 1


# ---------------------------------------------------------------------------
# One stream
# ---------------------------------------------------------------------------


class TestOneStream:
    def test_create_logger_adds_no_console_handler(self, console) -> None:
        logger = create_logger("t_stream_noconsole")
        assert logger.propagate is True
        assert not [h for h in logger.handlers if type(h) is logging.StreamHandler]

    def test_each_line_is_written_once(self, console) -> None:
        create_logger("t_stream_once").info("svc-line")
        logging.getLogger("app.t.stream").info("mod-line")
        lines = console.getvalue().splitlines()
        assert [line for line in lines if "svc-line" in line or "mod-line" in line] == [
            line for line in lines if line.endswith(("svc-line", "mod-line"))
        ]
        assert len(lines) == 2

    def test_file_gets_its_service_lines_only(self, console, monkeypatch, tmp_path) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("app.utils.logger.get_provider", lambda: _Provider(files=True))
        logger = create_logger("t_stream_file")
        try:
            logger.info("svc-line", extra={"record_id": "r1"})
            logging.getLogger("app.t.file").info("mod-line")
            text = (tmp_path / "logs" / "t_stream_file.log").read_text(encoding="utf-8")
        finally:
            for handler in logger.handlers:
                handler.close()
            logger.handlers.clear()
        assert text.rstrip().endswith("svc-line | record_id=r1")
        assert "mod-line" not in text

    def test_installing_twice_keeps_one_handler(self, console) -> None:
        first = _tagged()
        L._install_console_handler()
        assert len(_tagged()) == 1
        assert _tagged() != first

    def test_a_foreign_root_handler_means_none_is_added(self, logging_state) -> None:
        foreign = logging.StreamHandler(io.StringIO())
        logging.getLogger().handlers[:] = [foreign]
        L._install_console_handler()
        assert logging.getLogger().handlers == [foreign]

    def test_import_writes_module_and_service_lines_to_stdout(self) -> None:
        out, err = _out(_run(_PRELUDE + "mod.info('mod-line'); svc.error('svc-line')"))
        lines = out.splitlines()
        assert re.fullmatch(r".* - app\.p1a\.mod - INFO - \[<string>:\d+\] - mod-line", lines[0])
        assert re.fullmatch(r".* - p1a_svc - ERROR - \[<string>:\d+\] - svc-line", lines[1])
        assert err == ""
        assert "\x1b[" not in out

    def test_reload_keeps_one_handler_and_one_line(self) -> None:
        out, _ = _out(_run(_PRELUDE + "import importlib; importlib.reload(L); importlib.reload(L); "
                           "print(len([h for h in logging.getLogger().handlers if getattr(h, '_pipeshub_console', 0)])); "
                           "logging.getLogger('app.r').info('after-reload')"))
        assert out.splitlines()[0] == "1"
        assert out.count("after-reload") == 1

    def test_a_spawned_child_prints_once(self, tmp_path) -> None:
        script = tmp_path / "spawn_child.py"
        script.write_text(textwrap.dedent("""
            import logging, multiprocessing

            def child():
                import app.utils.logger  # noqa: F401
                logging.getLogger("app.child").info("child-line")

            if __name__ == "__main__":
                import app.utils.logger  # noqa: F401
                p = multiprocessing.get_context("spawn").Process(target=child)
                p.start()
                p.join()
                assert p.exitcode == 0
        """))
        out, err = _out(_run("", path=script))
        assert out.count("child-line") == 1, out + err


# ---------------------------------------------------------------------------
# Text format: fields and colour
# ---------------------------------------------------------------------------


def _quoting_vectors() -> list[tuple[object, str | None]]:
    block = re.search(r"```json quoting-vectors\n(.*?)```", DOCS.read_text(encoding="utf-8"), re.S)
    assert block, f"no quoting-vectors block in {DOCS}"
    return [tuple(json.loads(line)) for line in block.group(1).strip().splitlines()]


class TestTextFields:
    def test_no_fields_is_todays_line(self) -> None:
        record = _record("hello %s", args=("x",))
        assert TextFormatter().format(record) == logging.Formatter(TEXT_FORMAT).format(record)

    @pytest.mark.parametrize(("value", "rendered"), _quoting_vectors())
    def test_shared_quoting_vectors(self, value: object, rendered: str | None) -> None:
        line = TextFormatter("%(message)s").format(_record(v=value))
        assert line == ("m" if rendered is None else f"m | v={rendered}")

    def test_fields_in_the_order_passed(self) -> None:
        logger = logging.getLogger("t.text.order")
        record = logger.makeRecord(logger.name, logging.INFO, "f.py", 1, "m", (), None,
                                   extra={"b": 2, "a": "x", "c": None, "d": True})
        assert TextFormatter("%(message)s").format(record) == "m | b=2 a=x d=true"

    def test_suffix_ends_the_message_before_the_traceback(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            record = _record("line1\nline2", exc_info=sys.exc_info(), a=1)
        lines = TextFormatter("%(message)s").format(record).splitlines()
        assert lines[:3] == ["line1", "line2 | a=1", "Traceback (most recent call last):"]

    def test_context_and_private_attributes_are_not_fields(self) -> None:
        token = set_context("req-t")
        try:
            record = _record(color_message="\x1b[1mx", _private=1)
        finally:
            reset_context(token)
        assert record.request_id
        assert TextFormatter("%(message)s").format(record) == "m"

    def test_fields_are_redacted(self) -> None:
        line = TextFormatter("%(message)s").format(_record(
            token="abc", token_count=12, url="https://u:pw@h.test/x?sig=s3cr3t", cfg={"apiKey": "k-123456789"},
        ))
        assert 'token="[REDACTED]"' in line
        assert "token_count=12" in line
        assert "pw@" not in line and "s3cr3t" not in line and "k-123456789" not in line

    def test_credential_named_numbers_and_bools_are_kept(self) -> None:
        assert L._record_fields(_record(token_count=12, secret_rotated=False, password=None)) == {
            "token_count": 12, "secret_rotated": False, "password": None,
        }


class TestColour:
    @pytest.mark.parametrize(
        ("level", "prefix"),
        [(logging.WARNING, TextFormatter.YELLOW), (logging.ERROR, TextFormatter.RED), (logging.CRITICAL, TextFormatter.RED)],
    )
    def test_colour_on_warning_and_up(self, level: int, prefix: str) -> None:
        out = TextFormatter("%(message)s", color=True).format(_record(level=level))
        assert out == f"{prefix}m{TextFormatter.RESET}"

    @pytest.mark.parametrize("level", [logging.DEBUG, logging.INFO])
    def test_no_colour_below_warning(self, level: int) -> None:
        assert TextFormatter("%(message)s", color=True).format(_record(level=level)) == "m"

    def test_no_colour_unless_asked(self) -> None:
        assert "\x1b[" not in TextFormatter("%(message)s").format(_record(level=logging.ERROR))

    def test_redacts_and_still_colours(self) -> None:
        out = TextFormatter("%(message)s", color=True).format(_record("token: SENTINEL-color", level=logging.ERROR))
        assert out.startswith(TextFormatter.RED) and "SENTINEL-color" not in out

    def test_isatty(self) -> None:
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        class _Closed(io.StringIO):
            def isatty(self) -> bool:
                raise ValueError("I/O operation on closed file")

        assert L._isatty(_Tty()) is True
        assert L._isatty(io.StringIO()) is False
        assert L._isatty(object()) is False
        assert L._isatty(_Closed()) is False

    def test_console_handler_colours_only_a_tty(self, logging_state, monkeypatch) -> None:
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        for stream, coloured in ((io.StringIO(), False), (_Tty(), True)):
            monkeypatch.setattr(sys, "stdout", stream)
            logging.getLogger().handlers[:] = []
            L._install_console_handler()
            logging.getLogger("app.t.colour").error("e")
            assert ("\x1b[31m" in stream.getvalue()) is coloured


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------


def _json(record: logging.LogRecord) -> dict:
    line = JsonFormatter().format(record)
    assert "\n" not in line
    return json.loads(line)


class TestJson:
    def test_standard_keys_in_order(self) -> None:
        out = _json(_record("hello %s", args=("x",), record_id="r1"))
        assert list(out) == ["timestamp", "level", "logger", "filename", "line", "message", "request_id", "record_id"]
        assert out["level"] == "info" and out["logger"] == "t.log" and out["filename"] == "f.py" and out["line"] == 7
        assert out["message"] == "hello x" and out["request_id"] is None and out["record_id"] == "r1"
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", out["timestamp"])

    @pytest.mark.parametrize(
        ("level", "name"),
        [(logging.DEBUG, "debug"), (logging.INFO, "info"), (logging.WARNING, "warning"), (logging.ERROR, "error"),
         (logging.CRITICAL, "critical")],
    )
    def test_level_names(self, level: int, name: str) -> None:
        assert _json(_record(level=level))["level"] == name

    def test_request_id_inside_a_context(self) -> None:
        token = set_context("req-json")
        try:
            record = _record()
        finally:
            reset_context(token)
        assert _json(record)["request_id"].startswith("req-json")

    def test_field_types(self) -> None:
        out = _json(_record(n=3, f=1.5, ok=True, none=None, obj=object(), items=[1, 2]))
        assert (out["n"], out["f"], out["ok"], out["none"]) == (3, 1.5, True, None)
        assert out["obj"].startswith("<object object") and out["items"] == "[1, 2]"

    def test_a_field_does_not_replace_a_standard_key(self) -> None:
        record = _record()
        record.__dict__.update({"level": "fake", "stack": "fake", "logger": "fake"})
        out = _json(record)
        assert out["level"] == "info" and out["logger"] == "t.log" and "stack" not in out

    def test_exception_and_stack_in_one_redacted_line(self) -> None:
        try:
            raise RuntimeError("Authorization: Bearer SENTINEL-exc")
        except RuntimeError:
            record = _record(exc_info=sys.exc_info())
        record.stack_info = "Stack (most recent call last):\n  frame"
        out = _json(record)
        assert "Traceback (most recent call last)" in out["stack"] and "Stack (most recent call last)" in out["stack"]
        assert "SENTINEL-exc" not in out["stack"]

    def test_message_and_fields_are_redacted(self) -> None:
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpSENTINELpem\n-----END RSA PRIVATE KEY-----"
        out = _json(_record(f"key {pem} password=hunter2", token="SENTINEL-tok", token_count=4, note="Bearer SENTINEL-b"))
        assert "SENTINEL" not in json.dumps(out) and "hunter2" not in out["message"]
        assert out["token"] == "[REDACTED]" and out["token_count"] == 4

    def test_unicode_is_kept(self) -> None:
        out = JsonFormatter().format(_record("caf\u00e9 \u2705", city="M\u00fcnchen"))
        assert "caf\u00e9 \u2705" in out and "M\u00fcnchen" in out

    def test_import_under_json_writes_only_json(self) -> None:
        out, _ = _out(_run(_PRELUDE + "mod.info('m1', extra={'record_id': 'r1'}); svc.warning('m2 password=hunter2')\n"
                           "try:\n    1/0\nexcept ZeroDivisionError:\n    mod.exception('m3')",
                           LOG_FORMAT="json"))
        lines = [json.loads(line) for line in out.splitlines()]
        assert [line["message"] for line in lines] == ["m1", "m2 password=[REDACTED]", "m3"]
        assert lines[0]["record_id"] == "r1" and lines[1]["level"] == "warning"
        assert "ZeroDivisionError" in lines[2]["stack"]

    def test_unicode_under_c_locale_has_no_logging_error(self) -> None:
        for fmt in ("text", "json"):
            out, err = _out(_run(_PRELUDE + "mod.info('caf\u00e9 \u2705', extra={'city': '\u00fc'})",
                                 LOG_FORMAT=fmt, LC_ALL="C", LANG="C"))
            assert "Logging error" not in err
            assert "caf\u00e9 \u2705" in out
            assert ('city="\u00fc"' if fmt == "text" else '"city": "\u00fc"') in out


# ---------------------------------------------------------------------------
# Format changes after import, provider, uvicorn
# ---------------------------------------------------------------------------


class TestJsonEdgeValues:
    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_stay_valid_json(self, value: float) -> None:
        def strict(constant: str) -> None:  # NaN/Infinity are not JSON; Python's loader would accept them
            raise ValueError(constant)

        out = json.loads(JsonFormatter().format(_record(ratio=value)), parse_constant=strict)
        assert out["ratio"] == str(value)

    def test_a_field_named_stack_is_reserved_in_text_and_json(self) -> None:
        record = _record(stack="s1", kept=1)
        assert "stack" not in json.loads(JsonFormatter().format(record))
        assert TextFormatter().format(record).endswith(" | kept=1")


class TestFormatAfterImport:
    def test_log_format_from_a_late_env_is_honoured(self, console, monkeypatch) -> None:
        create_logger("t_late_fmt").info("text-line")
        monkeypatch.setenv("LOG_FORMAT", "json")
        create_logger("t_late_fmt").info("json-line")
        lines = console.getvalue().splitlines()
        assert lines[0].endswith("text-line")
        assert json.loads(lines[1])["message"] == "json-line"

    def test_provider_is_offered_the_new_default(self, console, monkeypatch) -> None:
        provider = _Provider()
        monkeypatch.setattr("app.utils.logger.get_provider", lambda: provider)
        create_logger("t_offer_a")
        monkeypatch.setenv("LOG_FORMAT", "json")
        create_logger("t_offer_b")
        assert [type(f).__name__ for f in provider.defaults_seen] == ["TextFormatter", "JsonFormatter"]

    def test_a_plain_provider_formatter_still_redacts_under_json(self, console, monkeypatch) -> None:
        monkeypatch.setenv("LOG_FORMAT", "json")
        provider = _Provider(formatter=logging.Formatter("%(message)s"))
        monkeypatch.setattr("app.utils.logger.get_provider", lambda: provider)
        create_logger("t_plain_provider")
        logging.getLogger("app.t.plain").info("Authorization: Bearer SENTINEL-plain")
        assert type(provider.defaults_seen[-1]).__name__ == "JsonFormatter"
        assert "SENTINEL-plain" not in console.getvalue()

    def test_startup_sync_routes_the_provider_formatter_after_a_format_change(self, console, monkeypatch) -> None:
        provider = _Provider(formatter=L.RedactingFormatter("PROVIDER %(message)s"))
        monkeypatch.setattr("app.utils.logger.get_provider", lambda: provider)
        create_logger("t_route_late")
        monkeypatch.setenv("LOG_FORMAT", "json")
        L.install_log_redaction()  # lifespan: no create_logger call follows
        logging.getLogger("app.t.route").info("after-lifespan")
        assert console.getvalue().splitlines()[-1] == "PROVIDER after-lifespan"
        assert type(provider.defaults_seen[-1]).__name__ == "JsonFormatter"


class TestUvicorn:
    _CODE = _PRELUDE + """
import uvicorn
uvicorn.Config(app=lambda: None, log_level="info")
L.install_log_redaction()
logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", "/x?api_key=SENTINEL", "1.1", 404)
mod.info("after")
"""

    def test_json_mode_gives_uvicorn_json(self) -> None:
        out, _ = _out(_run(self._CODE, LOG_FORMAT="json"))
        lines = [json.loads(line) for line in out.splitlines()]
        assert [line["logger"] for line in lines] == ["uvicorn.access", "app.p1a.mod"]
        assert "SENTINEL" not in out

    def test_text_mode_keeps_uvicorns_access_line(self) -> None:
        out, _ = _out(_run(self._CODE))
        assert re.search(r'^INFO: +1\.2\.3\.4:5 - "GET /x\?api_key=\S+ HTTP/1\.1" 404 Not Found$', out, re.M)
        assert "SENTINEL" not in out


# ---------------------------------------------------------------------------
# Call site this PR changes
# ---------------------------------------------------------------------------


async def test_embedding_health_check_does_not_log_the_configs() -> None:
    from app.api.routes.health import initialize_embedding_model

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.Logger("t_health")
    logger.addHandler(handler)
    request = MagicMock()
    request.app.container.logger.return_value = logger
    request.app.container.retrieval_service = AsyncMock(side_effect=RuntimeError("stop"))
    configs = [{"provider": "openAI", "configuration": {"apiKey": "SENTINEL-key", "model": "m"}}]
    with pytest.raises(RuntimeError, match="stop"):
        await initialize_embedding_model(request, configs)
    line = TextFormatter("%(message)s").format(records[0])
    assert line == "Starting embedding health check | count=1"

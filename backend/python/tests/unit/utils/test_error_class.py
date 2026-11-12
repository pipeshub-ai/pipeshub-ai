"""classify_error, level_for and log_failure (app.utils.error_class)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import sys
from typing import TYPE_CHECKING

import httpx
import pytest

from app.agent_loop_lib.core.exceptions import RunCancelled, TransportError
from app.agents.mcp.errors import MCPHttpStatusError
from app.agents.mcp.oauth_client import MCPOAuthError
from app.connectors.core.base.token_service.oauth_service import OAuthTokenRequestError
from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.services.base_client import ServiceAuthRefusedError
from app.utils import error_class as EC
from app.utils.api_call import ApiCallError
from app.utils.llm import LLMNotConfiguredError
from app.utils.logger import JsonFormatter, TextFormatter, log_record_fields

if TYPE_CHECKING:
    from collections.abc import Iterator


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://upstream.example/x")
    return httpx.HTTPStatusError("upstream", request=request, response=httpx.Response(status, request=request))


def _chained(outer: BaseException, cause: BaseException, *, explicit: bool = True) -> BaseException:
    try:
        try:
            raise cause
        except BaseException as inner:
            if explicit:
                raise outer from inner
            raise outer  # noqa: B904 - the implicit __context__ chain is what this builds
    except BaseException as e:
        return e


class TestStatus:
    @pytest.mark.parametrize(("status", "cls"), [
        (400, "bad_request"), (401, "auth_expired"), (403, "forbidden"), (404, "not_found"), (408, "timeout"),
        (409, "conflict"), (410, "not_found"), (422, "bad_request"), (429, "rate_limited"), (499, "bad_request"),
        (500, "upstream_5xx"), (503, "unavailable"), (504, "timeout"), (599, "upstream_5xx"),
        (200, "internal"), (399, "internal"), (True, "internal"),
    ])
    def test_table(self, status: int, cls: str) -> None:
        assert EC.classify_error(status) == cls

    @pytest.mark.parametrize(("exc", "cls"), [
        (_http_status_error(429), "rate_limited"),
        (ApiCallError("x", status_code=404), "not_found"),
        (MCPHttpStatusError(401), "auth_expired"),
        (MCPHttpStatusError(403), "forbidden"),
        (TransportError("x", status_code=429), "rate_limited"),
        (TransportError("x", status_code=502), "upstream_5xx"),
    ])
    def test_status_carrying_exceptions(self, exc: BaseException, cls: str) -> None:
        assert EC.classify_error(exc) == cls


class TestChain:
    def test_cause_decides_for_a_plain_wrapper(self) -> None:
        assert EC.classify_error(_chained(RuntimeError("sync failed"), _http_status_error(429))) == "rate_limited"

    def test_implicit_context_decides_too(self) -> None:
        assert EC.classify_error(_chained(RuntimeError("x"), TimeoutError(), explicit=False)) == "timeout"

    def test_our_bug_while_handling_a_timeout_is_internal(self) -> None:
        assert EC.classify_error(_chained(KeyError("k"), TimeoutError(), explicit=False)) == "internal"

    def test_from_none_hides_the_context(self) -> None:
        try:
            try:
                raise TimeoutError()
            except TimeoutError:
                raise RuntimeError("hidden") from None
        except RuntimeError as e:
            assert EC.classify_error(e) == "internal"

    def test_cyclic_chain_terminates(self) -> None:
        a, b = RuntimeError("a"), RuntimeError("b")
        a.__cause__, b.__cause__ = b, a
        assert EC.classify_error(a) == "internal"

    def test_our_http_exception_500_is_internal(self) -> None:
        from fastapi import HTTPException

        assert EC.classify_error(_chained(HTTPException(500), KeyError("k"), explicit=False)) == "internal"
        assert EC.classify_error(_chained(HTTPException(500), _http_status_error(502))) == "upstream_5xx"
        assert EC.classify_error(HTTPException(404)) == "not_found"


class TestTypes:
    @pytest.mark.parametrize(("exc", "cls"), [
        (asyncio.CancelledError(), "cancelled"),
        (concurrent.futures.CancelledError(), "cancelled"),
        (RunCancelled(), "cancelled"),
        (TimeoutError(), "timeout"),
        (httpx.ReadTimeout("t"), "timeout"),
        (ConnectionRefusedError(), "unavailable"),
        (httpx.ConnectError("c"), "unavailable"),
        (json.JSONDecodeError("m", "d", 0), "bad_request"),
        (DocumentProcessingError("bad pdf"), "bad_request"),
        (ValueError("x"), "internal"),
        (ImportError("x"), "internal"),
        (ServiceAuthRefusedError("refused"), "misconfigured"),
        (LLMNotConfiguredError(), "misconfigured"),
        (TransportError("x", retryable=True), "unavailable"),
        (TransportError("x", retryable=False), "internal"),
    ])
    def test_classes(self, exc: BaseException, cls: str) -> None:
        assert EC.classify_error(exc) == cls

    def test_transport_error_from_a_timeout(self) -> None:
        assert EC.classify_error(_chained(TransportError("x", retryable=True), httpx.ReadTimeout("t"))) == "timeout"

    def test_pydantic_validation_error_is_ours(self) -> None:
        from pydantic import BaseModel, ValidationError

        class M(BaseModel):
            n: int

        with pytest.raises(ValidationError) as info:
            M(n="x")
        assert EC.classify_error(info.value) == "internal"

    def test_provider_sdk_errors(self) -> None:
        import openai

        request = httpx.Request("POST", "https://api.example/v1")
        def resp(status: int) -> httpx.Response:
            return httpx.Response(status, request=request)
        assert EC.classify_error(openai.AuthenticationError("k", response=resp(401), body=None)) == "misconfigured"
        assert EC.classify_error(openai.PermissionDeniedError("k", response=resp(403), body=None)) == "misconfigured"
        assert EC.classify_error(openai.RateLimitError("r", response=resp(429), body=None)) == "rate_limited"
        assert EC.classify_error(openai.InternalServerError("s", response=resp(500), body=None)) == "upstream_5xx"
        assert EC.classify_error(openai.APITimeoutError(request=request)) == "timeout"
        assert EC.classify_error(openai.APIConnectionError(request=request)) == "unavailable"

    def test_anthropic_auth_is_misconfigured_when_loaded(self) -> None:
        anthropic = pytest.importorskip("anthropic")
        request = httpx.Request("POST", "https://api.example/v1")
        err = anthropic.AuthenticationError("k", response=httpx.Response(401, request=request), body=None)
        assert EC.classify_error(err) == "misconfigured"

    def test_unloaded_sdk_is_not_imported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delitem(sys.modules, "anthropic", raising=False)
        assert EC.classify_error(RuntimeError("x")) == "internal"
        assert "anthropic" not in sys.modules

    def test_status_property_that_raises_is_internal(self) -> None:
        class Odd(Exception):
            @property
            def status_code(self) -> int:
                raise RuntimeError("no")

        assert EC.classify_error(Odd()) == "internal"


class TestOAuth:
    @pytest.mark.parametrize(("status", "code", "cls"), [
        (400, "invalid_grant", "auth_expired"),
        (401, "invalid_client", "misconfigured"),
        (400, "unauthorized_client", "misconfigured"),
        (503, "invalid_grant", "unavailable"),
        (429, "invalid_grant", "rate_limited"),
    ])
    def test_codes_before_status(self, status: int, code: str, cls: str) -> None:
        assert EC.classify_error(OAuthTokenRequestError("refused", status=status, error_code=code)) == cls

    def test_refresh_token_invalid_chain(self) -> None:
        from app.connectors.core.base.token_service.oauth_service import (
            RefreshTokenInvalidError,
        )

        coded = OAuthTokenRequestError("refused", status=400, error_code="invalid_grant")
        assert EC.classify_error(_chained(RefreshTokenInvalidError("gone"), coded)) == "auth_expired"

    def test_mcp_oauth_error(self) -> None:
        assert EC.classify_error(MCPOAuthError("x", error_code="invalid_grant")) == "auth_expired"
        assert EC.classify_error(MCPOAuthError("x", error_code="invalid_client")) == "misconfigured"

    def test_text_markers_need_an_error_code(self) -> None:
        assert EC.classify_error(ValueError("invalid_grant")) == "internal"


def test_level_policy() -> None:
    assert {c: EC.level_for(c) for c in EC.ERROR_CLASSES} == {
        "internal": logging.ERROR, "misconfigured": logging.ERROR, "cancelled": logging.INFO,
        **dict.fromkeys(("auth_expired", "forbidden", "not_found", "rate_limited", "timeout",
                         "upstream_5xx", "bad_request", "conflict", "unavailable"), logging.WARNING),
    }
    assert EC.level_for("nonsense") == logging.ERROR


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, list[logging.LogRecord]]]:
    logger = logging.getLogger("test.error_class")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler.records
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


def _raised(exc: BaseException) -> BaseException:
    try:
        raise exc
    except BaseException as e:
        return e


class TestLoggedLevel:
    def test_never_logged(self) -> None:
        assert EC.logged_level(_raised(KeyError("k"))) is None

    def test_the_level_it_was_logged_at(self, capture) -> None:
        logger, _ = capture
        exc = _raised(_http_status_error(429))
        EC.log_failure(logger, "x.end", exc)
        assert EC.logged_level(exc) == logging.WARNING

    def test_the_highest_over_the_cause_chain(self, capture) -> None:
        logger, _ = capture
        inner = _raised(_http_status_error(429))
        EC.log_failure(logger, "inner.end", inner)
        outer = _chained(RuntimeError("outer"), inner)
        assert EC.logged_level(outer) == logging.WARNING  # seen through the cause
        EC.log_failure(logger, "outer.end", outer, level=logging.ERROR)
        assert EC.logged_level(outer) == logging.ERROR
        assert EC.logged_level(inner) == logging.WARNING


class TestLogFailure:
    def test_fields_level_and_no_stack_at_warn(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "llm_call.end", _raised(_http_status_error(429)), attempt=3)
        (record,) = records
        assert record.levelno == logging.WARNING and record.exc_info is None and record.exc_text is None
        assert record.getMessage() == "llm_call.end" and record.event_name == "llm_call.end"
        fields = log_record_fields(record)
        assert fields["error_class"] == "rate_limited" and fields["error_type"] == "HTTPStatusError"
        assert fields["outcome"] == "failure" and fields["attempt"] == 3
        assert fields["error_message"].startswith("HTTPStatusError: upstream")
        assert record.filename == "test_error_class.py"

    def test_internal_is_error_with_one_stack(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "x.end", _raised(KeyError("k")))
        (record,) = records
        assert record.levelno == logging.ERROR and record.exc_info is not None
        assert "Traceback" in record.exc_text and "KeyError" in record.exc_text
        assert "Traceback" in TextFormatter().format(record)
        assert "KeyError" in json.loads(JsonFormatter().format(record))["stack"]

    def test_exc_info_kept_for_formatters_that_read_only_it(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "x.end", _raised(KeyError("k")))
        assert records[0].exc_info[0] is KeyError
        assert "KeyError" in logging.Formatter().formatException(records[0].exc_info)

    def test_explicit_class_level_and_outcome_win(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "x.end", _raised(_http_status_error(401)), error_class="misconfigured", outcome="partial")
        EC.log_failure(logger, "y.end", _raised(TimeoutError()), level=logging.ERROR)
        first, second = records
        assert first.levelno == logging.ERROR and log_record_fields(first)["error_class"] == "misconfigured"
        assert log_record_fields(first)["outcome"] == "partial"
        assert second.levelno == logging.ERROR and second.exc_info is not None

    def test_cancelled_is_info_without_stack(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "x.end", _raised(asyncio.CancelledError()))
        assert records[0].levelno == logging.INFO and records[0].exc_info is None

    def test_same_exception_again_is_debug(self, capture) -> None:
        logger, records = capture
        exc = _raised(KeyError("k"))
        EC.log_failure(logger, "inner", exc)
        EC.log_failure(logger, "outer", exc)
        assert [(r.levelno, r.exc_info is not None) for r in records] == [(logging.ERROR, True), (logging.DEBUG, False)]

    def test_raised_from_a_logged_one_is_debug(self, capture) -> None:
        logger, records = capture
        inner = _raised(KeyError("k"))
        EC.log_failure(logger, "inner", inner)
        EC.log_failure(logger, "outer", _chained(RuntimeError("wrap"), inner))
        assert records[1].levelno == logging.DEBUG

    def test_context_only_is_a_new_failure(self, capture) -> None:
        logger, records = capture
        inner = _raised(KeyError("k"))
        EC.log_failure(logger, "inner", inner)
        outer = _chained(AttributeError("cleanup"), inner, explicit=False)
        EC.log_failure(logger, "outer", outer)
        assert records[1].levelno == logging.ERROR and records[1].exc_info is not None

    def test_inner_warn_then_outer_error_prints_the_stack_once(self, capture) -> None:
        logger, records = capture
        exc = _raised(TimeoutError())
        EC.log_failure(logger, "inner", exc)
        EC.log_failure(logger, "outer", exc, level=logging.ERROR)
        EC.log_failure(logger, "outermost", exc, level=logging.CRITICAL)
        assert [(r.levelno, r.exc_info is not None) for r in records] == [
            (logging.WARNING, False), (logging.ERROR, True), (logging.CRITICAL, False)]

    def test_inner_debug_then_outer_error(self, capture) -> None:
        logger, records = capture
        exc = _raised(KeyError("k"))
        EC.log_failure(logger, "inner", exc, level=logging.DEBUG)
        EC.log_failure(logger, "outer", exc)
        assert [(r.levelno, r.exc_info is not None) for r in records] == [(logging.DEBUG, False), (logging.ERROR, True)]

    def test_disabled_inner_line_does_not_demote(self, capture) -> None:
        logger, records = capture
        quiet = logging.getLogger("test.error_class.quiet")
        quiet.setLevel(logging.CRITICAL + 1)
        exc = _raised(KeyError("k"))
        EC.log_failure(quiet, "inner", exc)
        EC.log_failure(logger, "outer", exc)
        assert [(r.levelno, r.exc_info is not None) for r in records] == [(logging.ERROR, True)]
        quiet.setLevel(logging.NOTSET)

    def test_exception_refusing_attributes_still_logs(self, capture) -> None:
        logger, records = capture

        class Frozen(Exception):
            def __setattr__(self, name: str, value: object) -> None:
                raise AttributeError(name)

        EC.log_failure(logger, "x.end", _raised(Frozen("f")))
        assert len(records) == 1

    def test_classifier_failure_still_logs_one_line(self, capture, monkeypatch: pytest.MonkeyPatch) -> None:
        logger, records = capture
        monkeypatch.setattr(EC, "classify_error", lambda exc: 1 / 0)
        EC.log_failure(logger, "x.end", _raised(KeyError("k")))
        assert len(records) == 1 and records[0].getMessage() == "x.end"

    def test_fallback_line_carries_no_exception_text(self, capture) -> None:
        logger, records = capture

        class RaisesOnce(logging.Filter):
            fired = False

            def filter(self, record: logging.LogRecord) -> bool:
                if not RaisesOnce.fired:
                    RaisesOnce.fired = True
                    raise RuntimeError("filter broke")
                return True

        flt = RaisesOnce()
        logger.addFilter(flt)
        try:
            exc = _raised(RuntimeError("tool said secret-arg see https://x.example/a?sig=abc"))
            EC.log_failure(logger, "tool_call.end", exc, include_error_text=False)
        finally:
            logger.removeFilter(flt)
        (record,) = records
        out = TextFormatter().format(record)
        assert record.exc_info is None and "secret-arg" not in out and "x.example" not in out
        assert record.getMessage() == "tool_call.end" and log_record_fields(record) == {"error_type": "RuntimeError"}

    def test_outcome_none_still_defaults_to_failure(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "x.end", _raised(TimeoutError()), outcome=None)
        assert log_record_fields(records[0])["outcome"] == "failure"

    def test_text_is_redacted(self, capture) -> None:
        logger, records = capture
        msg = "call https://gen.example/v1?key=AIzaSyA1234567890abcdefghijklmnopqrstuv in org-AbCd1234"
        EC.log_failure(logger, "x.end", _raised(KeyError(msg)))
        (record,) = records
        error_message = log_record_fields(record)["error_message"]
        assert "AIza" not in error_message and "org-AbCd1234" not in error_message and "[url]" in error_message
        assert "AIza" not in record.exc_text and "[url]" in record.exc_text
        assert "test_error_class.py" in record.exc_text  # file names survive the stack redaction

    def test_error_message_honours_from_none_and_is_capped(self, capture) -> None:
        logger, records = capture
        try:
            try:
                raise TimeoutError("hidden-cause")
            except TimeoutError:
                raise RuntimeError("x" * 500) from None
        except RuntimeError as e:
            EC.log_failure(logger, "x.end", e)
        error_message = log_record_fields(records[0])["error_message"]
        assert "hidden-cause" not in error_message and len(error_message) == 300

    def test_without_error_text(self, capture) -> None:
        logger, records = capture
        exc = _chained(RuntimeError("server said secret-arg"), KeyError("secret-arg"))
        EC.log_failure(logger, "tool_call.end", exc, include_error_text=False)
        (record,) = records
        fields = log_record_fields(record)
        assert "error_message" not in fields and fields["error_type"] == "RuntimeError"
        # No exc_info: a handler or formatter that formats it itself would print the raw message.
        assert record.levelno == logging.ERROR and record.exc_info is None
        for line in (TextFormatter().format(record), logging.Formatter().format(record), JsonFormatter().format(record)):
            assert "secret-arg" not in line and "<message omitted>" in line
        assert "secret-arg" not in record.exc_text
        assert "RuntimeError: <message omitted>" in record.exc_text and "KeyError: <message omitted>" in record.exc_text
        assert "direct cause" in record.exc_text and "test_error_class.py" in record.exc_text


class TestMarkError:
    """A database client stamps its class and "text can quote sent values" on the exception (docs/logging.md, Database queries)."""

    SECRET = "alice@example.com"

    def test_stamp_beats_the_status_and_unstamped_is_unchanged(self) -> None:
        from app.services.graph_db.arango.arango_http_client import ArangoQueryError

        killed = ArangoQueryError("Query failed", 410, '{"errorNum":1500}')
        assert EC.classify_error(killed) == "not_found"  # positive control: status table as before
        EC.mark_error(killed, "timeout")
        assert EC.classify_error(killed) == "timeout"
        assert EC.classify_error(Exception("plain")) == "internal"

    def test_class_attribute_counts_and_is_not_an_instance_stamp(self) -> None:
        class Declared(Exception):
            log_error_class = "timeout"

        exc = Declared("x")
        assert EC.classify_error(exc) == "timeout" and not EC.is_stamped(exc)
        EC.mark_error(exc, "conflict")
        assert EC.classify_error(exc) == "conflict" and EC.is_stamped(exc)

    def test_first_stamp_wins(self) -> None:
        exc = Exception("x")
        EC.mark_error(exc, "conflict", hide_text=True)
        EC.mark_error(exc, "internal")
        assert EC.classify_error(exc) == "conflict" and exc.log_hide_text is True

    def test_hide_text_alone_keeps_the_normal_class(self, capture) -> None:
        logger, records = capture
        exc = _raised(_http_status_error(429))
        EC.mark_error(exc, hide_text=True)
        assert set(vars(exc)) >= {"log_hide_text"} and "log_error_class" not in vars(exc) and not EC.is_stamped(exc)
        assert EC.classify_error(exc) == "rate_limited"  # classified as it would be unstamped
        EC.log_failure(logger, "x.end", exc)
        (record,) = records
        assert getattr(record, "error_message", None) is None and record.error_class == "rate_limited"

    def test_a_class_given_later_still_wins_over_classification(self) -> None:
        exc = Exception("plain")
        EC.mark_error(exc, hide_text=True)
        EC.mark_error(exc, "conflict")
        assert EC.classify_error(exc) == "conflict" and exc.log_hide_text is True

    def test_first_class_wins_and_a_later_hide_is_added(self) -> None:
        exc = Exception("x")
        EC.mark_error(exc, "conflict")
        EC.mark_error(exc, "internal", hide_text=True)
        assert EC.classify_error(exc) == "conflict" and exc.log_hide_text is True

    def test_sets_exactly_the_two_attributes(self) -> None:
        exc = Exception("x")
        EC.mark_error(exc, "conflict", hide_text=True)
        assert set(vars(exc)) == {"log_error_class", "log_hide_text"}
        plain = Exception("y")
        EC.mark_error(plain, "conflict")
        assert set(vars(plain)) == {"log_error_class"}

    def test_unstampable_exception_keeps_todays_behaviour(self) -> None:
        class Frozen(Exception):
            def __setattr__(self, name: str, value: object) -> None:
                raise AttributeError(name)

        exc = Frozen("x")
        EC.mark_error(exc, "timeout", hide_text=True)  # never raises
        assert EC.classify_error(exc) == "internal" and not EC.is_stamped(exc)

    def test_stamped_cause_behind_an_unstamped_wrapper(self) -> None:
        from app.exceptions.graph_db_exceptions import GraphQueryError

        inner = Exception("driver")
        EC.mark_error(inner, "conflict")
        assert EC.classify_error(_chained(GraphQueryError("lookup failed"), inner)) == "conflict"

    def test_hidden_text_anywhere_in_the_chain(self, capture) -> None:
        logger, records = capture
        inner = _raised(KeyError(f"value {self.SECRET}"))
        EC.mark_error(inner, "internal", hide_text=True)
        outer = _raised(_chained(RuntimeError(f"wrapped: {inner}"), inner))
        EC.log_failure(logger, "x.end", outer)
        (record,) = records
        assert getattr(record, "error_message", None) is None
        assert "<message omitted>" in record.exc_text and self.SECRET not in record.exc_text and record.exc_info is None

    def test_positive_control_unstamped_chain_keeps_its_text(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "x.end", _raised(KeyError(f"value {self.SECRET}")))
        (record,) = records
        assert self.SECRET in record.error_message

    def test_group_member_stamped_hides_text(self, capture) -> None:
        logger, records = capture
        leak = _raised(ValueError(f"value {self.SECRET}"))
        EC.mark_error(leak, "bad_request", hide_text=True)
        group = _raised(ExceptionGroup("reads", [ValueError("first, clean"), _chained(RuntimeError(f"outer {leak}"), leak)]))
        EC.log_failure(logger, "x.end", group, level=logging.ERROR)
        (record,) = records
        assert getattr(record, "error_message", None) is None and self.SECRET not in (record.exc_text or "")

    def test_group_without_a_stamp_keeps_its_text(self, capture) -> None:
        logger, records = capture
        EC.log_failure(logger, "x.end", _raised(ExceptionGroup("reads", [ValueError("v1")])))
        (record,) = records
        assert "reads" in record.error_message

    def test_stamping_does_not_change_the_push_retry_decision(self) -> None:
        from app.connectors.sources.localKB.push.retry import is_attempt_lost

        mixed = Exception("Batch insert failed with 2 error(s): Item 0: [1210] dup; Item 1: [1200] timeout waiting to lock key")
        assert is_attempt_lost(mixed)
        EC.mark_error(mixed, "conflict", hide_text=True)
        assert is_attempt_lost(mixed)


def test_on_chain_follows_cause_and_unsuppressed_context_like_the_text() -> None:
    target = ValueError("marked")
    try:
        raise target
    except ValueError:
        try:
            raise RuntimeError("implicit")
        except RuntimeError as implicit:
            caught = implicit
    explicit = RuntimeError("explicit")
    explicit.__cause__ = target
    suppressed = RuntimeError("from None")
    suppressed.__context__, suppressed.__suppress_context__ = target, True

    assert EC.on_chain(target, target)
    assert EC.on_chain(caught, target)
    assert EC.on_chain(explicit, target)
    assert not EC.on_chain(suppressed, target)
    assert not EC.on_chain(RuntimeError("unrelated"), target)


class TestAlreadyLogged:
    def test_never_logged(self) -> None:
        assert EC.already_logged(_raised(KeyError("k")), logging.DEBUG) is False

    def test_at_or_below_the_level_it_was_logged_at(self, capture) -> None:
        logger, _ = capture
        exc = _raised(_http_status_error(429))
        EC.log_failure(logger, "x.end", exc)  # WARNING
        assert (EC.already_logged(exc, logging.INFO), EC.already_logged(exc, logging.WARNING)) == (True, True)
        assert EC.already_logged(exc, logging.ERROR) is False

    def test_through_the_cause(self, capture) -> None:
        logger, _ = capture
        inner = _raised(_http_status_error(429))
        EC.log_failure(logger, "inner.end", inner)
        assert EC.already_logged(_chained(RuntimeError("outer"), inner), logging.WARNING) is True


class TestChainHas:
    def test_a_type_anywhere_in_the_chain(self) -> None:
        outer = _chained(RuntimeError("outer"), _chained(ValueError("middle"), OSError("inner")))
        assert EC.chain_has(outer, OSError) is True
        assert EC.chain_has(outer, KeyError, ValueError) is True
        assert EC.chain_has(outer, KeyError) is False

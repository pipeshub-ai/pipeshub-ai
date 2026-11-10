"""A source refusing an item is an expected outcome: logged once at warning, its reason stored.

The stream route marks the answers it builds from the source's own status
(``SOURCE_REFUSAL_HEADER``); the indexer stores their detail as the record's
reason and logs them without a traceback. Anything else is still an error.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.connectors.core.base.error.stream_errors import (
    SOURCE_REFUSAL_HEADER,
    map_source_status,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
    is_source_refusal,
)
from app.services.messaging.kafka.handlers import record as record_module
from app.utils import user_errors
from app.utils.api_call import ApiCallError, _source_reason
from tests.unit.services.messaging.test_record_handler import (
    _collect_events,
    _make_handler,
)

WEB_400 = (
    "The page returned an error (400 Bad Request). Check the URL is correct and publicly "
    "reachable, then sync again."
)


def _refusal(status: int = 422, reason: str = WEB_400) -> ApiCallError:
    return ApiCallError(f"API call failed with status {status}", status_code=status, source_reason=reason)


def _wrapped(inner: Exception) -> Exception:
    """As the handler re-raises a download failure (``raise Exception(...) from e``)."""
    try:
        raise Exception(str(inner)) from inner
    except Exception as wrapped:
        return wrapped


class TestSourceReasonFromResponse:
    def test_marked_answer_carries_its_detail(self) -> None:
        assert _source_reason({SOURCE_REFUSAL_HEADER: "1"}, '{"detail": "%s"}' % WEB_400) == WEB_400

    def test_unmarked_answer_carries_none(self) -> None:
        # The route's own 404s ("Record not found", ids) are not written for people.
        assert _source_reason({}, '{"detail": "Record not found"}') is None

    def test_marked_answer_without_json_detail_carries_none(self) -> None:
        assert _source_reason({SOURCE_REFUSAL_HEADER: "1"}, "<html>oops</html>") is None


class TestMapSourceStatusMarksRefusals:
    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429])
    def test_every_refusal_is_marked(self, status: int) -> None:
        assert map_source_status(status, connector="Drive").headers[SOURCE_REFUSAL_HEADER] == "1"

    @pytest.mark.parametrize("status", [408, 500, 503, 504])
    def test_an_outage_at_the_source_is_not_marked(self, status: int) -> None:
        assert not (map_source_status(status, connector="Drive").headers or {})

    @pytest.mark.parametrize("status", [None, "400", 200, 302, 999])
    def test_no_usable_status_is_not_marked(self, status: object) -> None:
        assert not (map_source_status(status, connector="Drive").headers or {})  # type: ignore[arg-type]

    def test_other_connectors_keep_their_messages(self) -> None:
        exc = map_source_status(400, connector="Drive")
        assert exc.status_code == 422
        assert exc.detail == "This item cannot be downloaded from Drive in its current format."

    def test_a_reason_replaces_the_generic_message_but_not_the_status(self) -> None:
        exc = map_source_status(400, connector="Robots.txt", reason=WEB_400)
        assert (exc.status_code, exc.detail) == (422, WEB_400)

    def test_retry_after_is_kept_alongside_the_mark(self) -> None:
        exc = map_source_status(429, connector="Drive", retry_after="7")
        assert exc.headers == {SOURCE_REFUSAL_HEADER: "1", "Retry-After": "7"}


class TestClassification:
    def test_a_wrapped_refusal_is_a_refusal(self) -> None:
        assert is_source_refusal(_wrapped(_refusal()))

    def test_a_refusal_re_raised_from_another_failure_is_not(self) -> None:
        try:
            raise _wrapped(_refusal()) from RuntimeError("status write failed")
        except Exception as mixed:
            assert not is_source_refusal(mixed)

    def test_an_unmarked_api_error_is_not(self) -> None:
        assert not is_source_refusal(_wrapped(ApiCallError("boom", status_code=422)))
        assert not is_source_refusal(RuntimeError("boom"))
        assert not is_source_refusal(None)

    def test_a_refused_item_is_still_failed_on_its_first_attempt(self) -> None:
        assert MessageErrorClassifier.classify_by_exception(_wrapped(_refusal())) == MessageErrorType.TERMINAL

    def test_the_stored_reason_is_the_sources(self) -> None:
        assert user_errors.to_user_reason(_wrapped(_refusal())) == WEB_400

    def test_an_unmarked_failure_keeps_the_generic_reason(self) -> None:
        exc = _wrapped(ApiCallError("API call failed with status 422", status_code=422))
        assert user_errors.to_user_reason(exc) == user_errors.GENERIC_FAILURE


class TestHandlerLogging:
    async def _fail_download(self, error: Exception) -> tuple[MagicMock, dict | None]:
        logger = MagicMock()
        handler = _make_handler(logger=logger)
        gp = handler.event_processor.graph_provider
        record = {"_key": "r1", "virtualRecordId": "vr1", "origin": "CONNECTOR", "recordType": "FILE",
                  "indexingStatus": ProgressStatus.NOT_STARTED.value, "mimeType": "text/html"}
        gp.get_document = AsyncMock(return_value=record)
        gp.update_node = AsyncMock(return_value=True)
        handler._propagate_primary_failure_to_queued_duplicates = AsyncMock()
        handler._trigger_next_queued_duplicate = AsyncMock()
        payload = {"recordId": "r1", "virtualRecordId": "vr1", "orgId": "org-1",
                   "mimeType": "text/html", "extension": "html", "is_final_failure": False}
        handler.config_service.get_config = AsyncMock(return_value={"connectors": {"endpoint": "http://c"}})
        with patch.object(record_module, "generate_jwt", new_callable=AsyncMock, return_value="t"), \
             patch.object(record_module, "make_api_call", new_callable=AsyncMock, side_effect=error):
            with pytest.raises(Exception):  # noqa: B017 - the handler re-raises for the consumer
                await _collect_events(handler, EventTypes.NEW_RECORD.value, payload)
        updates = gp.update_node.call_args.args[2] if gp.update_node.await_count else None
        return logger, updates

    @pytest.mark.asyncio
    async def test_a_refusal_logs_one_warning_without_a_traceback(self) -> None:
        logger, updates = await self._fail_download(_refusal())
        logger.error.assert_not_called()
        warnings = [c for c in logger.warning.call_args_list if "r1" in str(c.args[0])]
        assert len(warnings) == 1
        assert "exc_info" not in warnings[0].kwargs
        assert updates["indexingStatus"] == ProgressStatus.FAILED.value
        assert updates["reason"] == WEB_400

    @pytest.mark.asyncio
    async def test_an_unexpected_failure_still_logs_an_error_with_its_traceback(self) -> None:
        boom = ApiCallError("API call failed with status 422", status_code=422)
        logger, _ = await self._fail_download(boom)
        errors = [c for c in logger.error.call_args_list if "Final failure" in str(c.args[0])]
        assert len(errors) == 1
        assert errors[0].kwargs["exc_info"] is not None

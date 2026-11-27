"""A source failure while a connector starts (toggle on, cold filter options, cold streams) or lists filter options
answers what the stream routes answer for it, instead of 400 "verify your credentials" or a generic 500.

Expected answers are the stream mapping's, written out by hand (not computed with it):
  source 401, dead refresh token  -> 409 "... Reconnect it ..."   (never a 401: that is about the caller's own session)
  source 429 (+ Retry-After)      -> 429 + the same Retry-After
  source 5xx, unreachable host    -> 502 "Could not reach <connector>"
  source timeout                  -> 504
  source 403                      -> 403 about the account the connector uses (409 on the indexer's cold stream)
Item-worded answers (404, 422) and unclassified failures keep the route's generic 500.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import httpx
import pytest
import tenacity
from fastapi import HTTPException
from google.auth.exceptions import RefreshError

from app.config.constants.arangodb import Connectors
from app.connectors.api import router as routes
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.base.token_service.oauth_service import (
    RefreshTokenInvalidError,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
    is_source_refusal,
)
from app.sources.client.http.address_guard import AddressNotAllowedError
from app.utils.api_call import ApiCallError, _source_reason

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.asyncio

LOG = logging.getLogger("test-router-vendor-errors")
CID = "c1"
NAME = "Acme Wiki"
GENERIC_500 = "We couldn't connect to this connector. Please try again; if it keeps failing, contact your admin."
OPTIONS_500 = "We couldn't load the options for this filter"
ACCOUNT_FORBIDDEN = (
    f"Access was denied by {NAME}. The account PipesHub uses for this connector may not have the permissions it needs."
)
RECONNECT = "Reconnect it"


def _httpx_status(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://vendor.example/api")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def _unreachable() -> aiohttp.ClientConnectorError:
    key = MagicMock(host="vendor.example", port=443, ssl=True)
    return aiohttp.ClientConnectorError(key, OSError(111, "Connection refused"))


def _retry_error(inner: BaseException) -> tenacity.RetryError:
    future = tenacity.Future(1)
    future.set_exception(inner)
    return tenacity.RetryError(future)


# (id, exception, expected status, text the detail must contain, expected Retry-After)
VENDOR_FAILURES = [
    ("vendor-401", _httpx_status(401), 409, RECONNECT, None),
    ("api-call-401", ApiCallError("401 Unauthorized", status_code=401), 409, RECONNECT, None),
    ("refresh-token-dead", RefreshTokenInvalidError("invalid_grant"), 409, RECONNECT, None),
    ("google-refresh-error", RefreshError("invalid_grant: Token has been expired or revoked."), 409, RECONNECT, None),
    ("vendor-429", _httpx_status(429, {"Retry-After": "30"}), 429, "Too many requests", "30"),
    ("vendor-503", _httpx_status(503), 502, "Could not reach", None),
    ("unreachable", _unreachable(), 502, "Could not reach", None),
    ("timeout", asyncio.TimeoutError(), 504, "timed out", None),
    ("vendor-403", _httpx_status(403), 403, ACCOUNT_FORBIDDEN, None),
    # make_api_call parses the source's Retry-After into seconds and keeps no response headers.
    ("api-call-429", ApiCallError("429", status_code=429, retry_after_seconds=30.0), 429, "Too many requests", "30"),
]
IDS = [v[0] for v in VENDOR_FAILURES]
# A ConnectorInitError raised `from` one of these keeps its own words with the outage's status.
OUTAGE_CAUSES = (
    ("cause", "status", "retry_after"),
    [
        (_httpx_status(429, {"Retry-After": "12"}), 429, "12"),
        (_httpx_status(503), 502, None),
        (_unreachable(), 502, None),
        (asyncio.TimeoutError(), 504, None),
    ],
)
OUTAGE_IDS = ["429", "503", "unreachable", "timeout"]
Outcome = tuple[int, Any, dict[str, str]]


async def _outcome(awaitable: Any) -> Outcome:  # noqa: ANN401
    try:
        return 200, await awaitable, {}
    except HTTPException as exc:
        return exc.status_code, exc.detail, dict(exc.headers or {})


def _assert_answer(got: Outcome, status: int, text: str, retry_after: str | None) -> None:
    code, detail, headers = got
    assert code != 401, "a source 401 must never reach the caller as a 401"
    assert (code, text in str(detail), headers.get("Retry-After")) == (status, True, retry_after), (code, detail, headers)
    assert "this item" not in str(detail), "no item was asked for"


# --- the shared build behind toggle on, cold filter options and cold streams -------------------------------------


def _fake_connector(*, init: Any = True, test: Any = True) -> MagicMock:  # noqa: ANN401
    connector = MagicMock()
    connector.display_name = NAME
    connector.init = AsyncMock(side_effect=init) if isinstance(init, BaseException) else AsyncMock(return_value=init)
    connector.test_connection_and_access = (
        AsyncMock(side_effect=test) if isinstance(test, BaseException) else AsyncMock(return_value=test)
    )
    connector.last_action_items = None
    connector.cleanup = AsyncMock()
    return connector


def _graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_document = AsyncMock(
        return_value={"_key": CID, "scope": "team", "createdBy": "u1", "orgId": "o1", "name": NAME, "accountType": "free"}
    )
    graph.begin_transaction = AsyncMock(return_value="txn1")
    graph.increment_node_field = AsyncMock(return_value=7)
    return graph


@contextmanager
def _factory_returns(connector: Any) -> Iterator[None]:  # noqa: ANN401
    with patch.object(routes, "resolve_config_service", return_value=AsyncMock()), \
         patch.object(routes, "build_graph_data_store", return_value=MagicMock()), \
         patch.object(routes, "get_data_entities_processor_cls", return_value=MagicMock()), \
         patch.object(routes.ConnectorFactory, "create_connector", AsyncMock(return_value=connector)):
        yield


async def _raw_build(connector: Any, registry: Any = None) -> Any:  # noqa: ANN401
    container = MagicMock()
    container.connectors_map = {}
    with _factory_returns(connector):
        try:
            return await routes._build_and_store_connector(
                container, CID, "CONFLUENCE", registry or AsyncMock(), _graph(), "u1", "o1", is_admin=True, logger=LOG,
            )
        finally:
            if registry is None:
                assert CID not in container.connectors_map, "a connector that failed to start must not be cached"


async def _build(connector: Any, registry: Any = None) -> Outcome:  # noqa: ANN401
    return await _outcome(_raw_build(connector, registry))


@pytest.mark.parametrize("where", ["init", "test"])
@pytest.mark.parametrize(("case", "exc", "status", "text", "retry_after"), VENDOR_FAILURES, ids=IDS)
async def test_build_answers_the_source_failure(
    where: str, case: str, exc: BaseException, status: int, text: str, retry_after: str | None,
) -> None:
    connector = _fake_connector(**{where: exc})
    _assert_answer(await _build(connector), status, text, retry_after)
    connector.cleanup.assert_awaited()


@pytest.mark.parametrize(*OUTAGE_CAUSES, ids=OUTAGE_IDS)
@pytest.mark.parametrize("where", ["init", "test"])
async def test_init_error_raised_from_an_outage_keeps_its_words_with_the_outage_status(
    where: str, cause: BaseException, status: int, retry_after: str | None, caplog: pytest.LogCaptureFixture,
) -> None:
    text = "Couldn't reach Confluence at https://acme.atlassian.net. Check the site URL."
    error = ConnectorInitError(text)
    error.__cause__ = cause
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    code, detail, headers = await _build(_fake_connector(**{where: error}))
    assert (code, detail, headers.get("Retry-After")) == (status, text, retry_after)
    _assert_one_quiet_failure_line(caplog, "connector.init_failed")


@pytest.mark.parametrize("where", ["init", "test"])
async def test_blocked_address_answers_409_with_the_guards_words(where: str, caplog: pytest.LogCaptureFixture) -> None:
    text = "Could not find a server named 'wiki.internal'."
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    assert await _build(_fake_connector(**{where: AddressNotAllowedError(text)})) == (409, text, {})
    line = _assert_one_quiet_failure_line(caplog, "connector.init_failed")
    assert (line.levelno, line.error_class) == (logging.WARNING, "bad_request"), "an admin's address, not our bug"


@pytest.mark.parametrize("where", ["init", "test"])
async def test_configuration_init_error_stays_400(where: str) -> None:
    text = "This connector is not linked to an OAuth app, so it cannot start."
    assert await _build(_fake_connector(**{where: ConnectorInitError(text)})) == (400, text, {})


@pytest.mark.parametrize("where", ["init", "test"])
async def test_init_error_from_a_refused_credential_stays_400_with_its_words(where: str) -> None:
    """The connector's own sentence for a refused credential is the better prompt than the generic reconnect."""
    text = "Confluence at https://acme.atlassian.net rejected the credentials (HTTP 401). Check the email and API token."
    error = ConnectorInitError(text)
    error.__cause__ = _httpx_status(401)
    assert await _build(_fake_connector(**{where: error})) == (400, text, {})


async def test_connection_test_false_stays_400() -> None:
    code, detail, _ = await _build(_fake_connector(test=False))
    assert (code, detail) == (400, "Connection test failed. Please verify your credentials have proper access.")


@pytest.mark.parametrize("where", ["init", "test"])
async def test_unclassified_bug_stays_the_generic_500_without_its_text(
    where: str, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    code, detail, _ = await _build(_fake_connector(**{where: KeyError("secret-token-abc")}))
    assert (code, detail) == (500, GENERIC_500)
    errors = [r for r in caplog.records if r.name == LOG.name and r.levelno >= logging.ERROR]
    assert len(errors) == 1 and errors[0].exc_info is not None, "a bug keeps its ERROR line with the stack"


@pytest.mark.parametrize("where", ["init", "test"])
@pytest.mark.parametrize("status", [404, 400])
async def test_item_worded_source_answers_keep_the_generic_500(where: str, status: int) -> None:
    assert (await _build(_fake_connector(**{where: _httpx_status(status)})))[:2] == (500, GENERIC_500)


async def test_retry_error_wrapping_a_429_is_unwrapped() -> None:
    code, _, headers = await _build(_fake_connector(test=_retry_error(_httpx_status(429, {"Retry-After": "5"}))))
    assert (code, headers.get("Retry-After")) == (429, "5")


async def test_retryable_refresh_error_keeps_the_generic_500() -> None:
    error = RefreshError("temporary", retryable=True)
    assert (await _build(_fake_connector(init=error)))[:2] == (500, GENERIC_500)


async def test_http_date_retry_after_is_dropped() -> None:
    failure = _httpx_status(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    code, _, headers = await _build(_fake_connector(test=failure))
    assert code == 429 and "Retry-After" not in headers


@pytest.mark.parametrize("where", ["init", "test"])
async def test_mapped_answer_names_the_instance(where: str) -> None:
    assert (await _build(_fake_connector(**{where: _httpx_status(503)})))[:2] == (
        502, f"Could not reach {NAME}. Please try again later."
    )


async def test_store_failure_after_a_good_test_is_not_blamed_on_the_source() -> None:
    registry = AsyncMock()
    registry.update_connector_instance = AsyncMock(side_effect=ConnectionError("graph down"))
    assert (await _build(_fake_connector(), registry))[:2] == (500, GENERIC_500)


async def test_connectors_own_http_exception_is_relayed_unchanged() -> None:
    own = HTTPException(409, "The connector 'Acme Wiki' is not connected. Check its settings and try again.")
    with pytest.raises(HTTPException) as caught:
        await _raw_build(_fake_connector(init=own))
    assert caught.value is own and caught.value.__cause__ is not own


@pytest.mark.parametrize("where", ["init", "test"])
async def test_mapped_build_failure_logs_one_line_without_a_stack_and_keeps_the_cause(
    where: str, caplog: pytest.LogCaptureFixture,
) -> None:
    failure = _httpx_status(429, {"Retry-After": "30"})
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    with pytest.raises(HTTPException) as caught:
        await _raw_build(_fake_connector(**{where: failure}))
    assert caught.value.__cause__ is failure
    _assert_one_quiet_failure_line(caplog, "connector.init_failed")


@pytest.mark.parametrize("where", ["init", "test"])
async def test_init_error_logs_one_line_and_keeps_the_cause(where: str, caplog: pytest.LogCaptureFixture) -> None:
    error = ConnectorInitError("This connector is not linked to an OAuth app, so it cannot start.")
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    with pytest.raises(HTTPException) as caught:
        await _raw_build(_fake_connector(**{where: error}))
    assert caught.value.__cause__ is error
    assert [r.getMessage() for r in caplog.records if r.name == LOG.name and r.levelno >= logging.WARNING] == [
        "connector.init_failed"
    ]


def _assert_one_quiet_failure_line(caplog: pytest.LogCaptureFixture, event: str) -> logging.LogRecord:
    lines = [r for r in caplog.records if r.name == LOG.name and r.getMessage() == event]
    assert len(lines) == 1, [r.getMessage() for r in caplog.records]
    assert lines[0].levelno < logging.ERROR and lines[0].exc_info is None
    assert not [r for r in caplog.records if r.name == LOG.name and r.levelno >= logging.ERROR]
    return lines[0]


# --- the toggle route end to end (it relays the build's HTTPException) ----------------------------------------


def _toggle_request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    request.headers = {}
    request.json = AsyncMock(return_value={"type": "sync", "enabled": True})
    container = MagicMock()
    container.logger.return_value = LOG
    container.messaging_producer = AsyncMock()
    container.connectors_map = {}
    request.app.container = container
    registry = AsyncMock()
    registry.get_connector_instance = AsyncMock(return_value={
        "_key": CID, "type": "Confluence", "scope": "team", "createdBy": "u1", "authType": "API_TOKEN",
        "isActive": False, "isAgentActive": False, "supportsAgent": True, "isConfigured": True, "name": NAME,
        "appGroup": "Atlassian", "appGroupId": None,
    })
    registry.update_connector_instance = AsyncMock(return_value={"updatedAtTimestamp": 1000})
    request.app.state.connector_registry = registry
    return request


@pytest.mark.parametrize(
    ("case", "exc", "status", "text", "retry_after"),
    [VENDOR_FAILURES[0], VENDOR_FAILURES[2], VENDOR_FAILURES[4], VENDOR_FAILURES[5], VENDOR_FAILURES[8]],
    ids=["vendor-401", "refresh-token-dead", "vendor-429", "vendor-503", "vendor-403"],
)
async def test_toggle_on_answers_the_source_failure_and_turns_nothing_on(
    case: str, exc: BaseException, status: int, text: str, retry_after: str | None,
) -> None:
    request = _toggle_request()
    with _factory_returns(_fake_connector(test=exc)), \
         patch.object(routes, "check_beta_connector_access", AsyncMock()), \
         patch.object(routes, "evict_cached_connector", AsyncMock()), \
         patch.object(routes, "_validate_sync_filter_selections", AsyncMock()):
        got = await _outcome(routes.toggle_connector_instance(CID, request, graph_provider=_graph()))
    _assert_answer(got, status, text, retry_after)
    request.app.state.connector_registry.update_connector_instance.assert_not_awaited()


# --- filter options -------------------------------------------------------------------------------------------


def _options_request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "u1", "orgId": "o1", "role": "admin"}
    request.headers = {}
    request.app.container.logger.return_value = LOG
    request.app.container.connectors_map = {}
    registry = AsyncMock()
    registry.get_connector_instance = AsyncMock(return_value={
        "_key": CID, "type": "Confluence", "scope": "team", "createdBy": "u1", "authType": "API_TOKEN",
        "isActive": True, "isConfigured": True, "name": NAME,
    })
    registry.get_connector_metadata = AsyncMock(return_value={"config": {"filters": {"sync": {"schema": {"fields": [
        {"name": "space_keys", "optionSourceType": "dynamic"},
    ]}}}}})
    request.app.state.connector_registry = registry
    return request


def _raw_options(connector: Any, request: MagicMock | None = None, graph: Any = None) -> Any:  # noqa: ANN401
    return routes.get_filter_field_options(
        CID, "space_keys", request or _options_request(), page=1, limit=20, search=None, cursor=None,
        context_group_path=None, exclude_context_group_path=None, graph_provider=graph or AsyncMock(),
    )


async def _options(connector: Any, request: MagicMock | None = None) -> Outcome:  # noqa: ANN401
    with patch.object(routes, "check_beta_connector_access", AsyncMock()), \
         patch.object(routes, "_get_connector_from_container", return_value=connector):
        return await _outcome(_raw_options(connector, request))


def _listing_fails_with(exc: BaseException) -> MagicMock:
    connector = MagicMock(display_name=NAME)
    connector.get_filter_options = AsyncMock(side_effect=exc)
    return connector


@pytest.mark.parametrize(("case", "exc", "status", "text", "retry_after"), VENDOR_FAILURES, ids=IDS)
async def test_filter_options_answer_the_source_failure(
    case: str, exc: BaseException, status: int, text: str, retry_after: str | None,
) -> None:
    _assert_answer(await _options(_listing_fails_with(exc)), status, text, retry_after)


async def test_cold_filter_options_answer_the_build_failure() -> None:
    connector = _fake_connector(test=_httpx_status(429, {"Retry-After": "9"}))
    with _factory_returns(connector), \
         patch.object(routes, "check_beta_connector_access", AsyncMock()), \
         patch.object(routes, "_get_connector_from_container", return_value=None):
        got = await _outcome(_raw_options(connector, graph=_graph()))
    _assert_answer(got, 429, "Too many requests", "9")


async def test_filter_options_init_error_answers_409_with_its_words() -> None:
    text = "Select a site before loading its spaces."
    assert await _options(_listing_fails_with(ConnectorInitError(text))) == (409, text, {})


@pytest.mark.parametrize(*OUTAGE_CAUSES, ids=OUTAGE_IDS)
async def test_filter_options_init_error_raised_from_an_outage_keeps_its_words_with_the_outage_status(
    cause: BaseException, status: int, retry_after: str | None, caplog: pytest.LogCaptureFixture,
) -> None:
    text = "Couldn't reach Confluence at https://acme.atlassian.net. Check the site URL."
    error = ConnectorInitError(text)
    error.__cause__ = cause
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    code, detail, headers = await _options(_listing_fails_with(error))
    assert (code, detail, headers.get("Retry-After")) == (status, text, retry_after)
    _assert_one_quiet_failure_line(caplog, "connector.filter_options_failed")


async def test_filter_options_unclassified_bug_stays_the_generic_500() -> None:
    code, detail, _ = await _options(_listing_fails_with(RuntimeError("connector crashed")))
    assert code == 500 and detail.startswith(OPTIONS_500)


async def test_unmapped_filter_failure_logs_one_line_with_the_vendor_text_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    request = httpx.Request("GET", "https://vendor.example/api/spaces?access_token=SECRET123")
    failure = httpx.HTTPStatusError(
        f"Client error '404 Not Found' for url '{request.url}'", request=request, response=httpx.Response(404, request=request),
    )
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    assert (await _options(_listing_fails_with(failure)))[0] == 500
    lines = [r for r in caplog.records if r.name == LOG.name and r.levelno >= logging.WARNING]
    assert [r.getMessage() for r in lines] == ["connector.filter_options_failed"]
    assert "SECRET123" not in lines[0].error_message and "vendor.example" not in lines[0].error_message


@pytest.mark.parametrize("status", [404, 400])
async def test_filter_options_item_worded_source_answers_keep_the_generic_500(status: int) -> None:
    code, detail, _ = await _options(_listing_fails_with(_httpx_status(status)))
    assert code == 500 and detail.startswith(OPTIONS_500)


async def test_filter_options_rate_limited_403_is_429() -> None:
    assert (await _options(_listing_fails_with(_httpx_status(403, {"X-RateLimit-Remaining": "0"}))))[0] == 429


async def test_filter_options_name_the_instance() -> None:
    assert (await _options(_listing_fails_with(_httpx_status(503))))[:2] == (
        502, f"Could not reach {NAME}. Please try again later."
    )


async def test_metadata_read_failure_is_not_blamed_on_the_source() -> None:
    request = _options_request()
    request.app.state.connector_registry.get_connector_metadata = AsyncMock(side_effect=ConnectionError("store down"))
    connector = _listing_fails_with(AssertionError("never called"))
    code, detail, _ = await _options(connector, request)
    assert code == 500 and detail.startswith(OPTIONS_500)
    connector.get_filter_options.assert_not_awaited()


async def test_mapped_filter_failure_logs_one_line_without_a_stack_and_keeps_the_cause(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failure = _httpx_status(503)
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    with patch.object(routes, "check_beta_connector_access", AsyncMock()), \
         patch.object(routes, "_get_connector_from_container", return_value=_listing_fails_with(failure)), \
         pytest.raises(HTTPException) as caught:
        await _raw_options(None)
    assert caught.value.status_code == 502 and caught.value.__cause__ is failure
    _assert_one_quiet_failure_line(caplog, "connector.filter_options_failed")


# --- a 403 is logged as what it is answered ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "status", "error_class"),
    [({"X-RateLimit-Remaining": "0"}, 429, "rate_limited"), ({}, 403, "forbidden")],
    ids=["rate-limited-403", "plain-403"],
)
@pytest.mark.parametrize("where", ["init", "test", "filter-options"])
async def test_a_source_403_logs_the_class_it_is_answered_as(
    where: str, headers: dict[str, str], status: int, error_class: str, caplog: pytest.LogCaptureFixture,
) -> None:
    failure = _httpx_status(403, headers)
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    if where == "filter-options":
        code, event = (await _options(_listing_fails_with(failure)))[0], "connector.filter_options_failed"
    else:
        code, event = (await _build(_fake_connector(**{where: failure})))[0], "connector.init_failed"
    line = _assert_one_quiet_failure_line(caplog, event)
    assert (code, line.error_class, line.error_type) == (status, error_class, "HTTPStatusError")


async def test_init_error_raised_from_a_rate_limited_403_logs_rate_limited(caplog: pytest.LogCaptureFixture) -> None:
    error = ConnectorInitError("Confluence is limiting requests from PipesHub. Try again in a few minutes.")
    error.__cause__ = _httpx_status(403, {"X-RateLimit-Remaining": "0"})
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    code = (await _build(_fake_connector(test=error)))[0]
    line = _assert_one_quiet_failure_line(caplog, "connector.init_failed")
    assert (code, line.error_class) == (429, "rate_limited")


# --- the indexer's stream route: a connector that cannot start is not a refused record -------------------------


def _indexer_reads(got: Outcome) -> tuple[str, bool]:
    """How the indexer reads the answer: make_api_call's ApiCallError, then the consumer's classifier."""
    code, detail, headers = got
    exc = ApiCallError("stream failed", status_code=code, source_reason=_source_reason(headers, json.dumps({"detail": detail})))
    return MessageErrorClassifier.classify_by_exception(exc), is_source_refusal(exc)


async def _stream_internal(connector: Any, *, started: bool) -> Outcome:  # noqa: ANN401
    record = MagicMock(
        id="r1", connector_id=CID, org_id="o1", is_placeholder=False, connector_name=Connectors.CONFLUENCE,
    )
    graph = _graph()
    graph.get_document = AsyncMock(return_value={
        "_key": CID, "scope": "team", "createdBy": "u1", "orgId": "o1", "name": NAME, "type": "Confluence",
        "isActive": True,
    })
    graph.get_record_by_id = AsyncMock(return_value=record)
    request = MagicMock()
    request.app.container.connectors_map = {CID: connector} if started else {}
    request.app.state.connector_registry = AsyncMock()
    with _factory_returns(connector), patch.object(routes, "logger", LOG):
        return await _outcome(routes.stream_record_internal(
            request, "r1", graph_provider=graph, config_service=MagicMock(), claims={"orgId": "o1", "userId": "u1"},
        ))


@pytest.mark.parametrize(
    ("failure", "status", "text", "retry_after"),
    [
        (_httpx_status(403), 409, ACCOUNT_FORBIDDEN, None),
        (_httpx_status(429, {"Retry-After": "7"}), 429, "Too many requests", "7"),
        (_httpx_status(503), 502, "Could not reach", None),
    ],
    ids=["403-is-409", "429-unchanged", "503-unchanged"],
)
async def test_cold_stream_start_up_failure_is_retried_by_the_indexer(
    failure: BaseException, status: int, text: str, retry_after: str | None, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger=LOG.name)
    got = await _stream_internal(_fake_connector(test=failure), started=False)
    _assert_answer(got, status, text, retry_after)
    assert _indexer_reads(got)[0] == MessageErrorType.TRANSIENT, "restoring the account's access must recover the record"
    _assert_one_quiet_failure_line(caplog, "connector.init_failed")


async def test_warm_stream_item_403_stays_terminal_for_the_indexer() -> None:
    connector = _fake_connector()
    connector.rate_budget = None
    connector.stream_record = AsyncMock(side_effect=_httpx_status(403))
    got = await _stream_internal(connector, started=True)
    assert (got[0], "this item" in got[1], got[2].get("X-Source-Refusal")) == (403, True, "1")
    assert _indexer_reads(got) == (MessageErrorType.TERMINAL, True)

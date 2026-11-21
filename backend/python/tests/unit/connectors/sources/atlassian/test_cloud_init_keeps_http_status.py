"""F55: Jira Cloud and Confluence Cloud keep the HTTP status of Atlassian's answer when init() fails.

The sync consumer retries a failure unless its exception chain says otherwise. Atlassian's
accessible-resources answer used to fail as a bare ``Exception("... status 401: <body>")``, so a
revoked or expired token was retried like an outage, three times, with the response body in the log. It now
raises ``ApiCallError`` with the status (no body): a 401/403/404 ends at once, a 429/5xx is still
retried. The connector and its client builder no longer write their own failure line: the layer
that handles the failure (the consumer, the turn-on route, boot) logs it once, with its class.

Real objects: the clients' ``get_accessible_resources`` / ``build_from_services``, the connectors'
``init()``, ``HTTPResponse`` over an ``httpx.Response``, both classifiers, the route's
``_build_and_store_connector``. Faked: ``HTTPClient`` (answers from memory), the config store,
the graph and the notification hook.
"""

import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.connectors.api import router as router_module
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.jira.jira import JiraClient
from app.utils import error_class
from app.utils.api_call import ApiCallError

ORG_ID = "65f0a1b2c3d4e5f6a7b8aaaa"
CONNECTOR_ID = "65f0a1b2c3d4e5f6a7b8cccc"
SITE = "https://acme.atlassian.net"
RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
BODY_MARKER = "body-marker-f55"  # in every error body: must never reach an exception message
ONE_SITE = [{"id": "cloud-1", "name": "acme", "url": SITE, "scopes": []}]
TWO_SITES = [*ONE_SITE, {"id": "cloud-2", "name": "beta", "url": "https://beta.atlassian.net", "scopes": []}]

CLIENTS = {"jira": ("app.sources.client.jira.jira", JiraClient),
           "confluence": ("app.sources.client.confluence.confluence", ConfluenceClient)}

STATUS_CASES = [
    # status, error_class, retry decision
    (401, "auth_expired", MessageErrorType.TERMINAL),
    (403, "forbidden", MessageErrorType.TERMINAL),
    (404, "not_found", MessageErrorType.TERMINAL),
    (429, "rate_limited", MessageErrorType.TRANSIENT),
    (500, "upstream_5xx", MessageErrorType.TRANSIENT),
    (503, "unavailable", MessageErrorType.TRANSIENT),
]


def _answer(status: int, body: object) -> HTTPResponse:
    content = {"text": body} if isinstance(body, str) else {"json": body}
    return HTTPResponse(httpx.Response(status, request=httpx.Request("GET", RESOURCES_URL), **content))


def _http_client_answering(status: int, body: object) -> MagicMock:
    """A stand-in for the module's HTTPClient class: every instance answers ``status`` with ``body``."""
    instance = MagicMock()
    instance.execute = AsyncMock(return_value=_answer(status, body))
    instance.close = AsyncMock()
    return MagicMock(return_value=instance)


def _links(exc: BaseException) -> list[BaseException]:
    out, link = [], exc
    while link is not None and link not in out:
        out.append(link)
        link = link.__cause__ or link.__context__
    return out


def _error_body(status: int) -> dict:
    return {"code": status, "message": f"denied ({BODY_MARKER})"}


# ---------------------------------------------------------------------------
# The shared helper: get_accessible_resources
# ---------------------------------------------------------------------------


class TestAccessibleResourcesKeepTheStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("client", sorted(CLIENTS))
    @pytest.mark.parametrize(("status", "expected_class", "expected_retry"), STATUS_CASES)
    async def test_a_refusal_carries_its_status_to_both_classifiers(
        self, client: str, status: int, expected_class: str, expected_retry: str,
    ) -> None:
        module, cls = CLIENTS[client]
        with patch(f"{module}.HTTPClient", _http_client_answering(status, _error_body(status))):
            with pytest.raises(Exception, match="Failed to fetch accessible resources") as caught:
                await cls.get_accessible_resources("token")

        typed = [link for link in _links(caught.value) if isinstance(link, ApiCallError)]
        assert [link.status_code for link in typed] == [status]
        assert error_class.classify_error(caught.value) == expected_class
        assert MessageErrorClassifier.classify_by_exception(caught.value) == expected_retry

    @pytest.mark.asyncio
    @pytest.mark.parametrize("client", sorted(CLIENTS))
    async def test_the_response_body_is_left_out_of_every_message(self, client: str) -> None:
        module, cls = CLIENTS[client]
        with patch(f"{module}.HTTPClient", _http_client_answering(401, _error_body(401))):
            with pytest.raises(Exception) as caught:
                await cls.get_accessible_resources("token")

        messages = [str(link) for link in _links(caught.value)]
        assert all(BODY_MARKER not in m for m in messages), messages
        assert "status 401" in messages[0]  # the status is still named for the person reading it

    @pytest.mark.asyncio
    @pytest.mark.parametrize("client", sorted(CLIENTS))
    @pytest.mark.parametrize(("answer", "error"), [
        (f"<html>{BODY_MARKER}</html>", "Failed to parse JSON response"),
        ({"note": BODY_MARKER}, "Expected list of resources"),
    ])
    async def test_an_unreadable_site_list_leaves_its_body_out(self, client: str, answer: object, error: str) -> None:
        module, cls = CLIENTS[client]
        with patch(f"{module}.HTTPClient", _http_client_answering(200, answer)):
            with pytest.raises(Exception, match=error) as caught:
                await cls.get_accessible_resources("token")

        assert all(BODY_MARKER not in str(link) for link in _links(caught.value))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("client", sorted(CLIENTS))
    async def test_an_unreachable_host_stays_a_retried_outage(self, client: str) -> None:
        module, cls = CLIENTS[client]
        refusing = MagicMock()
        refusing.return_value.execute = AsyncMock(side_effect=httpx.ConnectError("refused"))
        refusing.return_value.close = AsyncMock()
        with patch(f"{module}.HTTPClient", refusing):
            with pytest.raises(Exception) as caught:
                await cls.get_accessible_resources("token")

        assert not any(isinstance(link, ApiCallError) for link in _links(caught.value))
        assert error_class.classify_error(caught.value) == "unavailable"
        assert MessageErrorClassifier.classify_by_exception(caught.value) == MessageErrorType.TRANSIENT


# ---------------------------------------------------------------------------
# The connectors' init(), through the real client builders
# ---------------------------------------------------------------------------


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def connector_logger() -> Iterator[tuple[logging.Logger, "_Records"]]:
    logger = logging.getLogger("test_cloud_init_keeps_http_status.connector")
    level, propagate = logger.level, logger.propagate
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    records = _Records()
    logger.addHandler(records)
    yield logger, records
    logger.removeHandler(records)
    logger.setLevel(level)
    logger.propagate = propagate


CONNECTORS = {"jira": (JiraConnector, "app.sources.client.jira.jira"),
              "confluence": (ConfluenceConnector, "app.sources.client.confluence.confluence")}


def _cloud_connector(kind: str, logger: logging.Logger, base_url: str = SITE) -> JiraConnector | ConfluenceConnector:
    cls, _ = CONNECTORS[kind]
    processor = MagicMock()
    processor.org_id = ORG_ID
    processor.withdraw_team_app_edge = AsyncMock()  # the graph-only step Jira Cloud's init takes first
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "auth": {"authType": "OAUTH", "baseUrl": base_url},
        "credentials": {"access_token": "token"},
    })
    with patch(f"{cls.__module__}.SyncPoint"):
        connector = cls(logger, processor, MagicMock(), config_service, CONNECTOR_ID, "team", "user-1")
    connector.notify = AsyncMock()
    return connector


def _failure_lines(records: _Records) -> list[str]:
    return [f"{r.levelname} {r.getMessage()}" for r in records.records if r.levelno >= logging.WARNING]


class TestCloudInitReachesTheClassifierWithItsStatus:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", sorted(CONNECTORS))
    @pytest.mark.parametrize(("status", "expected_class", "expected_retry"), STATUS_CASES)
    async def test_an_atlassian_refusal_classifies_by_its_status_and_logs_nothing_of_its_own(
        self, connector_logger, kind: str, status: int, expected_class: str, expected_retry: str,
    ) -> None:
        logger, records = connector_logger
        connector = _cloud_connector(kind, logger)
        with patch(f"{CONNECTORS[kind][1]}.HTTPClient", _http_client_answering(status, _error_body(status))):
            with pytest.raises(ConnectorInitError) as caught:
                await connector.init()

        assert error_class.classify_error(caught.value) == expected_class
        assert MessageErrorClassifier.classify_by_exception(caught.value) == expected_retry
        # The consumer, the route or boot logs it once; the connector and its builder stay quiet.
        assert _failure_lines(records) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", sorted(CONNECTORS))
    async def test_a_settings_error_is_raised_without_a_line_of_its_own(self, connector_logger, kind: str) -> None:
        logger, records = connector_logger
        connector = _cloud_connector(kind, logger)
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"authType": "OAUTH", "baseUrl": SITE}, "credentials": {"refresh_token": "r"},
        })
        with pytest.raises(ConnectorInitError, match="Access token required"):
            await connector.init()

        assert _failure_lines(records) == []

    @pytest.mark.asyncio
    async def test_jira_still_tells_the_user_the_status_without_the_body(self, connector_logger) -> None:
        logger, _ = connector_logger
        connector = _cloud_connector("jira", logger)
        with patch(f"{CONNECTORS['jira'][1]}.HTTPClient", _http_client_answering(401, _error_body(401))):
            with pytest.raises(ConnectorInitError) as caught:
                await connector.init()

        assert "status 401" in str(caught.value)
        assert BODY_MARKER not in str(caught.value)

    @pytest.mark.asyncio
    async def test_confluence_still_gives_the_user_its_generic_message(self, connector_logger) -> None:
        logger, _ = connector_logger
        connector = _cloud_connector("confluence", logger)
        with patch(f"{CONNECTORS['confluence'][1]}.HTTPClient", _http_client_answering(401, _error_body(401))):
            with pytest.raises(ConnectorInitError, match="Couldn't connect to Confluence"):
                await connector.init()


async def _init_error(connector) -> ConnectorInitError:
    with pytest.raises(ConnectorInitError) as caught:
        await connector.init()
    return caught.value


class TestMultiSite:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("kind", sorted(CONNECTORS))
    async def test_a_token_reaching_several_sites_is_not_retried(self, connector_logger, kind: str) -> None:
        logger, records = connector_logger
        connector = _cloud_connector(kind, logger, base_url="")
        with patch(f"{CONNECTORS[kind][1]}.HTTPClient", _http_client_answering(200, TWO_SITES)):
            error = await _init_error(connector)

        assert "multiple" in str(error)
        assert MessageErrorClassifier.classify_by_exception(error) == MessageErrorType.TERMINAL
        assert _failure_lines(records) == []  # the handler's line is the only one
        connector.notify.assert_awaited_once()  # init still tells the admin


# ---------------------------------------------------------------------------
# The turn-on route: the one line for an init failure carries its cause
# ---------------------------------------------------------------------------


def _raised_from(cause: BaseException | None, message: str) -> ConnectorInitError:
    """A ConnectorInitError raised ``from cause`` (synthetic: the real Atlassian chain is checked in QA)."""
    try:
        if cause is None:
            raise ConnectorInitError(message)
        try:
            raise cause
        except type(cause) as e:
            raise ConnectorInitError(message) from e
    except ConnectorInitError as init_error:
        return init_error


async def _turn_on(init_error: ConnectorInitError, caplog) -> tuple[HTTPException, list[logging.LogRecord]]:
    connector = MagicMock()
    connector.init = AsyncMock(side_effect=init_error)
    connector.cleanup = AsyncMock()
    container = MagicMock()
    container.connectors_map = {}
    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(
        return_value={"scope": "team", "createdBy": "user-1", "orgId": ORG_ID, "name": "Wiki"}
    )
    logger = logging.getLogger("test_cloud_init_keeps_http_status.route")

    with patch.object(router_module, "resolve_config_service", return_value=MagicMock()), \
         patch.object(router_module, "build_graph_data_store", return_value=MagicMock()), \
         patch.object(router_module, "get_data_entities_processor_cls", return_value=MagicMock()), \
         patch.object(router_module.ConnectorFactory, "create_connector", AsyncMock(return_value=connector)), \
         caplog.at_level(logging.DEBUG, logger=logger.name):
        with pytest.raises(HTTPException) as caught:
            await router_module._build_and_store_connector(
                container, CONNECTOR_ID, "CONFLUENCE", MagicMock(), graph_provider, "user-1", ORG_ID,
                is_admin=True, logger=logger,
            )
    return caught.value, [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_the_turn_on_route_logs_a_refusal_once_as_a_warning_with_its_cause(caplog) -> None:
    user_message = "Couldn't connect to Confluence. Check the site URL and the credentials."
    answer, failure = await _turn_on(
        _raised_from(ApiCallError("API request failed with status 401", status_code=401), user_message), caplog,
    )

    assert answer.status_code == 400
    assert answer.detail == user_message  # what the user sees is unchanged
    assert len(failure) == 1
    assert failure[0].levelno == logging.WARNING
    assert getattr(failure[0], "error_class", None) == "auth_expired"
    assert getattr(failure[0], "error_type", None) == "ConnectorInitError"  # the raised error, not only its cause
    assert user_message in getattr(failure[0], "error_message", "")
    assert "status 401" in getattr(failure[0], "error_message", "")
    assert getattr(failure[0], "connector_id", None) == CONNECTOR_ID


@pytest.mark.asyncio
async def test_the_turn_on_route_logs_a_causeless_init_error_as_an_error_with_its_stack(caplog) -> None:
    # Any connector's ConnectorInitError with nothing behind it (e.g. no linked OAuth app) is ours to fix.
    answer, failure = await _turn_on(_raised_from(None, "This connector is not linked to an OAuth app."), caplog)

    assert answer.status_code == 400
    assert len(failure) == 1
    assert failure[0].levelno == logging.ERROR
    assert getattr(failure[0], "error_class", None) == "internal"
    assert failure[0].exc_info or getattr(failure[0], "exc_text", None)

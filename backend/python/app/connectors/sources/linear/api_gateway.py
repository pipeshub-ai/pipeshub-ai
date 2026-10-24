"""One place that talks to Linear's GraphQL API for the sync.

Responsibilities: run an operation, decide what a failure means, wait and retry when
waiting helps, and page through connections. Callers get data or a typed exception; a
failed page is never mistaken for the end of a list.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from logging import Logger
from typing import Any

from app.sources.client.graphql.response import GraphQLResponse
from app.sources.external.linear.linear import LinearDataSource

HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_SERVER_ERROR = 500
HTTP_TOO_MANY_REQUESTS = 429

# Linear refills its hourly allowance continuously, so short waits are enough to resume.
RATE_LIMIT_WAITS: tuple[float, ...] = (5, 15, 30, 60, 120, 240)
TRANSIENT_WAITS: tuple[float, ...] = (1, 3, 9)


class LinearApiError(Exception):
    """A call to Linear failed. ``status`` is the HTTP status when there was one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LinearRateLimited(LinearApiError):
    """Linear kept answering RATELIMITED for longer than the sync is willing to wait."""


class LinearAuthError(LinearApiError):
    """The token is missing, expired or revoked."""


class LinearForbidden(LinearApiError):
    """Linear refused this one request. The token itself still works."""


class LinearNotFound(LinearApiError):
    """The entity does not exist, or this account may not see it."""


class LinearPaginationError(LinearApiError):
    """Linear said more pages follow but gave no cursor to reach them: the listing is
    incomplete and must not be taken for the whole list."""


class LinearTransientError(LinearApiError):
    """A server error, a timeout or a dropped connection that outlived the retries."""


def classify(response: GraphQLResponse) -> LinearApiError | None:
    """The error a failed response stands for, or None when it succeeded."""
    if response.success:
        return None
    raw_status = getattr(response, "status_code", None)
    status = raw_status if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
    raw_message = getattr(response, "message", None)
    message = raw_message if isinstance(raw_message, str) and raw_message else "Linear request failed"
    raw_errors = getattr(response, "errors", None)
    errors = raw_errors if isinstance(raw_errors, list) else []
    extensions = [getattr(e, "extensions", None) or {} for e in errors]
    codes = {str(x.get("code") or "").upper() for x in extensions if isinstance(x, dict)}
    types = {str(x.get("type") or "").lower() for x in extensions if isinstance(x, dict)}
    text = " ".join([message, *(str(getattr(e, "message", "") or "") for e in errors)]).lower()

    if "RATELIMITED" in codes or "ratelimited" in types or status == HTTP_TOO_MANY_REQUESTS:
        return LinearRateLimited(message, status)
    if status == HTTP_UNAUTHORIZED or "AUTHENTICATION_ERROR" in codes:
        return LinearAuthError(message, status)
    if status == HTTP_FORBIDDEN or "FORBIDDEN" in codes:
        return LinearForbidden(message, status)
    if "entity not found" in text or "could not find referenced" in text:
        return LinearNotFound(message, status)
    if status is None or status >= HTTP_SERVER_ERROR or "INTERNAL_SERVER_ERROR" in codes:
        return LinearTransientError(message, status)
    return LinearApiError(message, status)


Invoke = Callable[[LinearDataSource], Awaitable[GraphQLResponse]]


class LinearGateway:
    def __init__(
        self,
        datasource_provider: Callable[[], Awaitable[LinearDataSource]],
        logger: Logger,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._datasource = datasource_provider
        self._logger = logger
        self._sleep = sleep

    async def run(self, name: str, invoke: Invoke) -> dict[str, Any]:
        """Run one request and return its ``data``. Raises a ``LinearApiError``."""
        rate_waits = list(RATE_LIMIT_WAITS)
        transient_waits = list(TRANSIENT_WAITS)
        while True:
            datasource = await self._datasource()
            response = await invoke(datasource)
            error = classify(response)
            if error is None:
                data = getattr(response, "data", None)
                return data if isinstance(data, dict) else {}
            if isinstance(error, LinearRateLimited) and rate_waits:
                wait = rate_waits.pop(0)
                self._logger.warning("Linear rate limit on %s; retrying in %ss", name, wait)
            elif isinstance(error, LinearTransientError) and transient_waits:
                wait = transient_waits.pop(0)
                self._logger.warning("Linear %s failed (%s); retrying in %ss", name, error, wait)
            else:
                raise error
            await self._sleep(wait)

    async def call(self, operation: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run an operation registered in ``LinearGraphQLOperations`` by name."""
        return await self.run(operation, lambda ds: ds.execute_operation(operation, variables or {}))

    async def pages(
        self,
        operation: str,
        path: list[str],
        variables: dict[str, Any] | None = None,
        *,
        page_size: int = 50,
        after: str | None = None,
        fetch: Callable[[LinearDataSource, int, str | None], Awaitable[GraphQLResponse]] | None = None,
    ) -> AsyncIterator[tuple[list[dict[str, Any]], str | None, bool]]:
        """Yield ``(nodes, end_cursor, has_next)`` for each page of the connection at ``path``.

        ``fetch`` runs one page through a data source method; without it the operation is
        looked up by name.
        """
        cursor = after
        while True:
            if fetch is not None:
                data = await self.run(operation, lambda ds, page_cursor=cursor: fetch(ds, page_size, page_cursor))
            else:
                page_vars = dict(variables or {})
                page_vars["first"] = page_size
                if cursor:
                    page_vars["after"] = cursor
                data = await self.call(operation, page_vars)
            connection: Any = data
            for key in path:
                connection = (connection or {}).get(key)
            connection = connection or {}
            nodes = connection.get("nodes") or []
            info = connection.get("pageInfo") or {}
            has_next = bool(info.get("hasNextPage"))
            end_cursor = info.get("endCursor")
            if has_next and (not end_cursor or end_cursor == cursor):
                raise LinearPaginationError(f"{operation}: Linear reported more pages without a new cursor")
            cursor = end_cursor
            yield nodes, cursor, has_next
            if not has_next:
                return

    async def all_nodes(
        self,
        operation: str,
        path: list[str],
        variables: dict[str, Any] | None = None,
        *,
        page_size: int = 50,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        async for nodes, _, _ in self.pages(operation, path, variables, page_size=page_size, after=after):
            collected.extend(nodes)
        return collected

    async def complete(
        self,
        parent: dict[str, Any],
        field: str,
        operation: str,
        path: list[str],
        *,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        """All nodes of ``parent[field]``, fetching the pages the first response left out.

        ``parent[field]`` is rewritten in place to hold every node, so code that reads
        ``parent[field]["nodes"]`` sees the whole list.
        """
        connection = parent.get(field) or {}
        nodes = list(connection.get("nodes") or [])
        info = connection.get("pageInfo") or {}
        if info.get("hasNextPage"):
            if not info.get("endCursor") or not parent.get("id"):
                # Saying "complete" here would silently cut the list short.
                raise LinearApiError(f"Linear reported more {field} but gave no cursor to read them")
            nodes.extend(await self.all_nodes(
                operation, path, {"id": parent["id"]}, page_size=page_size, after=info["endCursor"]))
        parent[field] = {"nodes": nodes, "pageInfo": {"hasNextPage": False, "endCursor": None}}
        return nodes

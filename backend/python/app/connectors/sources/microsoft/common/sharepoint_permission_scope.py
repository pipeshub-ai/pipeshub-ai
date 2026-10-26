"""Which items of a SharePoint list (a document library, a OneDrive) carry their own permissions.

Graph marks no driveItem permission as inherited, so only SharePoint REST
(``HasUniqueRoleAssignments``) can tell a uniquely permissioned item from one
that inherits. SharePoint REST refuses app-only tokens from a client secret: the
app has to sign in with a certificate, and hold the SharePoint (not Graph)
``Sites.Read.All`` application permission.
"""

import asyncio
import math
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from aiolimiter import AsyncLimiter
from azure.core.exceptions import ClientAuthenticationError

GUID_PATTERN = re.compile(r'([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})')

SHAREPOINT_REST_HEADERS = {
    'Accept': 'application/json;odata=verbose',
    'Content-Type': 'application/json;odata=verbose',
}
SHAREPOINT_REST_RETRY_STATUSES = (HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE)
SHAREPOINT_REST_MAX_ATTEMPTS = 3
SHAREPOINT_REST_MAX_RETRY_AFTER_S = 30
SHAREPOINT_ITEMS_PAGE_SIZE = 5000
# A token is renewed this long before it expires, so a page read never starts with one about to lapse.
TOKEN_RENEWAL_MARGIN_S = 300


class SharePointRestError(RuntimeError):
    """A SharePoint REST read failed; ``status_code`` is None when no answer came."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SharePointAccessError(SharePointRestError):
    """SharePoint refused the app itself: sign-in failed or the token was rejected (401).

    Unlike a failure on one list, this is the same for every list, so a caller stops
    asking for the rest of the run.
    """


@dataclass
class ListPermissionScope:
    """Which parts of a SharePoint list carry their own permissions."""
    web_url: str
    list_url: str
    has_unique_role_assignments: bool
    # Lower-cased list item UniqueId -> list item Id, for items with unique role assignments.
    unique_items: dict[str, int]
    # Lower-cased UniqueIds the read saw inheriting. An item in neither was not seen,
    # which is not the same as inheriting.
    inheriting_items: set[str] = field(default_factory=set[str])


@dataclass(frozen=True)
class SharePointToken:
    token: str
    expires_on: int


def etag_unique_id(e_tag: object) -> str | None:
    """The list item UniqueId in a drive item eTag, ``"{GUID},n"``, lower-cased."""
    if isinstance(e_tag, str):
        match = GUID_PATTERN.search(e_tag)
        if match:
            return match.group(1).lower()
    return None


def drive_item_unique_id(item: object) -> str | None:
    """The SharePoint list item UniqueId of a drive item, lower-cased."""
    sharepoint_ids = getattr(item, 'sharepoint_ids', None)
    unique_id = getattr(sharepoint_ids, 'list_item_unique_id', None) if sharepoint_ids else None
    if isinstance(unique_id, str) and unique_id:
        return unique_id.lower()
    return etag_unique_id(getattr(item, 'e_tag', None))


class SharePointPermissionScopeReader:
    """Reads unique permissions over SharePoint REST.

    ``get_token`` takes a resource such as ``https://contoso-my.sharepoint.com``
    and returns a token for it; tokens are kept until shortly before they expire.
    """

    def __init__(self, get_token: Callable[[str], Awaitable[SharePointToken]], rate_limiter: AsyncLimiter) -> None:
        self._get_token = get_token
        self._rate_limiter = rate_limiter
        self._tokens: dict[str, SharePointToken] = {}

    async def read_list_scope(self, web_url: str, list_url: str) -> ListPermissionScope:
        """Raises when any page can't be read, so a failed read can't pass for "nothing is unique"."""
        list_body: dict[str, Any] = {}
        async for body in self._pages(f"{list_url}?$select=Id,HasUniqueRoleAssignments"):
            list_body = body

        unique_items: dict[str, int] = {}
        inheriting_items: set[str] = set()
        items_url = f"{list_url}/items?$select=Id,UniqueId,HasUniqueRoleAssignments&$top={SHAREPOINT_ITEMS_PAGE_SIZE}"
        async for body in self._pages(items_url, follow_next=True):
            for item in body.get('results', []):
                unique_id = item.get('UniqueId')
                if not isinstance(unique_id, str):
                    continue
                flag = item.get('HasUniqueRoleAssignments')
                if flag is True:
                    unique_items[unique_id.lower()] = item.get('Id')
                elif flag is False:
                    inheriting_items.add(unique_id.lower())

        return ListPermissionScope(
            web_url=web_url,
            list_url=list_url,
            has_unique_role_assignments=list_body.get('HasUniqueRoleAssignments') is True,
            unique_items=unique_items,
            inheriting_items=inheriting_items,
        )

    async def read_item_count(self, list_url: str) -> int:
        async for body in self._pages(f"{list_url}?$select=Id,ItemCount"):
            count = body.get('ItemCount')
            if isinstance(count, int):
                return count
        raise SharePointRestError(f"SharePoint gave no item count for {list_url}")

    async def item_has_unique_role_assignments(self, list_url: str, unique_id: str) -> bool:
        """Raises when the item can't be read, including when SharePoint doesn't know it (400)."""
        url = f"{list_url}/GetItemByUniqueId(guid'{unique_id}')?$select=Id,HasUniqueRoleAssignments"
        async for body in self._pages(url):
            flag = body.get('HasUniqueRoleAssignments')
            if isinstance(flag, bool):
                return flag
        raise SharePointRestError(f"SharePoint gave no unique-permission flag for item {unique_id}")

    async def _token_for(self, resource: str) -> str:
        cached = self._tokens.get(resource)
        if cached is None or cached.expires_on - TOKEN_RENEWAL_MARGIN_S <= time.time():
            try:
                cached = await self._get_token(resource)
            except ClientAuthenticationError as error:
                raise SharePointAccessError(f"could not sign in to {resource}: {error}") from error
            except Exception as error:
                # Not an answer from Microsoft Entra ID (the network, say): this list only.
                raise SharePointRestError(f"could not get a token for {resource}: {error}") from error
            self._tokens[resource] = cached
        return cached.token

    async def _pages(self, url: str, *, follow_next: bool = False) -> AsyncGenerator[dict[str, Any], None]:
        resource = f"https://{urlparse(url).netloc}"
        next_url: str | None = url
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            while next_url:
                headers = {**SHAREPOINT_REST_HEADERS, 'Authorization': f'Bearer {await self._token_for(resource)}'}
                payload: dict[str, Any] = (await self._get(http_client, next_url, headers)).json()
                body: dict[str, Any] = payload.get('d', {})
                yield body
                next_url = (body.get('__next') or payload.get('odata.nextLink')) if follow_next else None

    async def _get(self, http_client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> httpx.Response:
        attempt = 1
        while True:
            async with self._rate_limiter:
                response = await http_client.get(url, headers=headers)
            if response.status_code == HTTPStatus.OK:
                return response
            if response.status_code == HTTPStatus.UNAUTHORIZED:
                raise SharePointAccessError(
                    f"SharePoint REST answered 401 for {url}: {_error_message(response)}", response.status_code
                )
            if response.status_code not in SHAREPOINT_REST_RETRY_STATUSES or attempt >= SHAREPOINT_REST_MAX_ATTEMPTS:
                raise SharePointRestError(
                    f"SharePoint REST answered {response.status_code} for {url}: {_error_message(response)}",
                    response.status_code,
                )
            retry_after = response.headers.get('Retry-After', '')
            delay = int(retry_after) if retry_after.isdigit() else 2 ** attempt
            await asyncio.sleep(min(delay, SHAREPOINT_REST_MAX_RETRY_AFTER_S))
            attempt += 1


def _field(value: object, key: str) -> object:
    return cast(dict[str, object], value).get(key) if isinstance(value, dict) else None


def _error_message(response: httpx.Response) -> str:
    """SharePoint's own error text, short; never the request's headers."""
    try:
        payload: object = response.json()
    except ValueError:
        return response.text[:200]
    error = _field(payload, 'error') or _field(payload, 'odata.error')
    message = _field(error, 'message') or error
    message = _field(message, 'value') or message
    return message[:200] if isinstance(message, str) else response.text[:200]


class ListUniquePermissions:
    """Answers whether items of one list carry their own permissions, for one sync run.

    A handful of changed items are looked up one by one; once that would cost more
    than reading the whole list (one page per 5000 items), the list is read once and
    every later answer comes from it.
    """

    def __init__(self, reader: SharePointPermissionScopeReader, web_url: str, list_url: str, item_count: int) -> None:
        self._reader = reader
        self.web_url = web_url
        self.list_url = list_url
        # The list's own read plus its item pages.
        self._full_read_cost = 1 + max(1, math.ceil(item_count / SHAREPOINT_ITEMS_PAGE_SIZE))
        self._lookups = 0
        self._scope: ListPermissionScope | None = None
        self._full_read_error: Exception | None = None

    @property
    def read_in_full(self) -> bool:
        return self._scope is not None

    @property
    def unreadable(self) -> bool:
        """The whole list was to be read this run and couldn't be."""
        return self._full_read_error is not None

    async def has_unique_permissions(self, unique_id: str) -> bool:
        """Raises when it can't be told for this item."""
        if self._full_read_error is not None:
            # A list that couldn't be read once this run isn't read again for every later item.
            raise self._full_read_error
        if self._scope is None and self._lookups >= self._full_read_cost:
            try:
                self._scope = await self._reader.read_list_scope(self.web_url, self.list_url)
            except Exception as error:
                self._full_read_error = error
                raise
        if self._scope is not None:
            if unique_id in self._scope.unique_items:
                return True
            if unique_id in self._scope.inheriting_items:
                return False
        # Not read in full yet, or added after the read: asked on its own.
        self._lookups += 1
        return await self._reader.item_has_unique_role_assignments(self.list_url, unique_id)

    async def changed_to_unique_since_read(self, unique_ids: set[str]) -> set[str]:
        """Which of these items, seen inheriting by the full read, are unique now; reads the list again."""
        scope = await self._reader.read_list_scope(self.web_url, self.list_url)
        return {unique_id for unique_id in unique_ids if unique_id not in scope.inheriting_items}

"""Tells whether the app lacks the Microsoft Graph application permission Sites.FullControl.All.

Without it, Graph's delta never reports a change to who can open an item that has its own permissions (a user
added to or removed from it), so PipesHub keeps the old access. Graph documents this ("to process permissions
correctly your application will need to request Sites.FullControl.All"), and a live tenant behaved so.

Listing a site's permissions (GET /sites/{id}/permissions) needs exactly that permission, and every tenant has
a root site, so the answer depends on nothing but the grant. It counts as missing only when the root site
itself can be read and its permissions answer 403: any other answer (throttling, a server error, a site that
cannot be read) proves nothing, and the check says it cannot tell.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiolimiter import AsyncLimiter
    from msgraph import GraphServiceClient

GRAPH_PERMISSION = "Sites.FullControl.All"


async def graph_sites_fullcontrol_missing(client: "GraphServiceClient", rate_limiter: "AsyncLimiter") -> bool | None:
    """True when the permission is missing, False when it is granted, None when the answer was not definite."""
    try:
        async with rate_limiter:
            await client.sites.by_site_id("root").get()
    except Exception:
        # The root site must be readable for the next answer to mean anything.
        return None
    try:
        async with rate_limiter:
            await client.sites.by_site_id("root").permissions.get()
    except Exception as e:
        return True if getattr(e, "response_status_code", None) == HTTPStatus.FORBIDDEN else None
    return False

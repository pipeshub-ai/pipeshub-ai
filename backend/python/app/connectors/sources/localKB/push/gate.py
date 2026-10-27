"""``ENABLE_KB_PUSH``: with the flag off the push routes do not exist.

A middleware reads the flag (first, before routing, authentication or any graph call) and
records it on the ASGI scope; ``GatedRoute`` then does not match while it is off. The router
answers exactly as an app built without the routes: 404 for an unknown path, and for a path
another route serves (``GET /records``) the same 405 and ``Allow`` header, for every method.
An app without the middleware has the flag off, and an unreadable flag is off.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from fastapi.routing import APIRoute
from starlette.routing import Match

from app.services.featureflag.platform_settings import is_kb_push_enabled

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

SCOPE_KEY = "kb_push_enabled"
_PUSH_PATH = re.compile(r"^/api/v1/kb/[^/]+/(?:records|schema)/?$")


class GatedRoute(APIRoute):
    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if scope.get(SCOPE_KEY) is not True:
            return Match.NONE, {}
        return super().matches(scope)


class KbPushFlagMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _PUSH_PATH.match(scope.get("path", "")):
            scope[SCOPE_KEY] = await self._enabled(scope)
        await self.app(scope, receive, send)

    @staticmethod
    async def _enabled(scope: Scope) -> bool:
        try:
            container = scope["app"].container
            return await is_kb_push_enabled(container.config_service()) is True
        except Exception as e:
            logger.warning("ENABLE_KB_PUSH could not be read, treating it as off: %s", e)
            return False

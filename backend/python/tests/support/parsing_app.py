"""A FastAPI app over the real parsing router with a fixed governor and a scoped token, for route tests that drive
``POST /api/v1/parse`` (the same set-up as ``tests/unit/api/routes/test_parsing_routes.py``, kept here so a test module does
not import another test module)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request

from app.api.middlewares.auth import authMiddleware
from app.api.routes.parsing import router as parsing_router
from app.models.blocks import BlocksContainer
from app.services.parsing.interface import ParseResult, ParserProvider
from app.services.parsing.registry import ParserRegistry  # noqa: TC001
from app.services.resource_governor import ResourceGovernor
from app.services.resource_governor.models import ResourceSnapshot


def _governor(heavy_limit: int = 5) -> ResourceGovernor:
    snapshot = ResourceSnapshot(
        cpu_quota=4.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
        mem_limit_bytes=8 * 1024 ** 3, mem_working_set_bytes=1 * 1024 ** 3, source="test",
    )

    class _FixedProbe:
        def snapshot(self) -> ResourceSnapshot:
            return snapshot

    return ResourceGovernor(logger=logging.getLogger("test.parsing_app.governor"), env_parse=heavy_limit, probe=_FixedProbe())


async def _authenticated_as_indexing(request: Request) -> Request:
    request.state.user = {"token_type": "scoped", "scopes": ["document:parse"], "orgId": "org-123"}
    return request


def build_app(registry: ParserRegistry) -> FastAPI:
    app = FastAPI()
    app.state.parser_registry = registry
    app.state.governor = _governor()
    app.include_router(parsing_router)
    app.dependency_overrides[authMiddleware] = _authenticated_as_indexing
    return app


def ok_result() -> ParseResult:
    return ParseResult(
        block_container=BlocksContainer(blocks=[], block_groups=[]), provider_used=ParserProvider.DEFAULT, metadata={},
    )

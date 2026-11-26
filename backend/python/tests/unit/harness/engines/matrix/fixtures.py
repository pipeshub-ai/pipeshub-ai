"""The matrix lane as pytest fixtures, for every module that runs cells on it (E2E3, E2E4, QA-31).

A module imports both: ``matrix_fixture`` is its lane, the fixture ``matrix`` (one per module), and
``echo_template_fixture`` puts the runner image's program behind the catalog's ``echo`` template, over the harness
conftest's ``echo_catalog_server``, for every test in it. Their own names keep a test's ``matrix`` parameter from
shadowing the import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from tests.unit.harness.engines.matrix import mcp
from tests.unit.harness.engines.matrix.lane import MatrixLane, matrix_lane
from tests.unit.harness.engines.real_stack import lane_available

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from app.agents.mcp.models import MCPServerTemplate


@pytest_asyncio.fixture(scope="module", loop_scope="module", name="matrix")
async def matrix_fixture(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[MatrixLane]:
    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    workdir: Path = tmp_path_factory.mktemp("e2e3")
    async with matrix_lane(workdir) as lane:
        yield lane


@pytest.fixture(autouse=True, name="echo_catalog_server")
def echo_template_fixture(echo_catalog_server: MCPServerTemplate) -> Iterator[MCPServerTemplate]:
    with mcp.echo_template():
        yield echo_catalog_server


__all__ = ["echo_template_fixture", "matrix_fixture"]

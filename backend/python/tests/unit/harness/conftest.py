"""Fixtures shared by the harness unit tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport
from app.agents.mcp.registry import get_mcp_registry
from harness_core.testing.sandbox.mcp_runner import ECHO_COMMAND, SECRET_NAME
from tests.support.journal_history import journal_history_check  # noqa: F401  (autouse: JRN-22)

if TYPE_CHECKING:
    from collections.abc import Iterator

# A hang guard, not a bound: these module fixtures start Node under ts-node inside the guard of the first test
# that asks for them. On a loaded host the start outlasted the suite's 30 s, and every test of the module then
# failed on the fixture's cached error.
NODE_PROCESS_FIXTURES = frozenset({"node_server", "node"})
HEAVY_TIMEOUT_S = 180


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        starts_node = NODE_PROCESS_FIXTURES & set(getattr(item, "fixturenames", ()))
        if starts_node and item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(HEAVY_TIMEOUT_S))


@pytest.fixture(autouse=True)
def echo_catalog_server() -> Iterator[MCPServerTemplate]:
    """The P5.10 tests' `echo` STDIO server as a catalog entry: main's STDIO policy (#3887) runs a
    catalog instance's template program only, so the instance's type must be in the catalog.

    Its own MonkeyPatch, not the test's: an autouse fixture asking for ``monkeypatch`` sets that up
    before every other fixture, so its undo would run last and re-install whatever a module's own
    patch had put in place (a leaked ``get_toolset_registry``)."""
    template = MCPServerTemplate(
        type_id="echo",
        display_name="Echo",
        description="Test echo server",
        transport=MCPTransport.STDIO,
        default_auth_mode=MCPAuthMode.API_TOKEN,
        supported_auth_modes=[MCPAuthMode.API_TOKEN],
        command=ECHO_COMMAND,
        required_env=[SECRET_NAME],
    )
    registry = get_mcp_registry()
    registry.auto_discover_templates()
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(registry._templates, "echo", template)  # pyright: ignore[reportPrivateUsage]
        yield template

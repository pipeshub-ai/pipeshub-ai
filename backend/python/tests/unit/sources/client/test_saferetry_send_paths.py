"""SR-12: every write tool a durable run can reach sends through a client on the send-safe path.

The inventory is QA-24's: every non-idempotent ``write``/``execute`` tool from P2.1's enumerators. For each tool's
module the check reads the clients it imports (``app.sources.client.*``) and any HTTP library it, or an action helper
it imports, uses directly. A client is on the path when it is an ``HTTPClient`` (httpx with no retry of its own, a
report on every possibly-sent failure, SR-9) that does not replace ``execute`` or ``_ensure_client``, or when it is
a reviewed SDK client that installs its send-safe mechanism. A new connector, a new SDK client, or a direct HTTP
call in a write tool fails here until it is put on the path and reviewed.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.sources.client.http.http_client import HTTPClient
from harness_core.kernel.durable.effects import classify_tool_effect
from tests.unit.harness.test_effect_tags_complete import ENUMERATORS

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.tools.base import Tool

CLIENTS = "app.sources.client."
ACTIONS = "app.agents.actions."
# Shared value types and the send-safety module itself, not clients.
CLIENT_SUPPORT = (
    "app.sources.client.http.",
    "app.sources.client.utils.",
    "app.sources.client.iclient",
    # Main's one-request-at-a-time lock over a googleapiclient service; the service it runs is the
    # reviewed google client below.
    "app.sources.client.google.serialized_execute",
)
HTTP_LIBRARIES = ("httpx", "requests", "aiohttp", "urllib3", "urllib.request", "http.client")


@dataclass(frozen=True)
class Reviewed:
    mechanism: str
    evidence: str


# SDK clients: each names the mechanism its module must install, and the cases that prove it.
SDK_CLIENTS: dict[str, Reviewed] = {
    "app.sources.client.slack.slack": Reviewed("send_safe_retry_handlers", "QA24-6, SR-8"),
    "app.sources.client.google.google": Reviewed("install_send_safe_http", "SR-2, SR-8"),
    "app.sources.client.microsoft.microsoft": Reviewed("send_safe_graph_adapter", "SR-3, SR-8"),
    "app.sources.client.github.github": Reviewed("SendSafeRetry", "SR-4, SR-8"),
    "app.sources.client.mariadb.mariadb": Reviewed("note_dbapi_failure", "SR-5, SR-8"),
    "app.sources.client.redshift.redshift": Reviewed("note_dbapi_failure", "SR-6, SR-8"),
}

# Write tools that reach no connector client.
NO_CLIENT: dict[str, str] = {
    "app.agents.actions.artifacts.artifacts": "the artifact registry (Mongo, graph, blob store), not a remote write",
    "app.agents.actions.coding_sandbox.coding_sandbox": "execute effect through the sandbox broker; parks (QA24-4)",
    "app.agents.actions.database_sandbox.database_sandbox": "execute effect through the sandbox broker; parks (QA24-4)",
    "app.agents.actions.image_generator.image_generator": "provider holds no state; one artifact per call (SR-7)",
    "app.agents.agent_loop.mcp_tool_adapter": "MCPClientManager.call_tool_in_session reports (SR-13)",
    "app.agents.agent_loop.sandbox_bridge": "execute effect through the sandbox broker; parks (QA24-4)",
    "app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox": "execute effect through the sandbox broker; parks",
    "app.agent_loop_lib.tools.builtin.data.skills": "the skill store, not a remote write",
}

# Modules of write tools that use an HTTP library themselves.
DIRECT_HTTP: dict[str, str] = {
    "app.agents.actions.microsoft.outlook.attachments": "chunk PUTs report every possibly-sent failure (SR-13)",
    "app.agents.actions.util.attachments": "reads attachments from blob storage (GET)",
    "app.agents.actions.util.blob_staging": "reads staged blobs (GET)",
    "app.agents.actions.jira.jira": "httpx exception types only, no request",
    # Main's attachment uploads: the request goes through the connector's datasource (send_upload); httpx only names
    # the timeout it reports.
    "app.agents.actions.jira.attachments": "httpx exception types only, no request",
    "app.agents.actions.confluence.attachments": "httpx exception types only, no request",
    "app.agents.actions.lumos.lumos": "httpx exception types only, no request",
    "app.agents.agent_loop.mcp_tool_adapter": "httpx/httpx2 HTTPStatusError types only, no request (main's mcp 2.x)",
}


def write_tools() -> dict[str, Tool]:
    reachable = {tool.name: tool for enumerate_ in ENUMERATORS.values() for tool in enumerate_()}
    return {
        name: tool
        for name, tool in reachable.items()
        if (effect := classify_tool_effect(name, tool.tags)).effect in ("write", "execute") and not effect.idempotent
    }


def tool_module(tool: Tool) -> str:
    bound = getattr(tool, "_bound_method", None)
    owner = getattr(bound, "__self__", None)
    return type(owner).__module__ if owner is not None else type(tool).__module__


def module_source(name: str) -> str:
    return inspect.getsource(importlib.import_module(name))


def imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def http_client_on_path(name: str) -> bool:
    module = importlib.import_module(name)
    subclasses = [
        value
        for value in vars(module).values()
        if isinstance(value, type) and issubclass(value, HTTPClient) and value is not HTTPClient
    ]
    return bool(subclasses) and not any(
        member in vars(cls) for cls in subclasses for member in ("execute", "_ensure_client")
    )


def violations(module: str, source_of: Callable[[str], str] = module_source) -> list[str]:
    seen: set[str] = set()
    pending = [module]
    clients: set[str] = set()
    found: list[str] = []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        imported = imports(source_of(current))
        clients |= {m for m in imported if m.startswith(CLIENTS) and not m.startswith(CLIENT_SUPPORT)}
        pending += [m for m in imported if m.startswith(ACTIONS) and m != current]
        if any(m == lib or m.startswith(f"{lib}.") for m in imported for lib in HTTP_LIBRARIES) and current not in DIRECT_HTTP:
            found.append(f"{current}: uses an HTTP library directly")
    if not clients and module not in NO_CLIENT:
        found.append(f"{module}: a write tool with no connector client and no reviewed reason")
    for client in sorted(clients):
        reviewed = SDK_CLIENTS.get(client)
        if reviewed is not None:
            if reviewed.mechanism not in source_of(client):
                found.append(f"{client}: no longer installs {reviewed.mechanism}")
        elif not http_client_on_path(client):
            found.append(f"{client}: not an HTTPClient on the send-safe path and not a reviewed SDK client")
    return found


@pytest.fixture(scope="module")
def write_modules() -> dict[str, list[str]]:
    by_module: dict[str, list[str]] = {}
    for name, tool in sorted(write_tools().items()):
        by_module.setdefault(tool_module(tool), []).append(name)
    return by_module


def test_sr_12_every_write_tool_sends_through_a_send_safe_client(write_modules: dict[str, list[str]]) -> None:
    found = [v for module in sorted(write_modules) for v in violations(module)]
    assert found == [], "write tools off the send-safe path (put the client on it, then review it here):\n" + "\n".join(
        found
    )


def test_sr_12_the_inventory_is_the_one_qa24_parks() -> None:
    """A floor, so an enumerator that silently lost a source cannot make SR-12 pass over nothing."""
    assert len(write_tools()) >= 142


def test_sr_12_no_review_is_stale(write_modules: dict[str, list[str]]) -> None:
    assert set(NO_CLIENT) <= set(write_modules)
    for module in DIRECT_HTTP:
        imported = imports(module_source(module))
        assert any(m == lib or m.startswith(f"{lib}.") for m in imported for lib in HTTP_LIBRARIES), module


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from app.sources.client.box.box import BoxClient\n", "app.sources.client.box.box: not an HTTPClient"),
        ("import requests\nfrom app.sources.client.jira.jira import JiraClient\n", "uses an HTTP library directly"),
        ("x = 1\n", "a write tool with no connector client"),
        ("from app.sources.client.jira.jira import JiraClient\n", None),
    ],
    ids=["unreviewed-sdk", "direct-http", "no-client", "httpclient"],
)
def test_sr_12_the_guard_names_what_is_off_the_path(source: str, expected: str | None) -> None:
    def source_of(name: str) -> str:
        return source if name == "app.agents.actions.newtool.newtool" else module_source(name)

    found = violations("app.agents.actions.newtool.newtool", source_of)
    if expected is None:
        assert found == []
    else:
        assert any(expected in v for v in found), found

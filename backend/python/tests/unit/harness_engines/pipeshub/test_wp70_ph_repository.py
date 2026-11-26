"""WP70-PH: an engine finds ``ph git clone`` and ``repository_fetch`` whatever it calls them.

A deep research run had github.com's public preset on, so ``repository_fetch`` was in the run's tool list. Codex ran
``ph tools describe pipeshub__repository_fetch`` ("this run has no tool named ...": its MCP client's server prefix is
not part of the gateway's name) and ``ph tools search repository``, which asked only the gateway's ``search_tools``:
that ranks the tools held back from a large list, never the listed ``repository_fetch``, so it printed nothing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from harness_engines.pipeshub import ph
from tests.unit.harness_engines.pipeshub import test_ph_cli as cli
from tests.unit.harness_engines.pipeshub.test_ph_cli import (
    CALL_TOOL,
    SEARCH_TOOLS,
    SLACK,
    run,
)

if TYPE_CHECKING:
    from tests.unit.harness_engines.pipeshub.test_ph_cli import Gateway

# ``test_ph_cli``'s test gateway server.
gateway = cli.gateway

REPOSITORY = {
    "name": "repository_fetch",
    "description": (
        "Clone a public git repository into this sandbox, read-only. Run `ph git clone <https url>` from your shell."
    ),
    "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}},
}
JIRA = {"name": "jira__search_issues", "description": "Search Jira issues with JQL.", "inputSchema": {"type": "object"}}


def _lazy(state: Gateway) -> None:
    """A large grant, as run 3's: the connector tools are held back behind search_tools, which ranks only those."""
    state.tools = [REPOSITORY, SLACK, SEARCH_TOOLS, CALL_TOOL]

    def answer(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "search_tools":
            found = [JIRA] if "jira" in str(arguments.get("query")) else []
            text = json.dumps(found) if found else f"No tool of this run matches {arguments.get('query')!r}."
        else:
            text = f"{name}:{json.dumps(arguments, sort_keys=True)}"
        return {"content": [{"type": "text", "text": text}], "isError": False}

    state.answer = answer


def test_wp70_ph_tools_search_finds_the_listed_repository_tool_beside_the_gateways_search(
    gateway: tuple[Gateway, str],
) -> None:
    state, url = gateway
    _lazy(state)
    code, out, _ = run(["tools", "search", "repository"], url)
    assert code == 0 and "repository_fetch" in out and "ph git clone <https url>" in out
    # The held-back tools are still found through the gateway's own search.
    code, out, _ = run(["tools", "search", "jira", "issues"], url)
    assert code == 0 and "jira__search_issues" in out
    code, out, _ = run(["--json", "tools", "search", "clone", "git"], url)
    assert json.loads(out)["data"]["tools"][0]["name"] == "repository_fetch"


def test_wp70_ph_a_name_with_the_engines_server_prefix_resolves_to_the_gateways(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    _lazy(state)
    for name in ("pipeshub__repository_fetch", "mcp__pipeshub__repository_fetch", "pipeshub_repository_fetch"):
        code, out, err = run(["tools", "describe", name], url)
        assert code == 0, (name, err)
        assert "repository_fetch" in out and "Clone a public git repository" in out
    code, out, _ = run(["tools", "call", "pipeshub__slack__send_message", "--json", '{"text": "hi"}'], url)
    assert code == 0
    called = [body["params"]["name"] for _, body in state.requests if body["method"] == "tools/call"]
    assert called[-1] == "slack__send_message"


def test_wp70_ph_an_unknown_name_says_what_is_near(gateway: tuple[Gateway, str]) -> None:
    state, url = gateway
    _lazy(state)
    code, _, err = run(["tools", "describe", "pipeshub__repo_clone"], url)
    assert code == ph.EX_DATAERR
    assert "no tool named 'pipeshub__repo_clone'" in err and "Did you mean: repository_fetch" in err


def test_wp70_ph_name_forms() -> None:
    assert ph.name_forms("pipeshub__repository_fetch") == [
        "pipeshub__repository_fetch", "repository_fetch", "pipeshub_repository_fetch",
    ]
    assert ph.name_forms("pipeshub_repository_fetch")[-1] == "repository_fetch"
    assert ph.name_forms("jira__search_issues")[0] == "jira__search_issues"

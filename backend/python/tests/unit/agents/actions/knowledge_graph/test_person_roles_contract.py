"""The roles a person lookup takes are listed in three places: the query service's ROLE_EDGES, the MCP tool's
PERSON_ROLES and the OpenAPI by-person contract. A role missing from either copy is rejected before the lookup."""

import re
from pathlib import Path

import yaml

from app.agents.actions.knowledge_graph.ops.people import ROLE_EDGES

_NODE = Path(__file__).resolve().parents[6] / "nodejs" / "apps" / "src" / "modules"


def test_the_mcp_tool_takes_every_role_in_order() -> None:
    source = (_NODE / "mcp" / "entity.tools.ts").read_text(encoding="utf-8")
    block = re.search(r"const PERSON_ROLES = \[(.*?)\] as const;", source, re.S)

    assert block is not None
    assert re.findall(r"'([a-z_]+)'", block.group(1)) == list(ROLE_EDGES)


def test_the_openapi_contract_takes_every_role_in_order() -> None:
    spec = yaml.safe_load((_NODE / "api-docs" / "pipeshub-openapi.yaml").read_text(encoding="utf-8"))
    roles = spec["components"]["schemas"]["RecordsByPersonRequest"]["properties"]["roles"]["items"]["enum"]

    assert roles == list(ROLE_EDGES)

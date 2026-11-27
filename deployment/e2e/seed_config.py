"""Writes the approvals document of a lane's stack (E2E2) through ``ConfigurationService``, in the app container.

    python /e2e/pyrun.py /e2e/seed_config.py approvals <orgId>
    python /e2e/pyrun.py /e2e/seed_config.py rules '<json>'
    python /e2e/pyrun.py /e2e/seed_config.py human <mcpInstanceId> [--keep-class-rules]
    python /e2e/pyrun.py /e2e/seed_config.py github <orgId> <userId>

``approvals`` (the durable lane): the deployment's rule asks before writes. ``rules`` (QA-6's rows, which set the
deployment's rule per row): each key of the JSON object replaces that key of the deployment's rule, and a null removes
it, so the release default applies again. ``human`` (a stack a person tests by hand,
after seed_human.py): the seeded MCP server's create_ticket asks and every class rule is the release default, enforced
rather than assumed (WP97-FU-1): a class rule left in the document is removed and logged as seed.approvals_reset, so
the stack measures what people get. A session that tests stricter rules keeps them with --keep-class-rules (or
E2E_KEEP_CLASS_RULES=1); the tool rule is set either way.

``github`` (WP-26, the repository picker): an org GitHub toolset instance named "GitHub" and <userId>'s sign-in to it,
holding the fake SaaS's GitHub token (``FAKE_SAAS_GITHUB_TOKEN``), as the OAuth callback would leave them. The fake
GitHub cannot run an OAuth sign-in, so the lane writes the result. Idempotent: an existing instance is reused.

``/services/agentHarness/approvals`` is the operator's (FLAGS.md), so the lane writes it as an operator would, through
the same service and KV store every process reads, and reads it back. Engine runs already ask for writes
(``ENGINE_APPROVALS``); the document makes the deployment's rule say so too.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants
from app.harness.assembly.services import HarnessServices

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService

KEY = config_node_constants.AGENT_HARNESS_APPROVALS.value


ASK_TOOL = "create_ticket"
CLASS_RULES = ("writes", "classes")


async def _document(config: Any) -> dict[str, Any]:
    current = await config.get_config(KEY, default=None) or {}
    if not isinstance(current, dict):
        raise SystemExit(f"{KEY} holds {type(current).__name__}, not a document")
    return dict(current)


async def _stored(config: Any, document: dict[str, Any]) -> dict[str, Any]:
    if not await config.set_config(KEY, document):
        raise SystemExit(f"{KEY} was not written")
    stored = await config.get_config(KEY, default=None, use_cache=False)
    if not isinstance(stored, dict):
        raise SystemExit(f"{KEY} reads back as {json.dumps(stored)[:200]}")
    return stored


async def write_rules(fields: dict[str, Any], config: ConfigurationService | None = None) -> dict[str, Any]:
    """The deployment's rule with ``fields`` set (a None removes its key), as stored and read back."""
    config = config or HarnessServices().config_service()
    document = await _document(config)
    rules = {**dict(document.get("default") or {}), **fields}
    document["default"] = {key: value for key, value in rules.items() if value is not None}
    stored = (await _stored(config, document)).get("default") or {}
    if any(stored.get(key) != value for key, value in fields.items()):
        raise SystemExit(f"{KEY} reads back as {json.dumps(stored)[:200]}")
    return stored


async def write_approvals(org_id: str, config: Any = None) -> None:
    stored = await write_rules({"writes": "ask"}, config)
    print(json.dumps({"approvals": {"orgId": org_id, "default": stored}}))


def human_rules(rules: dict[str, Any], tool_id: str, *, keep_class_rules: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """The deployment rule a person's stack holds: ``tool_id`` asks, and unless ``keep_class_rules`` no class rule is
    left, so the class rules are the release default. Returns it and the class rules it removed."""
    tools = {
        t: level
        for t, level in dict(rules.get("tools") or {}).items()
        # A rule for a seeded server that was lost and made again names an id nothing has any more.
        if not (t.startswith("mcp@") and t.endswith(f"/{ASK_TOOL}") and t != tool_id)
    }
    merged = {**rules, "tools": {**tools, tool_id: "ask"}}
    removed = {} if keep_class_rules else {key: merged.pop(key) for key in CLASS_RULES if key in merged}
    return merged, removed


async def write_human(instance_id: str, *, keep_class_rules: bool, config: Any = None) -> str:
    """The human stack's rule; the tool id an approval of it carries (an MCP tool's is mcp@<instance id>/<tool>)."""
    config = config or HarnessServices().config_service()
    tool_id = f"mcp@{instance_id}/{ASK_TOOL}"
    document = await _document(config)
    rules = dict(document.get("default") or {})
    merged, removed = human_rules(rules, tool_id, keep_class_rules=keep_class_rules)
    if merged != rules:
        stored = await _stored(config, {**document, "default": merged})
        rule = stored.get("default") or {}
        if rule.get("tools", {}).get(tool_id) != "ask" or any(key in rule for key in removed):
            raise SystemExit(f"{KEY} reads back as {json.dumps(stored)[:400]}")
    if removed:
        print(json.dumps({"event": "seed.approvals_reset", "removed": removed}), file=sys.stderr)
    print(json.dumps({"askToolId": tool_id}))
    return tool_id


INSTANCES_KEY = "/services/toolset-instances"
GITHUB_INSTANCE_NAME = "GitHub"


async def write_github(org_id: str, user_id: str) -> None:
    token = os.environ.get("FAKE_SAAS_GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("FAKE_SAAS_GITHUB_TOKEN is not set in the app container")
    config = HarnessServices().config_service()
    instances = await config.get_config(INSTANCES_KEY, default=[]) or []
    if not isinstance(instances, list):
        raise SystemExit(f"{INSTANCES_KEY} holds {type(instances).__name__}, not a list")
    found = next(
        (i for i in instances if isinstance(i, dict) and i.get("orgId") == org_id
         and i.get("toolsetType") == "github" and i.get("instanceName") == GITHUB_INSTANCE_NAME),
        None,
    )
    if found is None:
        now = int(time.time() * 1000)
        found = {
            "_id": str(uuid.uuid4()), "instanceName": GITHUB_INSTANCE_NAME, "toolsetType": "github",
            "authType": "OAUTH", "orgId": org_id, "createdBy": user_id, "scope": "org",
            "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }
        if not await config.set_config(INSTANCES_KEY, [*instances, found]):
            raise SystemExit(f"{INSTANCES_KEY} was not written")
    toolset_path = f"/services/toolsets/{found['_id']}/{user_id}"
    auth = {
        "isAuthenticated": True, "instanceId": found["_id"], "toolsetType": "github", "orgId": org_id,
        "userId": user_id, "credentials": {"access_token": token, "token_type": "bearer"},
        "updatedAtTimestamp": int(time.time() * 1000),
    }
    if not await config.set_config(toolset_path, auth):
        raise SystemExit(f"{toolset_path} was not written")
    stored = await config.get_config(toolset_path, default=None, use_cache=False)
    if not isinstance(stored, dict) or stored.get("isAuthenticated") is not True:
        raise SystemExit(f"{toolset_path} does not read back")
    print(json.dumps({"github": {"instanceId": found["_id"], "userId": user_id}}))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "approvals":
        asyncio.run(write_approvals(sys.argv[2]))
    elif len(sys.argv) == 3 and sys.argv[1] == "rules":
        fields = json.loads(sys.argv[2])
        if not isinstance(fields, dict):
            raise SystemExit("usage: seed_config.py rules '<json object>'")
        sys.stdout.write(json.dumps({"default": asyncio.run(write_rules(fields))}) + "\n")
    elif len(sys.argv) in (3, 4) and sys.argv[1] == "human":
        keep = sys.argv[3:] == ["--keep-class-rules"] or os.environ.get("E2E_KEEP_CLASS_RULES") == "1"
        if sys.argv[3:] not in ([], ["--keep-class-rules"]):
            raise SystemExit("usage: seed_config.py human <mcpInstanceId> [--keep-class-rules]")
        asyncio.run(write_human(sys.argv[2], keep_class_rules=keep))
    elif len(sys.argv) == 4 and sys.argv[1] == "github":
        asyncio.run(write_github(sys.argv[2], sys.argv[3]))
    else:
        raise SystemExit(
            "usage: seed_config.py approvals <orgId> | rules '<json>' | human <mcpInstanceId> [--keep-class-rules] | github <orgId> <userId>"
        )

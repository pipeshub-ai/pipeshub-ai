"""R31-SECKEY: the canaries seckey.py plants in the stores Python owns, written through ``ConfigurationService`` in the
app container as the sign-in callbacks would leave them, and taken out again (the AI models and web search documents,
which Node encrypts with its own key, are seckey_documents.js's).

    python /e2e/pyrun.py /e2e/seckey_config.py plant <  {"orgId", "userId", "canaries": {name: value}, "mcpInstanceId"}
    python /e2e/pyrun.py /e2e/seckey_config.py restore < {"orgId", "userId", "mcpInstanceId"}
    python /e2e/pyrun.py /e2e/seckey_config.py secrets < {}

The canaries come on stdin, never on a command line (a process's arguments are readable by anyone on the host). Prints
what it wrote by name, never a value. Every entry carries the lane's tag (seckey_inventory.TAG), so ``restore`` takes
out exactly what ``plant`` put in, and a second ``plant`` replaces the first.

What is written:
    toolsets: an instance of a type that takes each stored sign-in kind, with that sign-in
    the OAuth MCP server's sign-in (access and refresh token), as its callback stores it

``secrets`` writes nothing: it reads the platform's own secrets the config store holds (seckey_inventory.CONFIG_SECRETS,
R31SECKEY-FU-2), decrypted as the worker and Node read them, and answers them on stdout, which seckey.py reads on a pipe
and never prints. They are live, not planted: replacing them would sign the stack's own workers out.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from typing import Any

from app.agents.registry.toolset_registry import get_toolset_registry
from app.harness.assembly.services import HarnessServices
from app.harness.credentials.legacy_records import legacy_records

sys.path.insert(0, "/e2e")
from seckey_inventory import CONFIG_SECRETS, STORED, TAG, TOOLSET_AUTH, config_secret_values

INSTANCES = "/services/toolset-instances"


def _tagged(entry: Any) -> bool:
    return isinstance(entry, dict) and str(entry.get("instanceName") or "").startswith(f"{TAG}-")


def _toolset_types() -> dict[str, str]:
    """A toolset type that takes each stored sign-in kind (the registry's own metadata)."""
    registry = get_toolset_registry()
    if not registry.get_all_toolsets():
        registry.auto_discover_toolsets()
    found: dict[str, str] = {}
    for name in sorted(registry.get_all_toolsets()):
        metadata = registry.get_toolset_metadata(name) or {}
        for auth in metadata.get("supported_auth_types") or []:
            found.setdefault(str(auth).upper(), name)
    return found


async def _toolsets(config: Any, org: str, user: str, canaries: dict[str, str], *, plant: bool) -> list[str]:
    instances = await config.get_config(INSTANCES, default=[], use_cache=False) or []
    mine = [i for i in instances if _tagged(i) and i.get("orgId") == org]
    for instance in mine:
        await config.delete_config(f"/services/toolsets/{instance['_id']}/{user}")
    kept = [i for i in instances if i not in mine]
    wrote: list[str] = []
    if plant:
        types = _toolset_types()
        now = int(time.time() * 1000)
        for auth, (mode, fields) in TOOLSET_AUTH.items():
            if mode != STORED or not fields:
                continue
            toolset = types.get(auth)
            if toolset is None:
                raise SystemExit(f"no registered toolset takes {auth} sign-in")
            instance = {"_id": str(uuid.uuid4()), "instanceName": f"{TAG}-{auth.lower()}", "toolsetType": toolset,
                        "authType": auth, "orgId": org, "createdBy": user, "scope": "org",
                        "createdAtTimestamp": now, "updatedAtTimestamp": now}
            kept.append(instance)
            credentials = {name: canaries[f"toolset.{auth}.{name}"] for name in fields}
            if auth == "BASIC_AUTH":
                credentials["username"] = "seckey-user"
            await config.set_config(f"/services/toolsets/{instance['_id']}/{user}", {
                "isAuthenticated": True, "instanceId": instance["_id"], "toolsetType": toolset, "orgId": org,
                "userId": user, "credentials": credentials, "updatedAtTimestamp": now})
            wrote.append(f"toolset {auth} on {toolset}")
    if not await config.set_config(INSTANCES, kept):
        raise SystemExit(f"{INSTANCES} was not written")
    return wrote


async def _oauth(config: Any, org: str, user: str, instance: str, canaries: dict[str, str] | None) -> list[str]:
    if not instance:
        return []
    records = legacy_records(config)
    if canaries is None:
        record = {"instanceId": instance, "userId": user, "orgId": org, "authMode": "oauth", "isAuthenticated": False,
                  "updatedAt": int(time.time() * 1000)}
        await records.save("mcp", org, instance, user, record, user_id=user, narrowing=True)
        return ["mcp oauth sign-in withdrawn"]
    now = int(time.time() * 1000)
    record = {
        "instanceId": instance, "userId": user, "orgId": org, "authMode": "oauth", "isAuthenticated": True,
        "oauthTokens": {"accessToken": canaries["mcp.oauth.accessToken"], "tokenType": "Bearer",
                        "refreshToken": canaries["mcp.oauth.refreshToken"], "expiresIn": None,
                        "clientId": f"{TAG}-client", "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "updatedAt": now, "connectedAt": now,
    }
    if not await records.save("mcp", org, instance, user, record, user_id=user):
        raise SystemExit("the MCP OAuth sign-in was not stored")
    return ["mcp oauth sign-in"]


async def _config_secrets(config: Any) -> dict[str, str]:
    found: dict[str, str] = {}
    for name, path in CONFIG_SECRETS.items():
        found.update(config_secret_values(name, await config.get_config(path, default=None, use_cache=False)))
    return found


async def main(action: str) -> None:
    body = json.loads(sys.stdin.read())
    if action == "secrets":
        found = await _config_secrets(HarnessServices().config_service())
        # One line for seckey.py; the values never reach a terminal or a log.
        print(json.dumps({"configSecrets": found}))
        return
    org, user = str(body["orgId"]), str(body["userId"])
    canaries: dict[str, str] = body.get("canaries") or {}
    plant = action == "plant"
    config = HarnessServices().config_service()
    wrote = await _toolsets(config, org, user, canaries, plant=plant)
    wrote += await _oauth(config, org, user, str(body.get("mcpInstanceId") or ""), canaries if plant else None)
    print(json.dumps({action: wrote}))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("plant", "restore", "secrets"):
        raise SystemExit("usage: seckey_config.py plant|restore|secrets < body.json")
    asyncio.run(main(sys.argv[1]))

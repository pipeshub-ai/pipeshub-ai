"""R31-SECKEY gap 3: what a run's gateway token reaches at the real gateways, asked as the egress proxy asks.

    python /e2e/pyrun.py /e2e/seckey_gateway.py attest < {"bindings": {name: {"orgId", "runId", "attemptId"}}}
    python /e2e/pyrun.py /e2e/seckey_gateway.py live   < {"orgId", "otherOrgId", "child": {"runId", "attemptId"},
                                                          "parent": {"runId", "attemptId"}, "staleAttemptId",
                                                          "proofs": {name: proof}, "allowedModel", "otherModel",
                                                          "marker"}
    python /e2e/pyrun.py /e2e/seckey_gateway.py after  < {"marker"}

``attest`` runs in the sandbox broker's container: the broker's own ``BindingAttester`` under its own key signs each
named attempt as it signs the binding of a sandbox it starts (R38IDENT-FU-7, ADR-CRED-01 D6), so ``live`` asks with
the proof the egress proxy would hold for the held sandbox, made the one way a proof is made. Its single line is the
only output that carries a proof: seckey.py hands it to ``live`` on stdin and never prints or stores it.

``live`` and ``after`` run in the app container, beside the gateways: Node's token authority
(``/runs/{runId}/gateway-token``, the egress proxy's credential), the Tool Gateway on 8098 and the Model Gateway on
8096. ``live`` runs while the child's job is held mid-turn and keeps the child's and the parent's tokens and the child's
proof in a file only this container reads (mode 600) for ``after``, which runs once the run ended and removes it. Each
line they print is one check as JSON: what was asked, the status and the reason code; never a token, a proof, a
credential or a body.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from app.harness.assembly.services import HarnessServices
from app.harness.credential_roles import ROLE_DOCUMENTS
from app.harness.gateway.tokens import GatewayTokenUnavailable, NodeGatewayTokens
from app.harness.inline import node_state_url
from app.harness.journal_node.client import INTERNAL_PREFIX
from app.harness.journal_node.credentials import ConfiguredWorkerCredential

TOOLS = "http://127.0.0.1:8098/harness/tool-gateway"
MODELS = "http://127.0.0.1:8096"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _kept(marker: str) -> Path:
    return Path("/tmp") / f"seckey-tokens-{marker}.json"


def say(check: str, ok: bool, **fields: Any) -> None:
    print(json.dumps({"check": check, "ok": ok, **fields}), flush=True)


def _reason(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = response.text
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    body = json.loads(line[5:])
                    break
                except ValueError:
                    continue
        else:
            return text[:80]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            data = error.get("data") if isinstance(error.get("data"), dict) else {}
            return str(data.get("reasonCode") or error.get("code") or error.get("type") or error.get("message") or "")[:120]
        return str(body.get("reasonCode") or body.get("code") or error or body.get("message") or "")[:120]
    return ""


def _rpc_body(response: httpx.Response) -> dict[str, Any]:
    try:
        return response.json()
    except ValueError:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:])
                except ValueError:
                    continue
    return {}


async def _rpc(http: httpx.AsyncClient, token: str, method: str, params: dict[str, Any]) -> httpx.Response:
    return await http.post(TOOLS, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"},
                           json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


async def _listed(http: httpx.AsyncClient, token: str) -> tuple[int, list[str], str]:
    response = await _rpc(http, token, "tools/list", {})
    body = _rpc_body(response)
    tools = [str(t.get("name")) for t in ((body.get("result") or {}).get("tools") or [])]
    return response.status_code, tools, "" if tools else _reason(response)


async def _called(http: httpx.AsyncClient, token: str, name: str) -> tuple[int, bool, str]:
    """(status, whether the gateway ran it, its refusal): a JSON-RPC error or an ``isError`` result is a refusal."""
    response = await _rpc(http, token, "tools/call", {"name": name, "arguments": {"topic": "seckey"}})
    body = _rpc_body(response)
    result = body.get("result") or {}
    ran = response.status_code == 200 and "error" not in body and not result.get("isError")
    text = json.dumps(result.get("content") or [])[:200] if result.get("isError") else _reason(response)
    return response.status_code, ran, text


WIRES = (
    ("/v1/chat/completions", lambda m: {"model": m, "messages": [{"role": "user", "content": "say ok"}], "max_tokens": 16}),
    ("/v1/responses", lambda m: {"model": m, "input": "say ok", "max_output_tokens": 16}),
    ("/v1/messages", lambda m: {"model": m, "messages": [{"role": "user", "content": "say ok"}], "max_tokens": 16}),
    ("/v1beta/models/{model}:generateContent", lambda m: {"contents": [{"role": "user", "parts": [{"text": "say ok"}]}]}),
)
# A refusal that names the wire, not the token or the model: the run's engine speaks another one.
OTHER_WIRE = ("model_api_not_permitted", "permission_error")


async def _chat(http: httpx.AsyncClient, token: str, model: str) -> tuple[int, str]:
    """A model call on the first wire the run's engine may use (each engine has its own: a wire the run may not use is
    refused as ``model_api_not_permitted``, and the next is tried)."""
    status, reason = 0, ""
    for path, body in WIRES:
        # One credential header, as the engines send it (Claude Code's ANTHROPIC_AUTH_TOKEN is a bearer too): a second
        # one is refused.
        headers = {"Authorization": f"Bearer {token}", "anthropic-version": "2023-06-01"}
        response = await http.post(f"{MODELS}{path.format(model=model)}", headers=headers, json=body(model))
        status, reason = response.status_code, "" if response.status_code == 200 else _reason(response)
        if reason not in OTHER_WIRE:
            return status, reason
    return status, reason


async def _model_grant(http: httpx.AsyncClient, token: str, run_id: str) -> tuple[int, dict[str, Any]]:
    """Node's model grant as the Model Gateway reads it with a run's token: the routes and budgets (no credential)."""
    response = await http.get(f"{node_state_url()}{INTERNAL_PREFIX}/runs/{run_id}/model-grant",
                              headers={"Authorization": f"Bearer {token}"})
    return response.status_code, response.json() if response.status_code == 200 else {}


def _tampered(token: str) -> str:
    """The token with its claims changed (a later expiry) and its signature kept: what a forger would try."""
    head, claims, signature = token.split(".")
    body = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    body["exp"] = int(body.get("exp") or 0) + 86_400
    forged = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return f"{head}.{forged}.{signature}"


async def _mint(tokens: NodeGatewayTokens, org: str, run: str, attempt: str, proof: str) -> tuple[str | None, str]:
    try:
        issued = await tokens.for_attempt(org, run, attempt, proof)
    except GatewayTokenUnavailable as error:
        return None, error.reason
    return issued.value, ""


def _authority() -> NodeGatewayTokens:
    # As the egress proxy asks: only its role's credential mints a gateway token (SEC-C-10, ADR-CRED-01).
    return NodeGatewayTokens(base_url=node_state_url(),
                             credentials=ConfiguredWorkerCredential(HarnessServices().config_service(),
                                                                    key=ROLE_DOCUMENTS["egress-proxy"]))


def attest(body: dict[str, Any]) -> None:
    from app.harness.sandbox.binding_proof import BindingAttester
    from harness_core.engines.attempt_labels import ATTEMPT_LABEL, RUN_LABEL
    from harness_core.sandbox.egress.types import EgressBinding

    attester = BindingAttester.from_env()
    proofs: dict[str, str | None] = {}
    for name, ask in dict(body["bindings"]).items():
        labels = {"orgId": str(ask["orgId"]), RUN_LABEL: str(ask["runId"]), ATTEMPT_LABEL: str(ask["attemptId"])}
        proofs[name] = attester.attest(EgressBinding(f"seckey-{name}", "", "", labels=labels)).attestation
    print(json.dumps({"proofs": proofs}), flush=True)


def _forged_proof() -> str:
    """A proof of the broker's shape under no key the broker holds."""
    return "v1." + base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")


async def live(body: dict[str, Any]) -> None:
    org, child, parent = str(body["orgId"]), body["child"], body["parent"]
    proofs = {k: str(v or "") for k, v in dict(body.get("proofs") or {}).items()}
    other_org, stale_attempt = str(body["otherOrgId"]), str(body["staleAttemptId"])
    authority = _authority()
    say("attest.broker_signed", all(proofs.get(k) for k in ("child", "parent", "stale", "otherOrg")),
        signed=sorted(k for k, v in proofs.items() if v))
    child_token, why = await _mint(authority, org, child["runId"], child["attemptId"], proofs.get("child", ""))
    say("mint.child", child_token is not None, reason=why)
    parent_token, why = await _mint(authority, org, parent["runId"], parent["attemptId"], proofs.get("parent", ""))
    # A parent that waits on its child job is parked, not leased: then no token for it can be had at all.
    say("mint.parent_issued_or_parked", parent_token is not None or why == "RUN_NOT_LEASED", issued=parent_token is not None,
        reason=why)
    if child_token is None:
        return
    # R38IDENT-FU-7: the proxy's credential names no run by itself; only the broker's proof for exactly this org, run
    # and attempt does. Each of these is the child's own leased attempt, asked with a proof that is not that one.
    for check, proof in (("mint.missing_proof_refused", ""), ("mint.forged_proof_refused", _forged_proof()),
                         ("mint.other_attempt_proof_refused", proofs.get("stale", ""))):
        refused, why = await _mint(authority, org, child["runId"], child["attemptId"], proof)
        say(check, refused is None, reason=why)
    stale, why = await _mint(authority, org, child["runId"], stale_attempt, proofs.get("stale", ""))
    say("mint.stale_attempt_refused", stale is None, reason=why)
    other, why = await _mint(authority, other_org, child["runId"], child["attemptId"], proofs.get("otherOrg", ""))
    say("mint.other_org_refused", other is None, reason=why)
    other, why = await _mint(authority, other_org, child["runId"], child["attemptId"], proofs.get("child", ""))
    say("mint.child_proof_for_other_org_refused", other is None, reason=why)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        status, grant = await _model_grant(http, child_token, child["runId"])
        routes = [str(r.get("model")) for r in grant.get("routes") or []]
        say("grant.child_reads_its_own", status == 200 and bool(routes), status=status, routes=routes,
            budgets=grant.get("budgets"))
        # The engine names its model by the grant's neutral id (``engine-model``), never by the provider's own name: the
        # route's real model name is a model the token may not ask for by name.
        status, why = await _chat(http, child_token, str(body["allowedModel"]))
        say("model.allowed_answers", status == 200, model=body["allowedModel"], status=status, reason=why)
        status, why = await _chat(http, child_token, str(body["otherModel"]))
        say("model.other_refused", status >= 400, model=body["otherModel"], status=status, reason=why)
        forged = _tampered(child_token)
        status, why = await _chat(http, forged, str(body["allowedModel"]))
        say("model.tampered_refused", status in (401, 403), status=status, reason=why)
        status, _ = await _model_grant(http, child_token, parent["runId"])
        say("grant.child_token_for_parent_refused", status in (401, 403), status=status)
        if parent_token is not None:
            status, _ = await _model_grant(http, parent_token, child["runId"])
            say("grant.parent_token_for_child_refused", status in (401, 403), status=status)
    kept = _kept(str(body["marker"]))
    fd = os.open(kept, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"child": child_token, "parent": parent_token, "orgId": org, "childRun": child,
                   "childProof": proofs.get("child", ""), "parentRun": parent["runId"],
                   "allowedModel": body["allowedModel"]}, handle)


async def after(body: dict[str, Any]) -> None:
    kept = _kept(str(body["marker"]))
    held = json.loads(kept.read_text())
    kept.unlink()
    authority = _authority()
    child = held["childRun"]
    # With the broker's own proof for that attempt: refused because the run ended, not for want of a proof.
    again, why = await _mint(authority, held["orgId"], child["runId"], child["attemptId"], held["childProof"])
    say("after.mint_refused", again is None, reason=why)
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        for who in ("child", "parent"):
            token = held.get(who)
            if not token:
                continue
            status, why = await _chat(http, token, str(held["allowedModel"]))
            say(f"after.{who}.model_refused", status in (401, 403), status=status, reason=why)
            status, _ = await _model_grant(http, token, child["runId"] if who == "child" else str(held["parentRun"]))
            say(f"after.{who}.grant_refused", status in (401, 403, 409), status=status)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("attest", "live", "after"):
        raise SystemExit("usage: seckey_gateway.py attest|live|after < body.json")
    payload = json.loads(sys.stdin.read())
    if sys.argv[1] == "attest":
        attest(payload)
    else:
        asyncio.run(live(payload) if sys.argv[1] == "live" else after(payload))

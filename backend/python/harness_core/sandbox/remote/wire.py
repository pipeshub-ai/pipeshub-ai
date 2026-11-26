"""Sandbox broker HTTP API, version 1: paths, JSON shapes and the error table.

The API is product-neutral so any embedding application can run a client
against a broker process. Request and response bodies are the dataclasses
in ``harness_core.sandbox.types`` encoded with their field names.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from harness_core.sandbox.errors import ERROR_TYPES, SandboxError, SandboxRefusal
from harness_core.sandbox.types import BrokerHealth, LeaseRef, RuntimeMode, SandboxSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

API_VERSION = "v1"
PREFIX = f"/{API_VERSION}"

LIVENESS = "/healthz"
HEALTH = f"{PREFIX}/health"
MODES = f"{PREFIX}/modes"
SANDBOXES = f"{PREFIX}/sandboxes"
REAP = f"{PREFIX}/reap"
ERASE_SESSION = f"{PREFIX}/erase-session"
ERASE_ORG = f"{PREFIX}/erase-org"


def sandbox_path(sandbox_id: str, suffix: str = "") -> str:
    return f"{SANDBOXES}/{sandbox_id}{suffix}"


ARCHIVE = "/archive"
# Added after v1 shipped; additive, so a v1 client that never calls it is unaffected.
SEED = "/seed"
EXEC = "/exec"
# Added after v1 shipped (R32-SECCOMP); a broker without it answers 404, which a client reads as "cannot tell".
MEMORY = "/memory"
# Added by R49-SMALL (R38EGRESS-FU-1); a broker without it answers 404, which a client reads as "this broker cannot".
FLOW_HOLD = "/flow-hold"
CONNECT = "/connect"

# Exec stream frames: one channel byte, then the payload.
STDOUT = 1
STDERR = 2

# Control messages (JSON text frames) on the exec and connect websockets.
OP_STARTED = "started"
OP_CONNECTED = "connected"
OP_ERROR = "error"
OP_EOF = "eof"
OP_SIGNAL = "signal"
OP_EXIT = "exit"

SPEC = TypeAdapter(SandboxSpec)
LEASE = TypeAdapter(LeaseRef)
HEALTH_BODY = TypeAdapter(BrokerHealth)

JsonObject = dict[str, object]

_STATUS: Mapping[str, int] = {
    "invalid_spec": 400,
    "invalid_request": 400,
    "unauthorized": 401,
    "image_not_allowed": 403,
    "policy_unenforceable": 403,
    "policy_violation": 403,
    "not_found": 404,
    "path_not_found": 404,
    "lease_fenced": 409,
    "isolation_unavailable": 422,
    "refused": 422,
    "connect_failed": 502,
    "unavailable": 503,
}
_BY_CODE: Mapping[str, type[SandboxError]] = {cls.code: cls for cls in ERROR_TYPES}


def status_for(error: SandboxError) -> int:
    return _STATUS.get(error.code, 500)


def error_body(error: SandboxError) -> JsonObject:
    body: JsonObject = {"code": error.code, "message": str(error)}
    if isinstance(error, SandboxRefusal):
        body["reason"] = error.reason
    return {"error": body}


def error_from(body: object, *, status: int) -> SandboxError:
    """The typed error a server sent, or a generic one for a body that is not ours."""
    detail = cast("dict[str, object]", body).get("error") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return SandboxError(f"broker returned HTTP {status}")
    fields = cast("dict[str, object]", detail)
    code, message, reason = fields.get("code"), fields.get("message"), fields.get("reason")
    cls = _BY_CODE.get(code if isinstance(code, str) else "", SandboxError)
    text = message if isinstance(message, str) else f"broker returned HTTP {status}"
    if issubclass(cls, SandboxRefusal):
        return cls(text, reason=reason if isinstance(reason, str) else "unspecified")
    return cls(text)


def acquire_body(spec: SandboxSpec, lease: LeaseRef, idempotency_key: str) -> JsonObject:
    return {
        "spec": SPEC.dump_python(spec, mode="json"),
        "lease": LEASE.dump_python(lease, mode="json"),
        "idempotency_key": idempotency_key,
    }


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def acquired_body(sandbox_id: str, mode: RuntimeMode, verified_binaries: Mapping[str, str]) -> JsonObject:
    return {"id": sandbox_id, "mode": mode.value, "verifiedBinaries": dict(verified_binaries)}


def parse_verified_binaries(raw: object) -> dict[str, str]:
    """What the broker vouches for (EH2); an entry that is not an absolute path to a sha256 digest is dropped,
    so the caller hashes that file itself."""
    if not isinstance(raw, dict):
        return {}
    entries = cast("dict[object, object]", raw)
    return {
        path: digest
        for path, digest in entries.items()
        if isinstance(path, str) and path.startswith("/") and isinstance(digest, str) and _DIGEST.fullmatch(digest)
    }


def modes_body(modes: frozenset[RuntimeMode]) -> JsonObject:
    return {"modes": sorted(mode.value for mode in modes)}


def parse_modes(body: object) -> frozenset[RuntimeMode]:
    values = cast("dict[str, object]", body).get("modes") if isinstance(body, dict) else None
    if not isinstance(values, list):
        raise SandboxError("malformed modes response")
    return frozenset(RuntimeMode(str(v)) for v in cast("list[object]", values))


def control(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields})


def parse_control(text: str) -> JsonObject:
    try:
        message = json.loads(text)
    except ValueError as exc:
        raise SandboxError("malformed control message") from exc
    if not isinstance(message, dict):
        raise SandboxError("malformed control message")
    typed = cast("JsonObject", message)
    if not isinstance(typed.get("op"), str):
        raise SandboxError("malformed control message")
    return typed


__all__ = [
    "API_VERSION",
    "ARCHIVE",
    "CONNECT",
    "EXEC",
    "HEALTH",
    "HEALTH_BODY",
    "LEASE",
    "LIVENESS",
    "MODES",
    "OP_CONNECTED",
    "OP_EOF",
    "OP_ERROR",
    "OP_EXIT",
    "OP_SIGNAL",
    "OP_STARTED",
    "PREFIX",
    "REAP",
    "SANDBOXES",
    "SEED",
    "SPEC",
    "acquired_body",
    "parse_verified_binaries",
    "STDERR",
    "STDOUT",
    "acquire_body",
    "control",
    "error_body",
    "error_from",
    "modes_body",
    "parse_control",
    "parse_modes",
    "sandbox_path",
    "status_for",
]

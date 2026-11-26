"""The Python services' binding writes, through Node's binder (CPD-8).

Node is the only writer of bindings and their secrets: the credential migration, legacy credential
routes with ``CONTROL_PLANE_CREDENTIALS`` on and the token refresher hand records to its internal
binding routes under a service token of scope ``control-plane:bindings`` (the org is the token's).
``NodeBindingWriter`` is that client over the shared ``NodeServiceClient``; tests use
``tests/unit/harness/credentials/binding_support.FakeBindingWriter``, which keeps Node's semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, override

from app.core.org_context import org_of

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    import httpx
    from pydantic import JsonValue

BINDINGS_TOKEN_SCOPE = "control-plane:bindings"
INTERNAL_BINDINGS_PATH = "/api/v1/control-plane/internal/bindings"
ImportOutcome = Literal["created", "updated", "unchanged", "conflict"]


@dataclass(frozen=True, slots=True)
class Material:
    """A secret for the vault: ``record`` is a toolset or MCP credential record kept verbatim."""

    kind: Literal["api_key", "oauth_tokens", "header", "record"]
    value: Mapping[str, JsonValue]
    expires_at: int | None = None

    def as_json(self) -> dict[str, object]:
        body: dict[str, object] = {"kind": self.kind, "value": dict(self.value)}
        if self.expires_at is not None:
            body["expiresAt"] = self.expires_at
        return body

    @override
    def __repr__(self) -> str:
        return f"Material(kind={self.kind!r})"


@dataclass(frozen=True, slots=True)
class ImportRequest:
    installation: str
    owner: str
    mode: Literal["oauth", "api_key", "header", "dcr"]
    kind: str
    provider: str | None
    material: Material
    source_path: str | None
    source_digest: str | None
    # A legacy save only: the old path's record after the save (digest), which the read-through compares against.
    observed_path: str | None = None
    observed_digest: str | None = None

    def as_json(self) -> dict[str, object]:
        source = None if self.source_path is None else {"path": self.source_path, "digest": self.source_digest}
        observed = None if self.observed_path is None else {"path": self.observed_path, "digest": self.observed_digest}
        return {
            "observed": observed,
            "installation": self.installation,
            "owner": self.owner,
            "mode": self.mode,
            "kind": self.kind,
            "provider": self.provider,
            "material": self.material.as_json(),
            "source": source,
        }


def _org(row: Mapping[str, Any]) -> str:
    """The org a binding row from Node names; a row with none is malformed, as a missing field is."""
    org_id = org_of(row)
    if org_id is None:
        raise KeyError("orgId")
    return org_id


@dataclass(frozen=True, slots=True)
class BindingRow:
    """The binding's metadata as Node answers it (never a secret)."""

    id: str
    org_id: str
    status: str
    revision: int
    version: int | None
    path: str | None

    @classmethod
    def of(cls, body: Mapping[str, Any]) -> BindingRow:
        secret: object = body.get("secret")
        secret_map: Mapping[str, Any] = cast("Mapping[str, Any]", secret) if isinstance(secret, dict) else {}
        return cls(
            id=str(body["id"]),
            org_id=_org(body),
            status=str(body["status"]),
            revision=int(body["revision"]),
            version=int(secret_map["version"]) if "version" in secret_map else None,
            path=str(secret_map["path"]) if "path" in secret_map else None,
        )


@dataclass(frozen=True, slots=True)
class Imported:
    binding: BindingRow | None
    outcome: ImportOutcome


@dataclass(frozen=True, slots=True)
class Reverted:
    binding: BindingRow | None
    changed_since_import: bool


@dataclass(frozen=True, slots=True)
class DueBinding:
    id: str
    org_id: str
    installation: str
    owner: str
    mode: str
    revision: int
    expires_at: int | None
    path: str | None
    # False when another refresher replica holds it (QA23-F2): renewing now would spend its refresh token twice.
    claimed: bool = True

    @classmethod
    def of(cls, b: Mapping[str, Any]) -> DueBinding:
        return cls(
            id=str(b["id"]),
            org_id=_org(b),
            installation=str(b["installation"]),
            owner=str(b["owner"]),
            mode=str(b["mode"]),
            revision=int(b["revision"]),
            expires_at=None if b.get("expiresAt") is None else int(b["expiresAt"]),
            path=None if b.get("path") is None else str(b["path"]),
            claimed=bool(b.get("claimed", True)),
        )


class BindingConflict(RuntimeError):
    """The binding changed since it was read (another refresher, a revoke): nothing was written."""

    reason_code = "binding_conflict"


class BindingNotImported(RuntimeError):
    reason_code = "binding_not_imported"


class BindingWriteFailed(RuntimeError):
    reason_code = "binding_write_failed"


class BindingWriter(Protocol):
    async def import_record(self, org_id: str, request: ImportRequest, *, user_id: str | None = None) -> Imported: ...

    async def revert_import(self, org_id: str, installation: str, owner: str) -> Reverted: ...

    async def revoke_owner(
        self, org_id: str, installation: str, owner: str, *, user_id: str | None = None
    ) -> BindingRow | None: ...

    async def due(self, before_ms: int, limit: int, holder: str) -> list[DueBinding]:
        """The due bindings ``holder`` (a refresher replica) now holds until it writes or the claim lapses."""
        ...

    async def find_owner(self, org_id: str, installation: str, owner: str, holder: str) -> DueBinding | None: ...

    async def refreshed(self, org_id: str, binding: str, expected_revision: int, material: Material) -> BindingRow: ...

    async def refresh_failed(
        self, org_id: str, binding: str, expected_revision: int, code: str, *, permanent: bool
    ) -> BindingRow: ...


class NodeSend(Protocol):
    """``NodeServiceClient.send`` (WP-3): the one client the harness uses for Node."""

    async def send(
        self,
        method: str,
        path: str,
        *,
        bearer: str | None,
        json: object = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response: ...


class NodeBindingWriter:
    """``BindingWriter`` over Node's internal binding routes; one attempt per call (callers own retry)."""

    def __init__(self, node: NodeSend, token: Callable[[str | None, str | None], Awaitable[str]]) -> None:
        self._node = node
        self._token = token

    async def _call(
        self,
        method: str,
        path: str,
        *,
        org_id: str | None,
        user_id: str | None = None,
        body: object = None,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any]:
        response = await self._node.send(
            method,
            f"{INTERNAL_BINDINGS_PATH}{path}",
            bearer=await self._token(org_id, user_id),
            json=body,
            params=params,
        )
        if response.status_code == 409:
            code = str(cast("dict[str, Any]", response.json()).get("error", {}).get("code", ""))
            raise BindingNotImported(path) if "NOT_IMPORTED" in code else BindingConflict(path)
        if response.status_code != 200:
            raise BindingWriteFailed(f"{method} {path}: {response.status_code}")
        return cast("dict[str, Any]", response.json())

    @staticmethod
    def _row(value: object) -> BindingRow | None:
        return BindingRow.of(cast("dict[str, Any]", value)) if isinstance(value, dict) else None

    async def import_record(self, org_id: str, request: ImportRequest, *, user_id: str | None = None) -> Imported:
        answer = await self._call("POST", "/import", org_id=org_id, user_id=user_id, body=request.as_json())
        return Imported(self._row(answer.get("binding")), cast("ImportOutcome", answer["outcome"]))

    async def revert_import(self, org_id: str, installation: str, owner: str) -> Reverted:
        answer = await self._call(
            "POST", "/import/revert", org_id=org_id, body={"installation": installation, "owner": owner}
        )
        return Reverted(self._row(answer.get("binding")), bool(answer.get("changedSinceImport")))

    async def revoke_owner(
        self, org_id: str, installation: str, owner: str, *, user_id: str | None = None
    ) -> BindingRow | None:
        answer = await self._call(
            "POST", "/revoke", org_id=org_id, user_id=user_id, body={"installation": installation, "owner": owner}
        )
        return self._row(answer.get("binding"))

    async def due(self, before_ms: int, limit: int, holder: str) -> list[DueBinding]:
        answer = await self._call(
            "GET", "/due", org_id=None, params={"before": before_ms, "limit": limit, "holder": holder}
        )
        return [DueBinding.of(b) for b in cast("list[dict[str, Any]]", answer.get("bindings", []))]

    async def find_owner(self, org_id: str, installation: str, owner: str, holder: str) -> DueBinding | None:
        answer = await self._call(
            "GET", "/owner", org_id=org_id, params={"installation": installation, "owner": owner, "holder": holder}
        )
        found = answer.get("binding")
        return DueBinding.of(cast("dict[str, Any]", found)) if isinstance(found, dict) else None

    async def refreshed(self, org_id: str, binding: str, expected_revision: int, material: Material) -> BindingRow:
        answer = await self._call(
            "POST",
            f"/{binding}/refreshed",
            org_id=org_id,
            body={"expectedRevision": expected_revision, "material": material.as_json()},
        )
        return BindingRow.of(cast("dict[str, Any]", answer["binding"]))

    async def refresh_failed(
        self, org_id: str, binding: str, expected_revision: int, code: str, *, permanent: bool
    ) -> BindingRow:
        answer = await self._call(
            "POST",
            f"/{binding}/refresh-failed",
            org_id=org_id,
            body={"expectedRevision": expected_revision, "code": code, "permanent": permanent},
        )
        return BindingRow.of(cast("dict[str, Any]", answer["binding"]))


def service_token_source(config: Any) -> Callable[[str | None, str | None], Awaitable[str]]:  # noqa: ANN401
    """Mints the service token of the binding routes: the org (and the acting user, for audit) as claims."""

    async def mint(org_id: str | None, user_id: str | None) -> str:
        from app.utils import jwt

        claims: dict[str, object] = {
            "scopes": [BINDINGS_TOKEN_SCOPE],
            **({"orgId": org_id} if org_id is not None else {}),
            **({"userId": user_id} if user_id is not None else {}),
        }
        # Its payload parameter is an untyped dict.
        generate = cast("Callable[[Any, dict[str, object]], Awaitable[str]]", jwt.generate_jwt)  # pyright: ignore[reportUnknownMemberType]
        return await generate(config, claims)

    return mint


async def node_binding_writer(config: Any, *, timeout_s: float = 10.0) -> NodeBindingWriter:  # noqa: ANN401
    """The deployment's writer: Node's main listener (the ``cm`` endpoint) through the shared client."""
    from app.config.constants.service import DefaultEndpoints, config_node_constants
    from app.harness.journal_node.http import NodeServiceClient

    endpoints: object = await config.get_config(config_node_constants.ENDPOINTS.value, raise_on_error=True)
    cm: object = cast("dict[str, Any]", endpoints).get("cm") if isinstance(endpoints, dict) else None
    configured: object = cast("dict[str, Any]", cm).get("endpoint") if isinstance(cm, dict) else None
    base = configured if isinstance(configured, str) and configured else DefaultEndpoints.NODEJS_ENDPOINT.value
    client = NodeServiceClient(base_url=str(base).rstrip("/"), timeout_s=timeout_s, service_name="node.bindings")
    return NodeBindingWriter(client, service_token_source(config))


__all__ = [
    "BINDINGS_TOKEN_SCOPE",
    "BindingConflict",
    "BindingNotImported",
    "BindingRow",
    "BindingWriteFailed",
    "BindingWriter",
    "DueBinding",
    "ImportRequest",
    "Imported",
    "Material",
    "NodeBindingWriter",
    "NodeSend",
    "Reverted",
    "node_binding_writer",
    "service_token_source",
]

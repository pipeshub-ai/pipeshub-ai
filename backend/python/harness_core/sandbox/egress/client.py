"""``EgressBindings`` over the proxy's control plane: what a broker in another process uses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import aiohttp

from harness_core.sandbox.egress import wire
from harness_core.sandbox.errors import BrokerUnavailable, SandboxError
from harness_core.sandbox.remote.wire import error_from

if TYPE_CHECKING:
    from harness_core.sandbox.auth import BrokerAuth
    from harness_core.sandbox.egress.bindings import Revocation
    from harness_core.sandbox.egress.types import EgressBinding

CONTROL_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class EgressProxyHealth:
    ok: bool
    instance: str
    bindings: int
    kill_switch: bool = False
    egress_stops: int = 0


class RemoteEgressBindings:
    def __init__(self, base_url: str, auth: BrokerAuth, *, session: aiohttp.ClientSession | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._auth = auth
        self._session = session
        self._owns_session = session is None

    @property
    def endpoint(self) -> str:
        return self._base

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=CONTROL_TIMEOUT_S), trust_env=False)
        return self._session

    async def _call(self, method: str, path: str, *, json: object = None) -> object:
        try:
            async with self._http().request(
                method, self._base + path, json=json, headers=dict(await self._auth.headers())
            ) as response:
                body: object = await response.json(content_type=None) if response.status != 204 else None
                if response.status >= 400:
                    raise error_from(body, status=response.status)
                return body
        except SandboxError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as exc:
            raise BrokerUnavailable(f"egress proxy unreachable: {type(exc).__name__}") from exc

    async def register(self, binding: EgressBinding) -> None:
        _ = await self._call(
            "PUT", wire.binding_path(binding.sandbox_id), json=wire.BINDING.dump_python(binding, mode="json")
        )

    async def unregister(self, sandbox_id: str) -> None:
        _ = await self._call("DELETE", wire.binding_path(sandbox_id))

    async def revoke(self, revocation: Revocation) -> tuple[str, ...]:
        scope = {"orgId": revocation.org_id, "sessionId": revocation.session_id}
        body = await self._call("POST", wire.REVOCATIONS, json={k: v for k, v in scope.items() if v is not None})
        ids = cast("dict[str, object]", body).get("sandboxIds") if isinstance(body, dict) else None
        if not isinstance(ids, list):
            raise SandboxError("malformed revocations response")
        return tuple(str(i) for i in cast("list[object]", ids))

    async def bound(self) -> frozenset[str]:
        body = await self._call("GET", wire.BINDINGS)
        ids = cast("dict[str, object]", body).get("sandboxIds") if isinstance(body, dict) else None
        if not isinstance(ids, list):
            raise SandboxError("malformed bindings response")
        return frozenset(str(i) for i in cast("list[object]", ids))

    async def charge_volume(self, *, org_id: str, run_id: str, received: int) -> tuple[bool, int | None]:
        """Counts ``received`` bytes against the run's egress cap (WP-63 part A); whether the run may go on, and what
        it may still move (``None``: no cap)."""
        body = await self._call("POST", wire.VOLUME_CHARGES, json={"orgId": org_id, "runId": run_id, "bytes": received})
        fields = cast("dict[str, object]", body) if isinstance(body, dict) else {}
        allowed, remaining = fields.get("allowed"), fields.get("remaining")
        if not isinstance(allowed, bool) or not (remaining is None or type(remaining) is int):
            raise SandboxError("malformed volume charge response")
        return allowed, cast("int | None", remaining)

    async def grant_push(
        self, *, sandbox_id: str, placeholder: str, host: str, repository: str, branch: str, credential_ref: str,
        ttl_s: int,
    ) -> None:
        """Makes ``placeholder`` good for one push from ``sandbox_id`` to ``repository``'s ``branch`` (GIT-FLOW)."""
        _ = await self._call(
            "POST",
            wire.PUSH_GRANTS,
            json={
                "sandboxId": sandbox_id, "placeholder": placeholder, "host": host, "repository": repository,
                "branch": branch, "credentialRef": credential_ref, "ttlS": ttl_s,
            },
        )

    async def grant_site(self, *, sandbox_id: str, host: str, run_id: str) -> None:
        """Releases the sandbox's held website at ``host`` for the run it serves (the site card)."""
        _ = await self._call("POST", wire.SITE_GRANTS, json={"sandboxId": sandbox_id, "host": host, "runId": run_id})

    async def hold_flow(self, *, sandbox_id: str, run_id: str, hold: str) -> None:
        """SEC-A-02: narrows the live sandbox's websites to what the run's conversation holds now (``held``: every
        request that could carry what it read waits for the person; ``forbidden``: no website). Narrows only."""
        _ = await self._call("POST", wire.FLOW_HOLDS, json={"sandboxId": sandbox_id, "runId": run_id, "hold": hold})

    async def health(self) -> EgressProxyHealth:
        body = await self._call("GET", wire.HEALTH)
        fields = cast("dict[str, object]", body) if isinstance(body, dict) else {}
        count, stops = fields.get("bindings"), fields.get("egressStops")
        return EgressProxyHealth(
            ok=fields.get("ok") is True,
            instance=str(fields.get("instance", "")),
            bindings=count if isinstance(count, int) else 0,
            kill_switch=fields.get("killSwitch") is True,
            egress_stops=stops if type(stops) is int else 0,
        )


__all__ = ["EgressProxyHealth", "RemoteEgressBindings"]

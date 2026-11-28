"""The proxy's state that outlives the process (P5.4b): sandbox bindings, token grants, the instance id.

``DurableEgressBindings`` is the proxy's binding table with write-through to an
``EgressStateStore``. A starting proxy calls ``load`` before its data plane
listens, so a restart has no window in which a live sandbox is unknown. Every
record is sealed (AES-GCM under a key derived from a deployment secret, the
record's key as associated data): the store never holds a token in clear, and
a record that does not open, or names another key's sandbox, is never trusted.
What cannot be verified is refused as an unknown sandbox would be, and the
instance id changes, so the brokers that really hold a sandbox register it again.

Needs the ``sealing`` extra (``cryptography``).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast, override

from cryptography.exceptions import InvalidTag

from harness_core.logs import HarnessLog
from harness_core.sandbox.egress import wire
from harness_core.sandbox.egress.aead_ring import AeadRing
from harness_core.sandbox.egress.bindings import (
    EVERY_RUN,
    InMemoryEgressBindings,
    RestoredGrant,
    Revocation,
    rebound,
)
from harness_core.sandbox.egress.types import IssuedToken
from harness_core.sandbox.egress.volume import VOLUME_PREFIX
from harness_core.sandbox.errors import BrokerUnavailable, InvalidSandboxSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from harness_core.clock import Clock
    from harness_core.sandbox.egress.ports import EgressStateStore
    from harness_core.sandbox.egress.types import EgressBinding

# A record outlives a proxy outage of this long; a held binding is re-written every quarter of it.
DEFAULT_BINDING_TTL_S = 24 * 3600
INSTANCE_KEY = "instance"
# The signing certificate for intercepted TLS, shared by every replica (``interception.load_authority``).
AUTHORITY_KEY = "authority"
BINDING_PREFIX = "binding/"
GRANT_PREFIX = "grant/"
# A sandbox an incident stop revoked: kept as long as a binding record, so a broker's resync after a restart cannot
# bind it again.
REVOKED_PREFIX = "revoked/"
_SEAL_VERSION = "v1"
_NONCE_BYTES = 12
_KEY_LABEL = b"harness.egress.state.v1"
# Node derives the same key for the egress proxy (ADR-KEY-02); keep the two in step.
STATE_KEY_LABEL = _KEY_LABEL
_LOG = HarnessLog.named("harness_core.sandbox.egress.state")


class StateUnverifiable(Exception):
    """A stored record that cannot be trusted; ``reason`` names why (never its content)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class InMemoryEgressStateStore:
    """``EgressStateStore`` in a dict, with TTL on the injected clock (tests, Loom, the fake broker)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._entries: dict[str, tuple[str, datetime | None]] = {}

    def _purge(self) -> None:
        now = self._clock.now()
        for key in [k for k, (_, expires) in self._entries.items() if expires is not None and expires <= now]:
            del self._entries[key]

    async def put(self, key: str, value: str, *, ttl_s: int | None) -> None:
        expires = self._clock.now() + timedelta(seconds=ttl_s) if ttl_s is not None else None
        self._entries[key] = (value, expires)

    async def put_if_absent(self, key: str, value: str) -> str:
        self._purge()
        held = self._entries.setdefault(key, (value, None))
        return held[0]

    async def delete(self, key: str) -> None:
        _ = self._entries.pop(key, None)

    async def replace(self, key: str, expected: str, value: str) -> bool:
        self._purge()
        held = self._entries.get(key)
        if held is None or held[0] != expected:
            return False
        self._entries[key] = (value, held[1])
        return True

    async def entries(self) -> tuple[tuple[str, str], ...]:
        self._purge()
        return tuple(sorted((key, value) for key, (value, _) in self._entries.items()))


class StateSealer:
    """AES-256-GCM per record; the record's key is the associated data, so a record cannot change keys.

    ``accepted`` is the key ring's other secret (ADR-KEY-01): records sealed under it still open, and nothing is
    sealed under it."""

    def __init__(
        self, secret: bytes, *, accepted: Sequence[bytes] = (), nonce: Callable[[int], bytes] = os.urandom
    ) -> None:
        if not secret:
            raise ValueError("state secret is empty")
        self._aead = AeadRing((secret, *accepted), _KEY_LABEL)
        self._nonce = nonce

    @classmethod
    def of_keys(cls, keys: Sequence[bytes], *, nonce: Callable[[int], bytes] = os.urandom) -> StateSealer:
        """A sealer over keys already derived from the ring (``LABEL``), the writer's first (ADR-KEY-02)."""
        sealer = cls.__new__(cls)
        sealer._aead = AeadRing.of_keys(keys)
        sealer._nonce = nonce
        return sealer

    def seal(self, key: str, plaintext: bytes) -> str:
        nonce = self._nonce(_NONCE_BYTES)
        sealed = nonce + self._aead.encrypt(nonce, plaintext, key.encode("utf-8"))
        return f"{_SEAL_VERSION}.{base64.urlsafe_b64encode(sealed).decode('ascii')}"

    def open(self, key: str, value: str) -> bytes:
        return self.open_detailed(key, value)[0]

    def open_detailed(self, key: str, value: str) -> tuple[bytes, bool]:
        """The record, and whether the current key sealed it (False: the ring's other key did)."""
        version, _, body = value.partition(".")
        if version != _SEAL_VERSION or not body:
            raise StateUnverifiable("unknown_format")
        try:
            raw = base64.urlsafe_b64decode(body.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise StateUnverifiable("unknown_format") from exc
        if len(raw) <= _NONCE_BYTES:
            raise StateUnverifiable("unknown_format")
        try:
            return self._aead.decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], key.encode("utf-8"))
        except InvalidTag as exc:
            raise StateUnverifiable("seal_mismatch") from exc


def binding_key(sandbox_id: str) -> str:
    return BINDING_PREFIX + sandbox_id


def revoked_key(sandbox_id: str) -> str:
    return REVOKED_PREFIX + sandbox_id


def grant_key(sandbox_id: str, credential_ref: str) -> str:
    # The ref is the deployment's credential name; only its digest goes into a key.
    return f"{GRANT_PREFIX}{sandbox_id}/{hashlib.sha256(credential_ref.encode('utf-8')).hexdigest()[:24]}"


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _at(ms: object) -> datetime:
    if not isinstance(ms, int):
        raise StateUnverifiable("malformed")
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


@dataclass(frozen=True, slots=True)
class StateLoad:
    bindings: int
    grants: int
    unverified: int
    expired: int
    instance: str
    previous_instance: str | None


@dataclass(frozen=True, slots=True)
class _Opened:
    key: str
    saved_at: datetime
    expires_at: datetime
    body: dict[str, object]


class DurableEgressBindings(InMemoryEgressBindings):
    """The binding table, written through to ``store``; ``load`` restores it after a restart."""

    def __init__(
        self,
        store: EgressStateStore,
        sealer: StateSealer,
        clock: Clock,
        *,
        ttl_s: int = DEFAULT_BINDING_TTL_S,
        kill_switch: bool = False,
    ) -> None:
        super().__init__(kill_switch=kill_switch)
        self._store = store
        self._sealer = sealer
        self._clock = clock
        self.ttl_s = ttl_s
        self._lock = asyncio.Lock()
        self._grants: tuple[RestoredGrant, ...] = ()

    async def load(self, proposed_instance: str) -> StateLoad:
        """Restore what the store holds (call before serving); returns the instance id to serve under."""
        opened: list[_Opened] = []
        unverified = expired = 0
        stored_instance: str | None = None
        now = self._clock.now()
        for key, value in await self._store.entries():
            if key == INSTANCE_KEY:
                stored_instance = value
                continue
            # The volume records (C6.12) are ``EgressVolume.load``'s.
            if key == AUTHORITY_KEY or key.startswith(VOLUME_PREFIX):
                continue
            try:
                record = self._open(key, value)
            except StateUnverifiable as exc:
                unverified += 1
                _LOG.warning("egress.state_unverified", kind=_kind(key), reason=exc.reason)
                await self._drop_record(key)
                continue
            if record.expires_at <= now:
                expired += 1
                await self._drop_record(key)
                continue
            opened.append(record)
        self._restore_revoked(opened)
        restored, rejected, replaced = self._restore_bindings(opened)
        unverified += rejected
        for key in replaced:
            await self._drop_record(key)
        grants = self._restore_grants(opened)
        unproven = _unproven(opened)
        if unproven:
            # R41OPS-FU-6: written by a proxy from before the broker's proof (ADR-CRED-01 D6), so Node would refuse
            # its run's token. A new instance id makes every broker register its sandboxes again, signed.
            _LOG.info("egress.bindings_unproven", count=unproven)
        if unverified or unproven or stored_instance is None:
            instance = proposed_instance
            await self._store.put(INSTANCE_KEY, instance, ttl_s=None)
        else:
            instance = await self._store.put_if_absent(INSTANCE_KEY, proposed_instance)
        await self.renew()
        _LOG.info(
            "egress.state_restored",
            bindings=restored,
            grants=len(grants),
            unverified=unverified,
            expired=expired,
            instance=instance,
            previous=stored_instance,
        )
        return StateLoad(restored, len(grants), unverified, expired, instance, stored_instance)

    def _open(self, key: str, value: str) -> _Opened:
        if not key.startswith((BINDING_PREFIX, GRANT_PREFIX, REVOKED_PREFIX)):
            raise StateUnverifiable("unknown_key")
        try:
            parsed: object = json.loads(self._sealer.open(key, value))
        except ValueError as exc:
            raise StateUnverifiable("malformed") from exc
        if not isinstance(parsed, dict):
            raise StateUnverifiable("malformed")
        body = cast("dict[str, object]", parsed)
        return _Opened(key, _at(body.get("savedAt")), _at(body.get("expiresAt")), body)

    def _restore_bindings(self, opened: list[_Opened]) -> tuple[int, int, list[str]]:
        rejected = 0
        replaced: list[str] = []
        # Oldest first: on a shared proxy address the newer binding replaces the older (a lost unregister).
        for record in sorted((r for r in opened if r.key.startswith(BINDING_PREFIX)), key=lambda r: r.saved_at):
            try:
                binding = wire.BINDING.validate_python(record.body.get("binding"))
            except ValueError:
                rejected += 1
                _LOG.warning("egress.state_unverified", kind="binding", reason="malformed")
                continue
            if binding_key(binding.sandbox_id) != record.key:
                rejected += 1
                _LOG.warning("egress.state_unverified", kind="binding", reason="key_mismatch")
                continue
            if (
                binding.sandbox_id in self.revoked()
                or (self.kill_switch and EVERY_RUN.covers(binding))
                or any(stop.covers(binding) for stop in self.stops())
            ):
                # Not restored, and its records go: what a stop ended stays ended across a restart.
                replaced.append(record.key)
                _LOG.info("egress.binding_not_restored", sandboxId=binding.sandbox_id, reason="revoked")
                continue
            try:
                self.check(binding)
            except InvalidSandboxSpec:
                rejected += 1
                _LOG.warning("egress.state_unverified", kind="binding", reason="placeholder_in_use", sandboxId=binding.sandbox_id)
                continue
            stale = self.apply(binding)
            if stale is not None:
                replaced.append(binding_key(stale))
        return len(self.held()), rejected, replaced

    def _restore_revoked(self, opened: list[_Opened]) -> None:
        for record in opened:
            if not record.key.startswith(REVOKED_PREFIX):
                continue
            sandbox_id = record.body.get("sandboxId")
            if isinstance(sandbox_id, str) and revoked_key(sandbox_id) == record.key:
                self.mark_revoked(sandbox_id)

    def _restore_grants(self, opened: list[_Opened]) -> tuple[RestoredGrant, ...]:
        grants: list[RestoredGrant] = []
        for record in opened:
            if not record.key.startswith(GRANT_PREFIX):
                continue
            body = record.body
            sandbox_id, ref, token = body.get("sandboxId"), body.get("credentialRef"), body.get("token")
            if not (isinstance(sandbox_id, str) and isinstance(ref, str) and isinstance(token, str)):
                continue
            binding = self.get(sandbox_id)
            if binding is None or grant_key(sandbox_id, ref) != record.key:
                continue
            if ref not in {route.credential_ref for route in binding.policy.token_routes}:
                continue
            grants.append(RestoredGrant(sandbox_id, ref, IssuedToken(token, _at(body.get("expiresAt")))))
        self._grants = tuple(grants)
        return self._grants

    @override
    def restored_grants(self) -> tuple[RestoredGrant, ...]:
        return self._grants

    @override
    async def register(self, binding: EgressBinding) -> None:
        async with self._lock:
            self.check(binding)
            await self._save_binding(binding)
            stale_id = self._stale_on(binding)
            stale = self.get(stale_id) if stale_id is not None else None
            previous = self.get(binding.sandbox_id)
            _ = self.apply(binding)
            if stale is not None:
                await self._delete_records(stale)
            if previous is not None and rebound(previous, binding):
                # A restart must not restore the previous attempt's token as this binding's.
                await self._delete_grants(previous)

    def _stale_on(self, binding: EgressBinding) -> str | None:
        current = self.for_local_address(binding.proxy_address)
        return current.sandbox_id if current is not None and current.sandbox_id != binding.sandbox_id else None

    @override
    async def unregister(self, sandbox_id: str) -> None:
        async with self._lock:
            binding = self.get(sandbox_id)
            # Egress stops at once; the records follow (a failed delete expires with its TTL).
            self.discard(sandbox_id)
            if binding is not None:
                await self._delete_records(binding)

    @override
    async def revoke(self, revocation: Revocation) -> tuple[str, ...]:
        async with self._lock:
            covered = [b for b in self.held() if revocation.covers(b)]
            revoked = self.revoke_now(revocation)
            now = self._clock.now()
            for binding in covered:
                key = revoked_key(binding.sandbox_id)
                body = {
                    "sandboxId": binding.sandbox_id,
                    "savedAt": _ms(now),
                    "expiresAt": _ms(now + timedelta(seconds=self.ttl_s)),
                }
                try:
                    await self._store.put(key, self._seal(key, body), ttl_s=self.ttl_s)
                except Exception as exc:
                    # Egress already ended; a restart before the record is written lets a resync bind it again.
                    _LOG.warning(
                        "egress.state_write_failed", kind="revoked", sandboxId=binding.sandbox_id, errorType=type(exc).__name__
                    )
                await self._delete_records(binding)
            return revoked

    @override
    async def record_grant(self, sandbox_id: str, credential_ref: str, token: IssuedToken) -> None:
        if self.get(sandbox_id) is None:
            return
        key = grant_key(sandbox_id, credential_ref)
        ttl_s = int((token.expires_at - self._clock.now()).total_seconds())
        if ttl_s <= 0:
            return
        body = {
            "sandboxId": sandbox_id,
            "credentialRef": credential_ref,
            "token": token.value,
            "savedAt": _ms(self._clock.now()),
            "expiresAt": _ms(token.expires_at),
        }
        try:
            await self._store.put(key, self._seal(key, body), ttl_s=ttl_s)
        except Exception as exc:
            # The token is still good; a restart before it expires asks the source again.
            _LOG.warning("egress.state_write_failed", kind="grant", sandboxId=sandbox_id, errorType=type(exc).__name__)

    async def renew(self) -> int:
        """Re-write every held binding with a fresh TTL; how many were written."""
        written = 0
        for binding in self.held():
            try:
                await self._put_binding(binding)
            except Exception as exc:
                _LOG.warning("egress.state_write_failed", kind="renew", sandboxId=binding.sandbox_id, errorType=type(exc).__name__)
                continue
            written += 1
        return written

    async def _save_binding(self, binding: EgressBinding) -> None:
        try:
            await self._put_binding(binding)
        except Exception as exc:
            _LOG.warning("egress.state_write_failed", kind="binding", sandboxId=binding.sandbox_id, errorType=type(exc).__name__)
            raise BrokerUnavailable("the egress proxy cannot persist the binding") from exc

    async def _put_binding(self, binding: EgressBinding) -> None:
        now = self._clock.now()
        key = binding_key(binding.sandbox_id)
        body = {
            "binding": wire.BINDING.dump_python(binding, mode="json"),
            "savedAt": _ms(now),
            "expiresAt": _ms(now + timedelta(seconds=self.ttl_s)),
        }
        await self._store.put(key, self._seal(key, body), ttl_s=self.ttl_s)

    def _seal(self, key: str, body: Mapping[str, object]) -> str:
        return self._sealer.seal(key, json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))

    async def _delete_records(self, binding: EgressBinding) -> None:
        await self._delete_keys(binding, [binding_key(binding.sandbox_id), *self._grant_keys(binding)])

    async def _delete_grants(self, binding: EgressBinding) -> None:
        await self._delete_keys(binding, self._grant_keys(binding))

    @staticmethod
    def _grant_keys(binding: EgressBinding) -> list[str]:
        return [grant_key(binding.sandbox_id, route.credential_ref) for route in binding.policy.token_routes]

    async def _delete_keys(self, binding: EgressBinding, keys: list[str]) -> None:
        for key in keys:
            try:
                await self._store.delete(key)
            except Exception as exc:
                _LOG.warning(
                    "egress.state_write_failed", kind="delete", sandboxId=binding.sandbox_id, errorType=type(exc).__name__
                )

    async def _drop_record(self, key: str) -> None:
        with contextlib.suppress(Exception):
            await self._store.delete(key)


def _unproven(opened: Sequence[_Opened]) -> int:
    """Binding records with no ``attestation`` key at all: only a proxy from before D6 wrote them that way (a record
    written since always names it, ``null`` where the broker holds no key)."""
    return sum(
        1
        for record in opened
        if record.key.startswith(BINDING_PREFIX)
        and isinstance(binding := record.body.get("binding"), dict)
        and "attestation" not in binding
    )


def _kind(key: str) -> str:
    if key.startswith(BINDING_PREFIX):
        return "binding"
    if key.startswith(REVOKED_PREFIX):
        return "revoked"
    return "grant" if key.startswith(GRANT_PREFIX) else "unknown"


async def keep_state(bindings: DurableEgressBindings, stopped: asyncio.Event) -> None:
    """Renew held bindings every quarter of their TTL until ``stopped``."""
    interval = max(bindings.ttl_s / 4, 1.0)
    while not stopped.is_set():
        with contextlib.suppress(TimeoutError):
            _ = await asyncio.wait_for(stopped.wait(), interval)
        if not stopped.is_set():
            _ = await bindings.renew()


__all__ = [
    "AUTHORITY_KEY",
    "BINDING_PREFIX",
    "DEFAULT_BINDING_TTL_S",
    "GRANT_PREFIX",
    "INSTANCE_KEY",
    "REVOKED_PREFIX",
    "STATE_KEY_LABEL",
    "DurableEgressBindings",
    "InMemoryEgressStateStore",
    "StateLoad",
    "StateSealer",
    "StateUnverifiable",
    "binding_key",
    "grant_key",
    "keep_state",
    "revoked_key",
]

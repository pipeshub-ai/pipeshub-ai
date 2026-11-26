"""``python -m app.harness.sandbox.egress_rekey [--apply]``: the egress proxy's step of the ``SECRET_KEY`` rotation.

ADR-KEY-01. Node's ``rotate-secret-key`` command runs this in the same environment (``SECRET_KEY`` the new key,
``SECRET_KEY_PREVIOUS`` the old one). Without ``--apply`` it counts the proxy's durable records by the key that
sealed them; with it, each record sealed under the previous key is sealed again under the current one with a
compare-and-set that keeps the record's expiry, so a record a running proxy rewrote meanwhile is left as the proxy
wrote it. Nothing is checkpointed: a second run finds what is left. A sealed credential inside a binding's route is
the broker's, which re-registers it as it holds it: it is counted as draining, not rewritten, and ends with its
sandbox. Prints one JSON line of counts, never a value or a key.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING, TextIO, cast

from app.utils.encryption.rotation_step import (
    RekeyReport,
    describe_error,
    report_step,
    run_cli,
)
from app.utils.encryption.rotation_step import parse_apply as _parse_apply
from harness_core.sandbox.egress import wire
from harness_core.sandbox.egress.errors import TokenUnavailable
from harness_core.sandbox.egress.sealed import is_sealed
from harness_core.sandbox.egress.state import (
    BINDING_PREFIX,
    INSTANCE_KEY,
    StateUnverifiable,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from harness_core.sandbox.egress.ports import EgressStateStore
    from harness_core.sandbox.egress.sealed import CredentialSealer
    from harness_core.sandbox.egress.state import StateSealer

STEP = "egress-state"


async def rekey(
    store: EgressStateStore, sealer: StateSealer, credentials: CredentialSealer, *, apply: bool
) -> RekeyReport:
    report = RekeyReport(STEP)
    for key, value in await store.entries():
        if key == INSTANCE_KEY:
            # The proxy's instance id is not sealed.
            continue
        report.total += 1
        try:
            plaintext, current = sealer.open_detailed(key, value)
        except StateUnverifiable:
            report.unreadable += 1
            continue
        if key.startswith(BINDING_PREFIX):
            report.draining += _draining_routes(plaintext, credentials)
        if current:
            report.current += 1
        elif apply and await store.replace(key, value, sealer.seal(key, plaintext)):
            report.moved += 1
            report.current += 1
        else:
            # Not applied, or rewritten since it was read: the final check reads it again.
            report.previous += 1
    return report


def _draining_routes(plaintext: bytes, credentials: CredentialSealer) -> int:
    """Sealed credentials in a binding's routes that only the previous key opens (or none does)."""
    try:
        body = cast("dict[str, object]", json.loads(plaintext))
        binding = wire.BINDING.validate_python(body.get("binding"))
    except (ValueError, AttributeError):
        # The proxy's own load drops such a record; it holds no credential anyone can use.
        return 0
    draining = 0
    for route in binding.policy.token_routes:
        if not is_sealed(route.credential_ref):
            continue
        try:
            _, current = credentials.open_detailed(route.placeholder, route.credential_ref)
        except TokenUnavailable:
            current = False
        draining += 0 if current else 1
    return draining


async def deployment_state(logger: logging.Logger) -> tuple[EgressStateStore, StateSealer, CredentialSealer]:
    from app.config.configuration_service import ConfigurationService
    from app.config.providers.encrypted_store import EncryptedKeyValueStore
    from app.harness.sandbox.egress_credentials import required_sealer
    from app.harness.sandbox.egress_state import redis_state_store, state_sealer

    config = ConfigurationService(logger=logger, key_value_store=EncryptedKeyValueStore(logger=logger))
    store = await redis_state_store(config, required=True)
    sealer = state_sealer()
    if store is None or sealer is None:  # pragma: no cover - required=True raises first
        raise RuntimeError("the egress proxy's state store is unavailable")
    return store, sealer, required_sealer()


def parse_apply(argv: Sequence[str]) -> bool:
    return _parse_apply("python -m app.harness.sandbox.egress_rekey", __doc__, argv)


async def main(
    argv: Sequence[str],
    build: Callable[[logging.Logger], Awaitable[tuple[EgressStateStore, StateSealer, CredentialSealer]]] = deployment_state,
    out: TextIO | None = None,
) -> int:
    apply = parse_apply(argv)

    async def work() -> RekeyReport:
        store, sealer, credentials = await build(logging.getLogger("app.harness.sandbox.egress_rekey"))
        return await rekey(store, sealer, credentials, apply=apply)

    return await report_step(STEP, work, sys.stdout if out is None else out, _own_error)


def _own_error(exc: Exception) -> bool:
    from app.harness.sandbox.egress_credentials import CredentialSealingUnavailable
    from app.harness.sandbox.egress_state import EgressStateUnavailable
    from app.utils.encryption.secret_key_ring import SecretKeyRingError

    return isinstance(exc, (SecretKeyRingError, EgressStateUnavailable, CredentialSealingUnavailable))


def cli(argv: Sequence[str]) -> int:
    """The process entry (``run_cli``: stdout carries the report line alone)."""
    return run_cli(lambda report: main(argv, deployment_state, report))


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:]))


__all__ = ["STEP", "RekeyReport", "cli", "deployment_state", "describe_error", "main", "parse_apply", "rekey"]

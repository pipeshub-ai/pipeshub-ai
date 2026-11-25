"""SEC4-4: the leases add every record they resolve to their holder's ``ResolvedSecrets``, cleared at revoke."""

from __future__ import annotations

from harness_core.credentials import (
    CredentialLeases,
    CredentialPrincipal,
    CredentialPurpose,
    CredentialScope,
    HandleCredentialService,
    InMemorySecretSource,
)
from harness_core.security.secrets import ResolvedSecrets
from harness_core.security.token_patterns import SECRET_MASK
from harness_core.testing.canary import secret_canary
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

IDS = SeededIds(4044)
TENANT = "org-sec4"
PRINCIPAL = CredentialPrincipal(TENANT, "user-1")
PURPOSE = CredentialPurpose(TENANT, "toolset:jira-1", "run-1", "run-1-attempt-1")
FIRST = secret_canary(IDS.uuid4)
ROTATED = secret_canary(IDS.uuid4)
HEADER = "hv-" + IDS.uuid4().hex


async def _world(material: object) -> tuple[HandleCredentialService, ManualClock, CredentialScope]:
    clock = ManualClock()
    service = HandleCredentialService({"toolset": InMemorySecretSource(clock)}, clock, ids=SeededIds())
    scope = CredentialScope("toolset", "jira-1/user-1")
    _ = await service.rotate(TENANT, scope, material)  # pyright: ignore[reportArgumentType]
    return service, clock, scope


async def test_sec4_4_a_resolved_record_is_masked_until_the_leases_are_revoked() -> None:
    service, clock, scope = await _world({"credentials": {"client_secret": FIRST}, "baseUrl": "https://jira.example.test"})
    resolved = ResolvedSecrets()
    leases = CredentialLeases(service, clock, resolved=resolved)

    assert not resolved.resolved_secrets()
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)
    assert resolved.resolved_secrets().mask(f"{FIRST} at https://jira.example.test") == (
        f"{SECRET_MASK} at https://jira.example.test"
    )

    assert await leases.revoke_all() == 1
    assert not resolved.resolved_secrets()


async def test_sec4_4_a_rotated_record_adds_its_value_and_a_lapsed_handle_keeps_the_old_one() -> None:
    service, clock, scope = await _world({"client_secret": FIRST})
    resolved = ResolvedSecrets()
    leases = CredentialLeases(service, clock, ttl_s=300, refresh_before_s=0, resolved=resolved)
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)

    _ = await service.rotate(TENANT, scope, {"client_secret": ROTATED})  # pyright: ignore[reportArgumentType]
    _ = clock.advance(400)
    _ = await leases.secret(PURPOSE, PRINCIPAL, scope)

    # A client built from the first record may still hold its value after the handle lapsed.
    assert resolved.resolved_secrets().mask(f"{FIRST} {ROTATED}") == f"{SECRET_MASK} {SECRET_MASK}"


async def test_sec4_4_the_holder_names_extra_secret_keys_its_records_use() -> None:
    service, clock, scope = await _world({"credentials": {"headerName": "X-Org-Key", "headerValue": HEADER}})
    plain, named = ResolvedSecrets(), ResolvedSecrets(extra_keys=("headerValue",))
    for resolved in (plain, named):
        _ = await CredentialLeases(service, clock, resolved=resolved).secret(PURPOSE, PRINCIPAL, scope)

    assert plain.resolved_secrets().mask(HEADER) == HEADER
    assert named.resolved_secrets().mask(HEADER) == SECRET_MASK
    assert HEADER not in repr(named)


async def test_sec4_4_leases_without_a_holder_record_nothing() -> None:
    service, clock, scope = await _world({"client_secret": FIRST})
    leases = CredentialLeases(service, clock)

    assert (await leases.secret(PURPOSE, PRINCIPAL, scope)).material == {"client_secret": FIRST}

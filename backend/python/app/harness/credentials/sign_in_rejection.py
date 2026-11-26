"""A toolset credential its vendor rejected is withdrawn where it lives (WP62-TS).

The record is marked ``isAuthenticated: false`` with ``deauthReason: sign_in_rejected``, as the token refresher
marks one whose refresh token is refused and as an admin's OAuth change marks every user's. Every reader then
treats it like a credential the person never connected: the next run lists the toolset's tools as needing a
connection and offers the connect card, other running attempts refuse at their next call (``toolset_liveness``),
and Workspace > Actions shows it to reconnect. Reconnecting writes a new authenticated record, which clears it.

A record is withdrawn only when it is still the one the rejected client was built from, and not when its access
token has merely expired with a refresh token to renew it: renewing is the refresher's job, not the person's.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Final

from app.harness.credentials.legacy_records import legacy_records
from app.harness.credentials.migration import record_expiry_ms
from app.harness.credentials.read_through import record_digest

if TYPE_CHECKING:
    from collections.abc import Callable

DEAUTH_REASON: Final = "sign_in_rejected"


def _renewable(record: dict[str, Any], now_ms: int) -> bool:
    credentials = record.get("credentials")
    if not isinstance(credentials, dict) or not credentials.get("refresh_token"):
        return False
    expiry = record_expiry_ms("toolset", record)
    return expiry is not None and expiry <= now_ms


async def withdraw_rejected_toolset_credential(
    config_service: Any,  # noqa: ANN401 - ConfigurationService
    *,
    org_id: str,
    instance_id: str,
    owner_id: str,
    used_digest: str | None,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> bool:
    """Withdraws ``owner_id``'s record for the instance; ``False`` when it was left as it is (changed since the
    client was built, already withdrawn, or only expired)."""
    records = legacy_records(config_service)
    current = await records.load("toolset", org_id, instance_id, owner_id)
    if not isinstance(current, dict) or not current.get("isAuthenticated", False):
        return False
    if used_digest is None or record_digest(current) != used_digest:
        return False
    now_ms = clock_ms()
    if _renewable(current, now_ms):
        return False
    withdrawn = {**current, "isAuthenticated": False, "deauthReason": DEAUTH_REASON, "deauthAt": now_ms, "updatedAt": now_ms}
    return await records.save("toolset", org_id, instance_id, owner_id, withdrawn, narrowing=True)


__all__ = ["DEAUTH_REASON", "withdraw_rejected_toolset_credential"]

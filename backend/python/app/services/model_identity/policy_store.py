"""Reads the org's identity governance policy.

The hook is synchronous, so it never reads configuration itself: the boundary
loads the policy here (briefly cached) and binds it on the call context.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config.constants.service import config_node_constants
from app.services.model_identity.settings import IdentityGovernancePolicy

logger = logging.getLogger(__name__)

POLICY_TTL_SECONDS = 30.0
_PSEUDONYM_SECRET_FIELDS = ("identityPseudonymKey", "scopedJwtSecret")

# With no saved policy readable, send nothing that identifies a person: no groups,
# and a pseudonym (which the projector turns into the service identity when the
# deployment has no key). The permissive default would forward raw user ids that
# an admin may have turned off.
_OUTAGE_GOVERNANCE = IdentityGovernancePolicy(groups_enabled=False, pseudonymize=True)


class IdentityPolicyStore:
    """Cached read access to ``/services/aiGateway/identityPolicy``.

    OSS has one policy for the deployment. An edition that scopes it per org
    subclasses this and overrides ``_load``.
    """

    def __init__(self, config_service: Any, *, ttl_seconds: float = POLICY_TTL_SECONDS) -> None:  # noqa: ANN401 - an untyped dependency-injection object
        self._config = config_service
        self._ttl = ttl_seconds
        self._cached: IdentityGovernancePolicy | None = None
        self._cached_at = 0.0

    async def get(self, org_id: str = "") -> IdentityGovernancePolicy:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self._ttl:
            return self._cached
        try:
            policy = await self._load(org_id)
        except Exception:
            # A configuration outage must not stop model calls. Keep the last
            # known policy; with none, fall back to the closed one. Recording the
            # attempt makes the next reads wait out the TTL instead of each paying
            # the store's timeout.
            logger.warning("identity policy unavailable", exc_info=True)
            fallback = self._cached or _OUTAGE_GOVERNANCE
            self._cached, self._cached_at = fallback, now
            return fallback
        self._cached, self._cached_at = policy, now
        return policy

    def invalidate(self) -> None:
        self._cached = None

    async def _load(self, org_id: str) -> IdentityGovernancePolicy:
        raw = await self._config.get_config(
            config_node_constants.AI_GATEWAY_IDENTITY_POLICY.value,
            default=None,
            use_cache=False,
            raise_on_error=True,
        )
        return IdentityGovernancePolicy.parse(raw)

    async def pseudonym_key(self) -> bytes | None:
        """Secret for HMAC pseudonyms; None when the deployment has none."""
        try:
            secrets = await self._config.get_config(
                config_node_constants.SECRET_KEYS.value, default=None, use_cache=True
            )
        except Exception:
            logger.warning("pseudonym key unavailable", exc_info=True)
            return None
        if not isinstance(secrets, dict):
            return None
        for name in _PSEUDONYM_SECRET_FIELDS:
            value = secrets.get(name)
            if isinstance(value, str) and value:
                return value.encode()
        return None

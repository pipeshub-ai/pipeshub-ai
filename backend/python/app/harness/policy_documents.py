"""Where a policy document is read from: the one place that decides (CPD-10 §8.1).

``CONTROL_PLANE_POLICY_STORE`` says where the admin policy documents live (``ports/policy-backing.ts``): in
``off`` and ``dual`` the KV documents (``/services/agentHarness/{approvals,engineProfiles,modelAccess}``) are the
record and every reader reads them as before; in ``on`` Node no longer writes them, so reading one would enforce a
stale, possibly looser policy. Then a reader answers from what Node resolved (a run's pin, a request's
``X-PipesHub-Resolved``) or from the org's live layer, which Node serves from the policy store, and a caller that
holds neither is refused: never the KV copy.

Read once per process, as Node reads it at boot: a deployment changes it with a restart. Node also holds ``on`` to
``CONTROL_PLANE_PIN=on`` and refuses to start otherwise; the readers here do not depend on that.
"""

from __future__ import annotations

import os
from functools import cache
from typing import Final, Literal, cast, get_args

from harness_core.refusals import HarnessRefusal

ENV_POLICY_STORE: Final = "CONTROL_PLANE_POLICY_STORE"

PolicyStoreMode = Literal["off", "dual", "on"]
PolicyDocuments = Literal["kv", "live"]
KV: Final[PolicyDocuments] = "kv"
LIVE: Final[PolicyDocuments] = "live"


class InvalidPolicyStoreMode(ValueError):
    reason_code = "invalid_policy_store_mode"


class ResolvedPolicyRequired(HarnessRefusal):
    """The policy store is the record and the caller holds no resolved policy: nothing answers it from KV."""

    reason_code = "resolved_required"


@cache
def policy_store_mode() -> PolicyStoreMode:
    """``CONTROL_PLANE_POLICY_STORE`` as Node parses it (``policyStoreModeFromEnv``): ``dual`` unset or blank, and
    a value it does not know stops the caller instead of reading on a guess."""
    value = (os.getenv(ENV_POLICY_STORE) or "").strip().lower()
    if not value:
        return "dual"
    if value not in get_args(PolicyStoreMode):
        raise InvalidPolicyStoreMode(f"{ENV_POLICY_STORE} is not one of {', '.join(get_args(PolicyStoreMode))}")
    return cast("PolicyStoreMode", value)


def policy_documents() -> PolicyDocuments:
    """``KV`` while the KV documents are the record (``off``, ``dual``): read them exactly as before. ``LIVE`` once
    the policy store is (``on``): no KV policy document may be read."""
    return LIVE if policy_store_mode() == "on" else KV


__all__ = [
    "ENV_POLICY_STORE",
    "KV",
    "LIVE",
    "InvalidPolicyStoreMode",
    "PolicyDocuments",
    "PolicyStoreMode",
    "ResolvedPolicyRequired",
    "policy_documents",
    "policy_store_mode",
]

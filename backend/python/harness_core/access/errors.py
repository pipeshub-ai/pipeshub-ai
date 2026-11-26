"""Typed refusals of model access, each with a stable ``reason_code`` (C-6: unique).

A refusal never names another model or provider the caller could have had:
there is no fallback (principle 6). ``http_status`` is what a gateway answers.
The budget refusals live with the ledger (``harness_core.budget.errors``, P6.5).
"""

from __future__ import annotations

from harness_core.budget.errors import BudgetExhausted, BudgetInsufficient
from harness_core.refusals import HarnessRefusal


class ModelAccessRefused(HarnessRefusal):
    reason_code: str = "model_access_refused"
    http_status: int = 403


class RouteNotConfigured(ModelAccessRefused):
    """The pinned route names a model the org has not configured (or no longer has)."""

    reason_code = "route_not_configured"


class AccountNotPermitted(ModelAccessRefused):
    """The configured model is reached through an account kind this route may not use."""

    reason_code = "account_not_permitted"


class ProviderUnsupported(ModelAccessRefused):
    """The configured provider has no transport the gateway can call."""

    reason_code = "provider_unsupported"


class ModelNotAllowed(ModelAccessRefused):
    """A rule of the org an extension keeps (``ModelRegions``) does not allow the route; the extension may refuse
    with a narrower refusal of its own (``ModelRegions.refused``)."""

    reason_code = "model_not_allowed"


class AccountInvalid(ModelAccessRefused):
    """A model account name or reference that is not well formed."""

    reason_code = "account_invalid"
    http_status = 400


class AccountRevoked(ModelAccessRefused):
    """The pinned model account was removed: its owner revoked it."""

    reason_code = "account_revoked"


class AccountUnavailable(ModelAccessRefused):
    """The pinned model account could not be read (its store is down): refused, never assumed valid."""

    reason_code = "account_unavailable"
    http_status = 503


class GrantUnavailable(ModelAccessRefused):
    """The run's model grant cannot be read: not found, not leased, or the source is down."""

    reason_code = "grant_unavailable"

    def __init__(self, message: str, *, detail: str | None = None, http_status: int = 403) -> None:
        super().__init__(message)
        self.detail = detail
        self.http_status = http_status


class VendorRulesInvalid(ModelAccessRefused):
    """The signed vendor-rule release data is missing, unsigned, tampered or signed by an unknown key: every
    route is refused until a release ships valid rules (fail closed). ``detail`` names which."""

    reason_code = "vendor_rules_invalid"
    http_status = 503

    def __init__(self, message: str, *, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


class VendorRuleRefused(AccountNotPermitted):
    """A vendor-rule decision refused the route; ``detail`` is the deciding row's id (none when no row matched).
    Never raised itself: each subclass names its cause."""

    def __init__(self, message: str, *, rule: str | None = None) -> None:
        super().__init__(message)
        self.detail = rule


class VendorRuleMissing(VendorRuleRefused):
    """No vendor rule names this provider, account kind and route (default deny)."""

    reason_code = "vendor_rule_missing"


class VendorProhibited(VendorRuleRefused):
    reason_code = "vendor_prohibited"


class VendorPending(VendorRuleRefused):
    """The vendor has not answered whether this use is allowed: refused until it does."""

    reason_code = "vendor_pending"


class OfferingNotPermitted(VendorRuleRefused):
    """The vendor allows this only in other offerings (open source, self-hosted, managed) than this install's."""

    reason_code = "offering_not_permitted"


class OrgPolicyRefused(VendorRuleRefused):
    """The organization narrowed the vendor rules: the row is switched off, or consumer plans are not opted into."""

    reason_code = "org_policy_refused"


class AccessPolicyUnavailable(ModelAccessRefused):
    """The organization's access policy could not be read: refused, never assumed permissive."""

    reason_code = "access_policy_unavailable"
    http_status = 503


class AccountNotOwned(AccountNotPermitted):
    """A route pinned to a user's own key that is not the run's acting user's."""

    reason_code = "account_not_owned"


class ModelApiNotPermitted(ModelAccessRefused):
    """The run's engine does not speak the model API this call came in on."""

    reason_code = "model_api_not_permitted"


class AccessNeedMissing(ModelAccessRefused):
    """The grant does not say who acts or which engine calls, so no rule can be checked."""

    reason_code = "access_need_missing"


__all__ = [
    "AccessNeedMissing",
    "AccessPolicyUnavailable",
    "AccountInvalid",
    "AccountNotOwned",
    "AccountNotPermitted",
    "AccountRevoked",
    "AccountUnavailable",
    "BudgetExhausted",
    "BudgetInsufficient",
    "GrantUnavailable",
    "ModelAccessRefused",
    "ModelApiNotPermitted",
    "ModelNotAllowed",
    "OfferingNotPermitted",
    "OrgPolicyRefused",
    "ProviderUnsupported",
    "RouteNotConfigured",
    "VendorPending",
    "VendorProhibited",
    "VendorRuleMissing",
    "VendorRuleRefused",
    "VendorRulesInvalid",
]

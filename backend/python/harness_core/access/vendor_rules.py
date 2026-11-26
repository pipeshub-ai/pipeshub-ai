"""Vendor rules (ARCHITECTURE §4.6.3, D30): which account kinds and routes each model vendor's terms allow.

The rules are release data, signed: a detached Ed25519 signature over the
document's bytes, verified against public keys the host compiles into its code
(``verify_vendor_rules``). Nothing here reads configuration: a deployment cannot
swap the key or the rules, and an org can only narrow the outcome through its
``OrgAccessPolicy`` (switch a row off, opt into consumer-plan rows), never widen it.

``decide`` is pure and is the same function as Node's ``decideAccess``
(``contracts/harness/vectors/vendor-rules.json`` holds both to one answer).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, cast

from harness_core.access.errors import (
    OfferingNotPermitted,
    OrgPolicyRefused,
    VendorPending,
    VendorProhibited,
    VendorRuleMissing,
    VendorRulesInvalid,
)
from harness_core.access.types import AccountKind, RouteKind
from harness_core.contracts.generated.enums import OFFERINGS, RULE_STATUSES, Offering
from harness_core.contracts.generated.enums import VendorRuleStatus as RuleStatus
from harness_core.supply_chain.signatures import (
    SIGNATURE_ALGORITHM,
    SignatureRefused,
    verify_detached,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_STATUSES: Final[frozenset[str]] = frozenset(RULE_STATUSES)
_RELEASE_REFUSALS: Final = {
    "missing": "the vendor rules are missing",
    "unsigned": "the vendor rules are not signed",
    "unknown_key": "the vendor rules are signed by a key this release does not ship",
    "bad_signature": "the vendor rules signature does not verify",
}
DOCUMENT_SCHEMA: Final = "pipeshub.vendor-rules/1"
EXTENSION_SCHEMA: Final = "pipeshub.vendor-rules-extension/1"
_EXTENSION_FIELDS: Final = frozenset({"schema", "version", "offering", "rules"})
_ROW_FIELDS: Final = frozenset(
    {"id", "provider", "accountKind", "route", "engines", "offering", "status", "consumer", "source", "note"}
)
# OWD-8: model traffic goes through the Model Gateway; the one vendor-direct path is a person's own sign-in inside
# their sandbox (``vendor_sandbox_login``, OWD-17). A signed row that would let a key go straight to the vendor is
# read as prohibited, whatever a document says, so no release can reopen that route by data alone.
GATEWAY_BYPASS_ROUTES: Final = frozenset({RouteKind.VENDOR_INJECTED})


@dataclass(frozen=True, slots=True)
class VendorRule:
    id: str
    provider: str
    account_kind: AccountKind
    route: RouteKind
    # ``None``: every engine (and our own loop); otherwise only these, and the row is more specific.
    engines: frozenset[str] | None
    offering: frozenset[Offering]
    status: RuleStatus
    # A consumer plan's terms apply: off for an org until it opts in (§4.6.3, last paragraph).
    consumer: bool
    source: str


@dataclass(frozen=True, slots=True)
class VendorRules:
    version: str
    rules: tuple[VendorRule, ...]

    def ids(self) -> frozenset[str]:
        return frozenset(rule.id for rule in self.rules)


@dataclass(frozen=True, slots=True)
class AccessCandidate:
    """One way a run would reach a model: whose account, by which route, from which engine."""

    provider: str
    account_kind: AccountKind
    route: RouteKind
    engine: str


@dataclass(frozen=True, slots=True)
class OrgAccessPolicy:
    """What an org narrows: rows switched off, whether consumer-plan rows may be used at all, and where its model
    calls may be served (MG-15): one pin per layer that sets one, each of which must allow a route's region."""

    disabled_rules: frozenset[str] = frozenset()
    consumer_plans: bool = False
    region_pins: tuple[frozenset[str], ...] = ()


DEFAULT_ORG_POLICY: Final = OrgAccessPolicy()


def _matches(rule: VendorRule, candidate: AccessCandidate) -> bool:
    return (
        rule.provider == candidate.provider
        and rule.account_kind is candidate.account_kind
        and rule.route is candidate.route
        and (rule.engines is None or candidate.engine in rule.engines)
    )


def decide(
    rules: VendorRules, candidate: AccessCandidate, offering: Offering, policy: OrgAccessPolicy = DEFAULT_ORG_POLICY
) -> VendorRule:
    """The row that allows ``candidate``, or the refusal (its ``detail`` names the deciding row).

    Engine-specific rows win over generic ones; among the winners prohibited beats
    pending beats allowed, so two rows never combine into a wider answer.
    """
    matching = [rule for rule in rules.rules if _matches(rule, candidate)]
    specific = [rule for rule in matching if rule.engines is not None]
    deciding = specific or matching
    if not deciding:
        raise VendorRuleMissing("no vendor rule allows this account and route")
    prohibited = next((rule for rule in deciding if rule.status == "prohibited"), None)
    if prohibited is not None:
        raise VendorProhibited("the vendor's terms prohibit this account and route", rule=prohibited.id)
    pending = next((rule for rule in deciding if rule.status == "pending_vendor"), None)
    if pending is not None:
        raise VendorPending("the vendor has not confirmed this account and route", rule=pending.id)
    allowed = next((rule for rule in deciding if offering in rule.offering), None)
    if allowed is None:
        raise OfferingNotPermitted("this offering may not use this account and route", rule=deciding[0].id)
    if allowed.id in policy.disabled_rules:
        raise OrgPolicyRefused("the organization switched this account and route off", rule=allowed.id)
    if allowed.consumer and not policy.consumer_plans:
        raise OrgPolicyRefused("the organization has not opted into consumer plans", rule=allowed.id)
    return allowed


def _invalid(detail: str, message: str) -> VendorRulesInvalid:
    return VendorRulesInvalid(message, detail=detail)


def _strings(raw: object, what: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(v, str) and v for v in cast("list[object]", raw)):
        raise _invalid("malformed", f"{what} is not a list of names")
    return cast("list[str]", raw)


def _row(raw: object) -> VendorRule:
    if not isinstance(raw, dict):
        raise _invalid("malformed", "a rule is not an object")
    row = cast("dict[str, object]", raw)
    if set(row) - _ROW_FIELDS:
        raise _invalid("malformed", "a rule has an unknown field")
    try:
        rule_id, provider, source = row["id"], row["provider"], row["source"]
        status, consumer = row["status"], row["consumer"]
        account_kind = AccountKind(cast("str", row["accountKind"]))
        route = RouteKind(cast("str", row["route"]))
    except (KeyError, ValueError):
        raise _invalid("malformed", "a rule lacks a field or names an unknown kind") from None
    if not all(isinstance(v, str) and v for v in (rule_id, provider, source)):
        raise _invalid("malformed", "a rule's id, provider or source is empty")
    if status not in _STATUSES or not isinstance(consumer, bool):
        raise _invalid("malformed", "a rule's status or consumer flag is invalid")
    offering = _strings(row.get("offering"), "offering")
    if not set(offering) <= set(OFFERINGS) or not offering:
        raise _invalid("malformed", "a rule names an unknown offering")
    engines_raw = row.get("engines")
    engines = None if engines_raw is None else frozenset(_strings(engines_raw, "engines"))
    if engines is not None and not engines:
        raise _invalid("malformed", "a rule names an empty engine list")
    return VendorRule(
        id=cast("str", rule_id),
        provider=cast("str", provider),
        account_kind=account_kind,
        route=route,
        engines=engines,
        offering=cast("frozenset[Offering]", frozenset(offering)),
        status="prohibited" if route in GATEWAY_BYPASS_ROUTES else cast("RuleStatus", status),
        consumer=consumer,
        source=cast("str", source),
    )


def parse_vendor_rules(document: bytes) -> VendorRules:
    """The rows of a verified document; anything not exactly a rule document is ``malformed``."""
    try:
        raw: object = json.loads(document)
    except (ValueError, UnicodeDecodeError):
        raise _invalid("malformed", "the vendor rules are not JSON") from None
    if not isinstance(raw, dict):
        raise _invalid("malformed", "the vendor rules are not an object")
    body = cast("dict[str, object]", raw)
    version, rows = body.get("version"), body.get("rules")
    if body.get("schema") != DOCUMENT_SCHEMA or not isinstance(version, str) or not isinstance(rows, list):
        raise _invalid("malformed", "the vendor rules document has no schema, version or rules")
    rules = tuple(_row(row) for row in cast("list[object]", rows))
    if len({rule.id for rule in rules}) != len(rules):
        raise _invalid("malformed", "two vendor rules share an id")
    return VendorRules(version, rules)


def extend_vendor_rules(rules: VendorRules, document: bytes) -> VendorRules:
    """``rules`` with the offering a verified extension document adds (an edition's own offering, x-extensible-enum).

    The document names one offering that no row of ``rules`` names and the rows that offering may use. It opens only
    those rows to it: a row's status and every other offering's answer stay as signed, and it cannot add a row, so
    an extension can never widen what a core offering is allowed. Anything else is ``malformed``."""
    try:
        raw: object = json.loads(document)
    except (ValueError, UnicodeDecodeError):
        raise _invalid("malformed", "the vendor rules extension is not JSON") from None
    if not isinstance(raw, dict) or set(cast("dict[str, object]", raw)) != _EXTENSION_FIELDS:
        raise _invalid("malformed", "the vendor rules extension is not an extension document")
    body = cast("dict[str, object]", raw)
    version, offering = body["version"], body["offering"]
    if body["schema"] != EXTENSION_SCHEMA or not isinstance(version, str) or not version:
        raise _invalid("malformed", "the vendor rules extension has no schema or version")
    named = {o for rule in rules.rules for o in rule.offering}
    if not isinstance(offering, str) or offering not in OFFERINGS or offering in named:
        raise _invalid("malformed", "the vendor rules extension names an unknown offering or one the rules decide")
    ids = _strings(body["rules"], "rules")
    if not ids or len(set(ids)) != len(ids) or not set(ids) <= rules.ids():
        raise _invalid("malformed", "the vendor rules extension names a rule the rules do not have")
    opened = frozenset(ids)
    extended = cast("Offering", offering)
    return VendorRules(
        f"{rules.version}+{version}",
        tuple(replace(rule, offering=rule.offering | {extended}) if rule.id in opened else rule for rule in rules.rules),
    )


def verify_vendor_rules(document: bytes | None, signature: bytes | None, keys: Mapping[str, bytes]) -> VendorRules:
    """The rules, only when ``signature`` is a valid signature of ``document`` by one of ``keys``
    (key id → raw 32-byte Ed25519 public key). Every failure is ``vendor_rules_invalid``."""
    return parse_vendor_rules(verify_release_document(document, signature, keys))


def verify_release_document(document: bytes | None, signature: bytes | None, keys: Mapping[str, bytes]) -> bytes:
    """``document``, only when ``signature`` is a valid signature of it by one of ``keys``; the check every piece of
    signed release data (the vendor rules, their sign-in hosts) passes, through the one detached-signature check
    every signed artefact uses (ADR-SEC-01). Every failure is ``vendor_rules_invalid``."""
    try:
        _ = verify_detached(document, signature, keys)
    except SignatureRefused as refused:
        raise _invalid(refused.detail, _RELEASE_REFUSALS[refused.detail]) from None
    return cast("bytes", document)


__all__ = [
    "DEFAULT_ORG_POLICY",
    "DOCUMENT_SCHEMA",
    "EXTENSION_SCHEMA",
    "GATEWAY_BYPASS_ROUTES",
    "OFFERINGS",
    "SIGNATURE_ALGORITHM",
    "AccessCandidate",
    "Offering",
    "OrgAccessPolicy",
    "RuleStatus",
    "VendorRule",
    "VendorRules",
    "decide",
    "extend_vendor_rules",
    "parse_vendor_rules",
    "verify_release_document",
    "verify_vendor_rules",
]

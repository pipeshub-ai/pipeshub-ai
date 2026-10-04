"""Organisation names a document mentions (KG-13 slice 3b).

The classification call lists them; this module decides which are worth
resolving. ``organization_key`` is the comparison key: two spellings of one
company ("Globex Corp.", "Globex Corporation", "globex") share it.
"""

from __future__ import annotations

import re

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.entity_resolution.normalizer import (
    display_form,
    is_acceptable_name,
    normalize_name,
)

# Records that must name an organisation no connector knows before it is
# searchable: one mention is too often a stray name (KG-13, decided 2026-10-02).
MIN_EXTRACTED_RECORDS = 2

# Indexing names a tenant this until its owner registers a real name.
PLACEHOLDER_TENANT_NAME = "Individual Account"

_LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "plc", "corp", "corporation",
    "co", "company", "gmbh", "ag", "sa", "sas", "srl", "spa", "bv", "nv", "oy", "ab", "as",
    "pty", "pvt", "private", "kk", "sarl",
})
_SEPARATORS_RE = re.compile(r"[.,]+")

# Words that stand for an organisation without naming one.
_GENERIC = frozenset({
    "company", "the company", "client", "the client", "customer", "the customer", "vendor",
    "the vendor", "supplier", "the supplier", "partner", "the partner", "organization",
    "organisation", "the organization", "the organisation", "corporation", "firm", "the firm",
    "agency", "the agency", "bank", "the bank", "government", "the government", "university",
    "the university", "startup", "employer", "the employer", "contractor", "the contractor",
    "team", "the team", "department", "management", "the board", "board", "headquarters",
})

# Product names of the application a connector syncs from, by connector name
# with its edition suffix removed; a connector not listed is its own name.
_APP_NAMES: dict[str, frozenset[str]] = {
    "drive": frozenset({"google drive", "drive"}),
    "gmail": frozenset({"gmail", "google mail"}),
    "calendar": frozenset({"google calendar", "calendar"}),
    "onedrive": frozenset({"onedrive", "microsoft onedrive"}),
    "sharepoint online": frozenset({"sharepoint", "sharepoint online", "microsoft sharepoint"}),
    "outlook": frozenset({"outlook", "microsoft outlook"}),
    "outlook calendar": frozenset({"outlook", "outlook calendar", "microsoft outlook"}),
    "microsoft teams": frozenset({"microsoft teams", "teams"}),
    "kb": frozenset(),
    "web": frozenset(),
    "unknown": frozenset(),
}
_EDITION_SUFFIXES = (" workspace", " personal", " data center", " cloud")


def organization_key(name: str) -> str:
    """Case-, punctuation- and legal-suffix-insensitive key of a name."""
    words = _SEPARATORS_RE.sub(" ", normalize_name(name)).split()
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def extracted_organization_id(tenant_id: str, name: str) -> str:
    """The key extraction gives the tenant's organisation named ``name``."""
    return taxonomy_node_key(tenant_id, CollectionNames.ORGS.value, organization_key(name))


def _app_names(connector_name: str) -> frozenset[str]:
    name = (connector_name or "").strip().casefold()
    changed = True
    while changed:
        changed = False
        for suffix in _EDITION_SUFFIXES:
            if name.endswith(suffix):
                name, changed = name[: -len(suffix)], True
    return _APP_NAMES.get(name, frozenset({name}) if name else frozenset())


def usable_organization_names(names: list[str], *, tenant_name: str, connector_name: str) -> list[str]:
    """``names`` worth resolving, in order and once per key: not the tenant
    itself, not the application the record came from, not a generic word."""
    tenant = "" if tenant_name == PLACEHOLDER_TENANT_NAME else organization_key(tenant_name or "")
    # Its leading words too: documents call "Northwind Analytics" "Northwind".
    tenant_words = tenant.split()
    tenant_forms = {" ".join(tenant_words[:n]) for n in range(1, len(tenant_words) + 1)}
    app = {organization_key(n) for n in _app_names(connector_name)}
    seen: set[str] = set()
    kept: list[str] = []
    for raw in names:
        name = display_form(raw or "")
        key = organization_key(name)
        if not is_acceptable_name(normalize_name(name)) or not key or key in seen:
            continue
        seen.add(key)
        if key in tenant_forms or key in app or normalize_name(name) in _GENERIC or key in _GENERIC:
            continue
        kept.append(name)
    return kept


def searchable_organizations(reach: dict[str, dict]) -> set[str]:
    """The keys of ``reach`` (``IGraphDBProvider.get_organization_record_reach``)
    that belong in the entity index: known to a connector, or named in
    enough records."""
    return {
        key for key, r in reach.items()
        if r.get("inferred") or int(r.get("records") or 0) >= MIN_EXTRACTED_RECORDS
    }

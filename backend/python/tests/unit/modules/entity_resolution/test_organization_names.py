"""Extracted organisation names are filtered before resolution (KG-13 3b):
the org itself, the application the record came from, and generic words
are not organisations worth a node."""
from __future__ import annotations

import pytest

from app.modules.entity_resolution.organizations import (
    accounts_last,
    extracted_organization_id,
    usable_organization_names,
)


def test_keeps_real_names_in_order_and_once() -> None:
    names = ["Acme Corp", "Globex", "ACME CORP.", "University of Oxford"]
    assert usable_organization_names(names, org_name="Northwind", connector_name="DRIVE") == [
        "Acme Corp", "Globex", "University of Oxford",
    ]


def test_drops_the_org_itself_with_or_without_its_legal_suffix() -> None:
    assert usable_organization_names(
        ["Northwind Analytics", "northwind analytics, inc", "Acme"],
        org_name="Northwind Analytics", connector_name="SLACK",
    ) == ["Acme"]


def test_the_placeholder_org_name_filters_nothing() -> None:
    assert usable_organization_names(
        ["Individual Account"], org_name="Individual Account", connector_name="KB",
    ) == ["Individual Account"]


@pytest.mark.parametrize(("connector", "app"), [
    ("SLACK", "Slack"), ("SLACK WORKSPACE", "slack"), ("DRIVE", "Google Drive"),
    ("GMAIL WORKSPACE", "Gmail"), ("MICROSOFT TEAMS", "Microsoft Teams"), ("MICROSOFT TEAMS", "Teams"),
    ("JIRA DATA CENTER", "Jira"), ("CONFLUENCE", "Confluence"), ("SHAREPOINT ONLINE", "SharePoint"),
])
def test_drops_the_application_the_record_came_from(connector: str, app: str) -> None:
    assert usable_organization_names([app, "Acme"], org_name="", connector_name=connector) == ["Acme"]


def test_another_application_is_kept() -> None:
    """A Drive contract with Slack Technologies names a real counterparty."""
    assert usable_organization_names(["Slack"], org_name="", connector_name="DRIVE") == ["Slack"]


@pytest.mark.parametrize("generic", ["the client", "Vendor", "Company", "customer", "Bank", "the government"])
def test_drops_generic_words(generic: str) -> None:
    assert usable_organization_names([generic], org_name="", connector_name="DRIVE") == []


def test_drops_unusable_strings() -> None:
    assert usable_organization_names(["", " ", "x", "a" * 200], org_name="", connector_name="DRIVE") == []


def test_legal_suffixes_do_not_make_a_second_organisation() -> None:
    assert usable_organization_names(
        ["Globex Corporation", "Globex Corp.", "Globex"], org_name="", connector_name="DRIVE",
    ) == ["Globex Corporation"]


def test_the_orgs_short_name_is_the_org() -> None:
    """Documents call the org by its first word ("Northwind"); a name only
    sharing part of a word, or adding words, is another organisation."""
    assert usable_organization_names(
        ["Northwind", "North", "Northwind Traders", "Acme"],
        org_name="Northwind Analytics", connector_name="DRIVE",
    ) == ["North", "Northwind Traders", "Acme"]


@pytest.mark.parametrize("name", ["", "   ", "."])
def test_a_name_with_no_key_has_no_extracted_id_and_does_not_break_the_account_order(name: str) -> None:
    """A stored account whose name normalizes to nothing must not fail every account sync."""
    assert extracted_organization_id("org-1", name) is None
    extracted = {"_key": extracted_organization_id("org-1", "Acme"), "parentOrgId": "org-1", "name": "Acme"}
    blank = {"_key": "acct-1", "parentOrgId": "org-1", "name": name}
    assert accounts_last([blank, extracted]) == [extracted, blank]


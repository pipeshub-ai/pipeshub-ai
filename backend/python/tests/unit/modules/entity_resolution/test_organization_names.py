"""Extracted organisation names are filtered before resolution (KG-13 3b):
the tenant itself, the application the record came from, and generic words
are not organisations worth a node."""
from __future__ import annotations

import pytest

from app.modules.entity_resolution.organizations import usable_organization_names


def test_keeps_real_names_in_order_and_once() -> None:
    names = ["Acme Corp", "Globex", "ACME CORP.", "University of Oxford"]
    assert usable_organization_names(names, tenant_name="Northwind", connector_name="DRIVE") == [
        "Acme Corp", "Globex", "University of Oxford",
    ]


def test_drops_the_tenant_itself_with_or_without_its_legal_suffix() -> None:
    assert usable_organization_names(
        ["Northwind Analytics", "northwind analytics, inc", "Acme"],
        tenant_name="Northwind Analytics", connector_name="SLACK",
    ) == ["Acme"]


def test_the_placeholder_tenant_name_filters_nothing() -> None:
    assert usable_organization_names(
        ["Individual Account"], tenant_name="Individual Account", connector_name="KB",
    ) == ["Individual Account"]


@pytest.mark.parametrize(("connector", "app"), [
    ("SLACK", "Slack"), ("SLACK WORKSPACE", "slack"), ("DRIVE", "Google Drive"),
    ("GMAIL WORKSPACE", "Gmail"), ("MICROSOFT TEAMS", "Microsoft Teams"), ("MICROSOFT TEAMS", "Teams"),
    ("JIRA DATA CENTER", "Jira"), ("CONFLUENCE", "Confluence"), ("SHAREPOINT ONLINE", "SharePoint"),
])
def test_drops_the_application_the_record_came_from(connector: str, app: str) -> None:
    assert usable_organization_names([app, "Acme"], tenant_name="", connector_name=connector) == ["Acme"]


def test_another_application_is_kept() -> None:
    """A Drive contract with Slack Technologies names a real counterparty."""
    assert usable_organization_names(["Slack"], tenant_name="", connector_name="DRIVE") == ["Slack"]


@pytest.mark.parametrize("generic", ["the client", "Vendor", "Company", "customer", "Bank", "the government"])
def test_drops_generic_words(generic: str) -> None:
    assert usable_organization_names([generic], tenant_name="", connector_name="DRIVE") == []


def test_drops_unusable_strings() -> None:
    assert usable_organization_names(["", " ", "x", "a" * 200], tenant_name="", connector_name="DRIVE") == []


def test_legal_suffixes_do_not_make_a_second_organisation() -> None:
    assert usable_organization_names(
        ["Globex Corporation", "Globex Corp.", "Globex"], tenant_name="", connector_name="DRIVE",
    ) == ["Globex Corporation"]


def test_the_tenants_short_name_is_the_tenant() -> None:
    """Documents call the tenant by its first word ("Northwind"); a name only
    sharing part of a word, or adding words, is another organisation."""
    assert usable_organization_names(
        ["Northwind", "North", "Northwind Traders", "Acme"],
        tenant_name="Northwind Analytics", connector_name="DRIVE",
    ) == ["North", "Northwind Traders", "Acme"]

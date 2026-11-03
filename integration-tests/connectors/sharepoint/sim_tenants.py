# pyright: ignore-file

"""Tenants the SharePoint simulator suites load (the document shape is in connectors-simulator's
``src/connsim/sources/sharepoint/scenario.py``).

The hard cases sit in known places: a group-connected private site, restricted folders and pages, a public
intranet open to everyone, a site open to members only, nested security groups, a guest, a subsite that
inherits, a second library with its own permissions, and a OneDrive site the connector must leave alone.
Every object has a short ``key`` the tests use to change it.
"""

from __future__ import annotations

import copy
from typing import Any

USERS = [
    {"key": "alice", "name": "Alice Admin"},
    {"key": "bob", "name": "Bob Builder"},
    {"key": "carol", "name": "Carol HR"},
    {"key": "dan", "name": "Dan Director"},
    {"key": "erin", "name": "Erin Finance"},
    {"key": "frank", "name": "Frank Field"},
    {"key": "gus", "name": "Gus Guest", "type": "Guest", "mail": "gus@partner.example"},
]

GROUPS = [
    {"key": "eng", "name": "Engineering", "members": ["user:alice", "group:eng-leads"]},
    {"key": "eng-leads", "name": "Engineering Leads", "members": ["user:bob"]},
    {"key": "hr-team", "name": "HR Team", "unified": True, "members": ["user:carol", "user:dan"],
     "owners": ["dan"]},
    {"key": "fin", "name": "Finance", "members": ["user:erin"]},
]

SITES = [
    {"key": "hr", "title": "HR", "group": "hr-team",
     "libraries": [{"key": "hr-docs", "name": "Documents", "url_name": "Shared Documents", "tree": [
         {"file": "welcome.docx", "key": "hr-welcome"},
         {"folder": "Policies", "key": "hr-policies", "children": [
             {"file": "handbook.pdf", "key": "hr-handbook"}]},
         {"folder": "Salaries", "key": "hr-salaries", "unique": True,
          "grants": [{"principal": "user:dan", "roles": ["Read"]}],
          "children": [{"file": "salaries.xlsx", "key": "hr-salaries-file"},
                       {"folder": "2026", "key": "hr-salaries-2026", "children": [
                           {"file": "q1.xlsx", "key": "hr-q1"}]}]}]}],
     "pages": [{"key": "hr-page-welcome", "title": "HR Welcome"},
               {"key": "hr-page-secret", "title": "Reorg plan", "unique": True,
                "grants": [{"principal": "user:carol", "roles": ["Read"]}]},
               {"key": "hr-page-home", "title": "Home", "system": True}]},
    {"key": "eng", "title": "Engineering", "members": ["secgroup:eng"], "visitors": ["everyone_ext"],
     "libraries": [
         {"key": "eng-docs", "name": "Documents", "url_name": "Shared Documents", "tree": [
             {"folder": "Specs", "key": "eng-specs", "children": [{"file": "design.docx", "key": "eng-design"}]},
             {"folder": "Restricted", "key": "eng-restricted", "unique": True,
              "grants": [{"principal": "secgroup:eng-leads", "roles": ["Edit"]}],
              "children": [{"file": "roadmap.pptx", "key": "eng-roadmap"}]},
             # Shared with the Entra group itself, not through a SharePoint group.
             {"folder": "All engineering", "key": "eng-all", "unique": True,
              "grants": [{"principal": "secgroup:eng", "roles": ["Read"]}],
              "children": [{"file": "allhands.pptx", "key": "eng-allhands"}]}]},
         {"key": "eng-archive", "name": "Archive", "url_name": "Archive", "unique": True,
          "grants": [{"principal": "user:alice", "roles": ["Read"]}],
          "tree": [{"file": "old.txt", "key": "eng-old"}]}],
     "pages": [{"key": "eng-page", "title": "Engineering news"}],
     "subsites": [{"key": "eng-sub", "title": "Platform", "libraries": [
         {"key": "eng-sub-docs", "name": "Documents", "url_name": "Shared Documents",
          "tree": [{"file": "platform.md", "key": "eng-sub-file"}]}]}]},
    {"key": "intranet", "title": "Intranet", "visitors": ["everyone"],
     "libraries": [{"key": "intranet-docs", "name": "Documents", "url_name": "Shared Documents",
                    "tree": [{"file": "news.pdf", "key": "intranet-news"}]}]},
    {"key": "fin", "title": "Finance", "members": ["secgroup:fin"],
     "libraries": [{"key": "fin-docs", "name": "Documents", "url_name": "Shared Documents",
                    "tree": [{"file": "budget.xlsx", "key": "fin-budget"}]}]},
    {"key": "alice-od", "title": "Alice OneDrive", "personal": True, "path": "/personal/alice_contoso_com",
     "owners": ["user:alice"],
     "libraries": [{"key": "alice-od-docs", "name": "Documents", "url_name": "Documents",
                    "tree": [{"file": "private.docx", "key": "alice-private"}]}]},
]


def base_tenant(**overrides: object) -> dict[str, Any]:
    tenant = {"seed": 7, "tenant": "contoso", "users": copy.deepcopy(USERS), "groups": copy.deepcopy(GROUPS),
              "sites": copy.deepcopy(SITES)}
    tenant.update(overrides)
    return tenant


def small_tenant() -> dict[str, Any]:
    """One site, one library, three people: for tests about one mechanism."""
    return {"seed": 11, "tenant": "contoso",
            "users": [{"key": "alice", "name": "Alice"}, {"key": "bob", "name": "Bob"},
                      {"key": "carol", "name": "Carol"}],
            "groups": [{"key": "team", "name": "Team", "members": ["user:alice", "user:bob"]}],
            "sites": [{"key": "s1", "title": "Site One", "members": ["secgroup:team"],
                       "libraries": [{"key": "docs", "name": "Documents", "url_name": "Shared Documents",
                                      "tree": [{"file": "a.docx", "key": "a"},
                                               {"folder": "F", "key": "f", "children": [
                                                   {"file": "b.pdf", "key": "b"}]}]}]}]}


def site(tenant: dict[str, Any], key: str) -> dict[str, Any]:
    return next(s for s in tenant["sites"] if s["key"] == key)

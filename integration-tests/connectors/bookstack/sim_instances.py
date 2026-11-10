# pyright: ignore-file

"""Instance documents the bookstack_sim tests start from (the shape is the connectors-simulator's
`connsim.sources.bookstack.context.BsContext.load`; a copy of its harness/bookstack/tenants.py).

One job: describe a realistic BookStack with the hard cases in known places: a book only one role may read
(Everyone Else: nothing), a page only Editors may read, a chapter that blocks the Viewer role while the book
lets it in, an explicit deny for Viewer on one page while another of a user's roles lets them in, a role that may
view only its own pages, a shelf restricted to Viewer, a user in no role, a user with no PipesHub account and a
user whose PipesHub address differs in case.
"""

from __future__ import annotations

import copy
from typing import Any

VIEW = ["book-view-all", "page-view-all", "chapter-view-all", "bookshelf-view-all", "content-export"]
DENY_ALL = {"view": False, "create": False, "update": False, "delete": False}

ROLES = [
    {"key": "finance", "name": "Finance", "permissions": []},
    {"key": "authors", "name": "Authors", "permissions": ["page-view-own", "chapter-view-all", "book-view-all"]},
    {"key": "readers", "name": "Readers", "permissions": VIEW},
]

USERS = [
    {"key": "bob", "name": "Bob", "email": "bob@example.com", "roles": ["editor"]},
    {"key": "carol", "name": "Carol", "email": "carol@example.com", "roles": ["viewer"]},
    {"key": "dan", "name": "Dan", "email": "dan@example.com", "roles": ["viewer", "finance"]},
    {"key": "erin", "name": "Erin", "email": "erin@example.com", "roles": ["finance"]},
    {"key": "frank", "name": "Frank", "email": "frank@example.com", "roles": ["authors"]},
    {"key": "gina", "name": "Gina", "email": "gina@example.com", "roles": ["readers", "viewer"]},
    {"key": "hal", "name": "Hal", "email": "hal@example.com", "roles": []},
]

BOOKS = [
    {"key": "handbook", "name": "Handbook", "contents": [
        {"chapter": "Policies", "key": "policies", "pages": [
            {"page": "Leave policy", "key": "leave", "markdown": "# Leave\nEveryone gets 25 days."},
            {"page": "Expenses", "key": "expenses", "markdown": "Keep receipts."}]},
        {"page": "Welcome", "key": "welcome", "markdown": "Welcome to the handbook."},
        {"page": "Not for viewers", "key": "denied", "markdown": "Hidden from the Viewer role."},
        {"page": "Ünïcödé 日本語 / slash", "key": "unicode", "html": "<p>Héllo wörld</p>"},
        {"page": "Frank's notes", "key": "frank_own", "owner": "frank", "markdown": "Frank wrote this."}]},
    {"key": "finance", "name": "Finance", "contents": [
        {"page": "Budget 2026", "key": "budget", "markdown": "Numbers."},
        {"page": "Frank's plan", "key": "frank_plan", "owner": "frank", "markdown": "A plan."}]},
    {"key": "engineering", "name": "Engineering", "contents": [
        {"chapter": "Internal", "key": "internal", "pages": [
            {"page": "Internal notes", "key": "internal_notes", "markdown": "Notes."}]},
        {"page": "Runbook", "key": "runbook", "markdown": "Drain traffic, then restart."},
        {"page": "Secret plan", "key": "secret", "markdown": "Shh."}]},
    {"key": "empty", "name": "Empty", "contents": []},
]

SHELVES = [{"key": "company", "name": "Company", "books": ["handbook", "finance"]}]

PERMISSIONS = [
    {"target": "finance", "roles": {"finance": {"view": True}}, "fallback": DENY_ALL},
    {"target": "secret", "roles": {"editor": {"view": True, "update": True}}, "fallback": DENY_ALL},
    {"target": "internal", "roles": {"viewer": {"view": False}}},
    {"target": "denied", "roles": {"viewer": {"view": False}}},
    {"target": "company", "roles": {"viewer": {"view": True}}, "fallback": DENY_ALL},
]

# PipesHub accounts: everyone but Erin; Frank signs in with a capitalised address.
PEOPLE = {"admin": "admin@admin.com", "bob": "bob@example.com", "carol": "carol@example.com",
          "dan": "dan@example.com", "frank": "Frank@Example.com", "gina": "gina@example.com",
          "hal": "hal@example.com"}


def base_instance(**overrides: Any) -> dict[str, Any]:
    doc = {"roles": copy.deepcopy(ROLES), "users": copy.deepcopy(USERS), "books": copy.deepcopy(BOOKS),
           "shelves": copy.deepcopy(SHELVES), "permissions": copy.deepcopy(PERMISSIONS)}
    doc.update(overrides)
    return doc


def plain_instance(**overrides: Any) -> dict[str, Any]:
    """The base instance without its two access traps (a role that may view books but only its own pages, and
    a user whose second role is denied a page explicitly), for scenarios about another mechanism: a failure in
    them then comes from that mechanism."""
    doc = base_instance(**overrides)
    for role in doc["roles"]:
        if role["key"] == "authors":
            role["permissions"] = VIEW + ["page-view-own"]
    for user in doc["users"]:
        if user["key"] == "gina":
            user["roles"] = ["readers"]
    return doc


def small_instance(pages: int = 3, books: int = 1) -> dict[str, Any]:
    """Books of plain pages everyone with the Viewer role may read: for scenarios about one mechanism."""
    return {"roles": [], "users": [{"key": "carol", "name": "Carol", "email": "carol@example.com",
                                    "roles": ["viewer"]}],
            "books": [{"key": f"b{b}", "name": f"Book {b}",
                       "contents": [{"page": f"Page {b}.{i}", "key": f"p{b}_{i}", "markdown": f"Text {b}.{i}."}
                                    for i in range(pages)]} for b in range(books)],
            "shelves": [], "permissions": []}


SMALL_PEOPLE = {"admin": "admin@admin.com", "carol": "carol@example.com"}

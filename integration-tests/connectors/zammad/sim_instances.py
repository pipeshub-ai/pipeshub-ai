# pyright: ignore-file

"""Installations the zammad_sim tests start from (the shape is the connectors-simulator's
`connsim.sources.zammad.context.ZmContext.load`; a copy of its harness/zammad/tenants.py).

One job: describe a realistic Zammad with the hard cases in known places: agents whose group access is only change,
create or overview (they hold a row on the group but may not read it), an agent who reaches a group only through a
role, customers with their own tickets and with a shared organisation, a group that is not active, an inactive agent,
internal articles with attachments, and a knowledge base with a published, an internal, a draft and an archived answer.
"""

from __future__ import annotations

import copy
from typing import Any

# The integration token (Admin + Agent permissions, no knowledge_base.*): the knowledge base lists no answer to it.
TOKEN = "pipeshub-integration-zammad-token"
# The same admin with knowledge_base.editor: every answer is listed.
KB_TOKEN = "pipeshub-kb-editor-token"
TOKENS = [
    {"token": TOKEN, "name": "pipeshub", "user": "admin", "permission": ["admin", "ticket.agent"]},
    {"token": KB_TOKEN, "name": "kb-editor", "user": "admin",
     "permission": ["admin", "ticket.agent", "knowledge_base.editor"]},
]
# A token of an agent who is not an admin: on 6.4.1 GET /groups needs admin.group, so it cannot list groups.
AGENT_TOKEN = "pipeshub-agent-only-token"
AGENT_TOKENS = [{"token": AGENT_TOKEN, "name": "agent-only", "user": "ann", "permission": ["ticket.agent"]}]

GROUPS = [{"key": "support", "name": "Support"}, {"key": "billing", "name": "Billing"},
          {"key": "retired", "name": "Retired", "active": False}]

ORGANIZATIONS = [{"key": "acme", "name": "Acme", "shared": True}, {"key": "quiet", "name": "Quiet", "shared": False}]

ROLES = [{"key": "billing_agents", "name": "Billing agents", "permissions": ["ticket.agent"],
          "groups": {"billing": ["read"]}}]

USERS = [
    {"key": "ann", "email": "ann@example.com", "firstname": "Ann", "roles": ["Agent"], "groups": {"support": ["full"]}},
    {"key": "ben", "email": "ben@example.com", "firstname": "Ben", "roles": ["Agent"], "groups": {"support": ["read"]}},
    {"key": "cy", "email": "cy@example.com", "firstname": "Cy", "roles": ["Agent"], "groups": {"support": ["change"]}},
    {"key": "dee", "email": "dee@example.com", "firstname": "Dee", "roles": ["Agent"],
     "groups": {"support": ["create", "overview"]}},
    {"key": "eva", "email": "eva@example.com", "firstname": "Eva", "roles": ["billing_agents"]},
    {"key": "fox", "email": "fox@example.com", "firstname": "Fox", "roles": ["Agent"],
     "groups": {"billing": ["full"], "retired": ["full"]}},
    {"key": "gus", "email": "gus@example.com", "firstname": "Gus", "roles": ["Agent"], "groups": {"support": ["full"]},
     "active": False},
    {"key": "carol", "email": "carol@example.com", "firstname": "Carol", "roles": ["Customer"], "organization": "acme"},
    {"key": "cole", "email": "cole@example.com", "firstname": "Cole", "roles": ["Customer"], "organization": "acme"},
    {"key": "dina", "email": "dina@example.com", "firstname": "Dina", "roles": ["Customer"], "organization": "quiet"},
    {"key": "erin", "email": "erin@example.com", "firstname": "Erin", "roles": ["Customer"], "organizations": ["acme"]},
    {"key": "vera", "email": "vera@example.com", "firstname": "Vera", "roles": ["Agent", "Customer"]},
]

TICKETS = [
    {"key": "laptop", "title": "Laptop will not boot", "group": "support", "customer": "carol",
     "articles": [{"key": "laptop_a", "body": "<p>Black screen after the update.</p>", "sender": "Customer"},
                  {"key": "laptop_note", "body": "<p>Internal: firmware looks bad.</p>", "internal": True,
                   "sender": "Agent",
                   "attachments": [{"filename": "boot.txt", "content": "boot log: firmware handover failed at 03:14"}]}]},
    {"key": "printer", "title": "Printer jams on duplex", "group": "support", "customer": "dina"},
    {"key": "seats", "title": "Seat count", "group": "support", "customer": "cole"},
    {"key": "invoice", "title": "Invoice 4471 is wrong", "group": "billing", "customer": "carol",
     "articles": [{"body": "We were billed twice.", "sender": "Customer",
                   "attachments": [{"filename": "invoice.pdf", "size": 3000}]},
                  {"body": "Auto reply sent.", "sender": "System",
                   "attachments": [{"filename": "autoreply.txt", "content": "x"}]}]},
    {"key": "retired", "title": "In a retired group", "group": "retired", "customer": "carol"},
]

LINKS = [{"type": "normal", "from": "laptop", "to": "invoice"}, {"type": "parent", "from": "laptop", "to": "printer"}]

KB = {"categories": [{"key": "faq", "title": "FAQ"}, {"key": "staff", "title": "Staff only", "parent": "faq"}],
      "answers": [{"key": "pub", "category": "faq", "title": "How to reset your password", "state": "published",
                   "body": "<p>Use the reset link.</p>", "attachments": [{"filename": "howto.txt", "content": "steps"}]},
                  {"key": "int", "category": "staff", "title": "Escalation rules", "state": "internal",
                   "body": "<p>Escalate after two hours.</p>"},
                  {"key": "draft", "category": "faq", "title": "Draft: new retention policy", "state": "draft"},
                  {"key": "arch", "category": "faq", "title": "Archived: old VPN setup", "state": "archived"}]}

# PipesHub accounts, made once per session before any connector: every person of the instances, customers too (a
# customer with a PipesHub account is how the customer rules are checked), and the agent TC-ZMSIM-ACL-006 adds.
PEOPLE = ["admin@example.com", *(u["email"] for u in USERS), "newbie@example.com"]


def base_instance(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {"groups": GROUPS, "organizations": ORGANIZATIONS, "roles": ROLES, "users": USERS,
                           "tickets": TICKETS, "links": LINKS, "tokens": TOKENS, "kb": KB}
    doc.update(overrides)
    return copy.deepcopy(doc)


def plain_instance(**overrides: Any) -> dict[str, Any]:
    """Two groups, agents with read or full access, an inactive agent, one customer (in no organisation) who holds
    every ticket, an internal article with an attachment, no knowledge base: for scenarios about one sync mechanism, so
    a failure in them comes from that mechanism and not from an access rule."""
    users = [copy.deepcopy(u) for u in USERS if u["key"] in ("ann", "ben", "gus")]
    users[1]["groups"] = {"support": ["read"], "billing": ["read"]}
    users.append({"key": "carol", "email": "carol@example.com", "firstname": "Carol", "roles": ["Customer"]})
    tickets = [{"key": "laptop", "title": "Laptop will not boot", "group": "support", "customer": "carol",
                "articles": [{"body": "Black screen.", "sender": "Customer"},
                             {"key": "laptop_note", "body": "Internal note.", "internal": True, "sender": "Agent",
                              "attachments": [{"filename": "boot.txt", "content": "firmware handover failed"}]}]},
               {"key": "printer", "title": "Printer jams", "group": "support", "customer": "carol"},
               {"key": "invoice", "title": "Invoice 4471", "group": "billing", "customer": "carol",
                "articles": [{"body": "Billed twice.", "attachments": [{"filename": "invoice.pdf", "size": 3000}]}]}]
    doc: dict[str, Any] = {"groups": GROUPS[:2], "users": users, "tickets": tickets, "links": LINKS[:1],
                           "tokens": TOKENS}
    doc.update(overrides)
    return copy.deepcopy(doc)

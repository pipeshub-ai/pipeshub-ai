# pyright: ignore-file

"""The Zendesk accounts the zendesk_sim suite loads into the simulator (``PUT /_sim/zendesk/scenario``).

Every account is the tenant ``pipeshubsim``: the connector calls ``https://pipeshubsim.zendesk.com`` and the attachment
CDN, both answered by the overlay's TLS front. ``public_base`` and ``cdn_base`` name those https hosts, so the links the
simulator writes (web URLs, the redirect of an attachment download) point at hosts the connector's own checks "this is
the tenant's host" and "this is a Zendesk CDN host" accept for real. Nothing here is a real account: the people,
addresses and OAuth client are made up.

Each person with an address has a PipesHub account with that address (made once per session), so the permission query
can be asked for them; ``OUTSIDER`` is a workspace member with no Zendesk account.
"""

from __future__ import annotations

import os
from typing import Any

SUBDOMAIN = "pipeshubsim"
TENANT_HOST = f"{SUBDOMAIN}.zendesk.com"
PUBLIC_BASE = f"https://{TENANT_HOST}"
CDN_HOST = "p1.zdusercontent.com"
CDN_BASE = f"https://{CDN_HOST}"
OUTSIDER = "outsider@corp.example"
# The PipesHub account the suite's API client signs in as: a streamed record is read as this person, so every account
# makes them a Zendesk admin (who reads every ticket and article).
CLIENT_EMAIL = os.getenv("PIPESHUB_TEST_USER_EMAIL", "pipeshub-it@example.com")
# Where the platform's OAuth callback is served: FRONTEND_PUBLIC_URL of the stack plus the connector callback path.
REDIRECT_URI = "http://localhost:25200/connectors/oauth/callback/Zendesk"
OAUTH = {"client_id": "pipeshub-client", "client_secret": "pipeshub-secret", "access_token": "zd-sim-access-1",
         "refresh_token": "zd-sim-refresh-1", "redirect_uri": REDIRECT_URI, "user": "admin", "scope": "read"}


def desk(*, custom_roles: bool = False, real_clock: bool = True) -> dict[str, Any]:
    """A small desk covering every access rule.

    custom_roles=False mirrors a trial: custom roles list only the system roles and a restricted agent carries
    `ticket_restriction` with no custom role. custom_roles=True gives agents custom roles with each ticket access.
    """
    def agent(key: str, name: str, groups: list[str], *, role: str | None = None, restriction: str | None = "__none__",
              org: str | None = None) -> dict[str, Any]:
        user: dict[str, Any] = {"key": key, "name": name, "email": f"{key.replace('_', '-')}@corp.example",
                                "role": "agent", "groups": groups}
        if org:
            user["organization"] = org
        if custom_roles and role:
            user["custom_role"] = role
        elif restriction != "__none__":
            user["ticket_restriction"] = restriction
        return user

    roles: list[dict[str, Any]] = [{"key": "r_admin", "name": "Admin", "ticket_access": "all", "role_type": 4},
                                   {"key": "r_light", "name": "Light agent", "ticket_access": "within-groups",
                                    "role_type": 1}]
    if custom_roles:
        roles += [{"key": "r_all", "name": "Team lead", "ticket_access": "all"},
                  {"key": "r_groups", "name": "Staff", "ticket_access": "within-groups"},
                  {"key": "r_pub", "name": "Advisor", "ticket_access": "within-groups-and-public-groups"},
                  {"key": "r_assigned", "name": "Assigned only", "ticket_access": "assigned-only"},
                  {"key": "r_org", "name": "Org only", "ticket_access": "within-organization"}]
    users: list[dict[str, Any]] = [
        {"key": "admin", "name": "Admin", "email": "admin@corp.example", "role": "admin", "custom_role": "r_admin",
         "groups": ["support"]},
        {"key": "client", "name": "Suite", "email": CLIENT_EMAIL, "role": "admin", "custom_role": "r_admin",
         "groups": ["support"]},
        {"key": "light", "name": "Light", "email": "light@corp.example", "role": "agent", "custom_role": "r_light",
         "groups": ["support"]},
        agent("agent_all", "Agent All", ["support"], role="r_all", restriction=None),
        agent("agent_groups", "Agent Groups", ["support"], role="r_groups", restriction="groups"),
        agent("agent_billing", "Agent Billing", ["billing"], role="r_groups", restriction="groups"),
        agent("agent_secret", "Agent Secret", ["secret"], role="r_groups", restriction="groups"),
        agent("agent_assigned", "Agent Assigned", ["support"], role="r_assigned", restriction="assigned"),
        agent("agent_org", "Agent Org", ["support"], role="r_org", restriction="organization", org="org_private"),
    ]
    if custom_roles:
        users.append(agent("agent_pub", "Agent Pub", ["billing"], role="r_pub"))
    users += [
        {"key": "suspended", "name": "Suspended", "email": "suspended@corp.example", "role": "agent",
         "groups": ["support"], "ticket_restriction": "groups", "suspended": True},
        {"key": "cust_shared1", "name": "Cust Shared 1", "email": "shared1@customer.example",
         "organization": "org_shared"},
        {"key": "cust_shared2", "name": "Cust Shared 2", "email": "shared2@customer.example",
         "organization": "org_shared"},
        {"key": "cust_priv1", "name": "Cust Priv 1", "email": "priv1@customer.example", "organization": "org_private"},
        {"key": "cust_priv2", "name": "Cust Priv 2", "email": "priv2@customer.example", "organization": "org_private"},
        {"key": "cust_none", "name": "Cust None", "email": "none@customer.example"},
        {"key": "cust_cc", "name": "Cust CC", "email": "cc@customer.example"},
        {"key": "cust_noemail", "name": "No Email", "email": None},
    ]
    return {
        "subdomain": SUBDOMAIN, "public_base": PUBLIC_BASE, "cdn_base": CDN_BASE, "seed": 7, "clock": "real" if real_clock else "virtual", "oauth": dict(OAUTH),
        "custom_roles": roles,
        "groups": [{"key": "support", "name": "Support", "default": True}, {"key": "billing", "name": "Billing"},
                   {"key": "secret", "name": "Secret", "is_public": False}],
        "organizations": [{"key": "org_shared", "name": "Shared Org", "shared_tickets": True},
                          {"key": "org_private", "name": "Private Org"}],
        "users": users,
        "attachments": [
            {"key": "att_public", "name": "invoice.pdf", "type": "application/pdf", "content": "%PDF-1.4 public attachment"},
            {"key": "att_private", "name": "internal.txt", "content": "internal attachment"},
            {"key": "att_inline", "name": "screenshot.png", "type": "image/png", "inline": True, "size": 64}],
        "tickets": [
            {"key": "t_support", "subject": "Support ticket", "requester": "cust_shared1", "group": "support",
             "comments": [
                 {"author": "cust_shared1", "body": "Support ticket - description", "public": True},
                 {"key": "c_public", "author": "agent_all", "body": "Public reply with invoice", "public": True,
                  "attachments": ["att_public", "att_inline"]},
                 {"key": "c_private", "author": "agent_all", "body": "SECRET-INTERNAL-NOTE do not share",
                  "public": False, "attachments": ["att_private"]}]},
            {"key": "t_billing", "subject": "Billing ticket", "requester": "cust_priv1", "group": "billing"},
            {"key": "t_secret", "subject": "Secret ticket", "requester": "cust_none", "group": "secret"},
            {"key": "t_unassigned", "subject": "Unassigned ticket", "requester": "cust_none", "group": None},
            {"key": "t_assigned", "subject": "Assigned ticket", "requester": "cust_priv2", "group": "support",
             "assignee": "agent_assigned"},
            {"key": "t_agent_req", "subject": "Agent as requester", "requester": "agent_groups", "group": "billing"},
            {"key": "t_private_first", "subject": "Internal first comment", "requester": "cust_shared2",
             "group": "support", "first_comment_public": False, "description": "internal-only opening note"},
            {"key": "t_cc", "subject": "Ticket with CCs", "requester": "cust_priv1", "group": "billing",
             "followers": ["agent_assigned"], "email_ccs": ["cust_cc"], "collaborators": ["agent_assigned", "cust_cc"]},
            {"key": "t_noemail", "subject": "No-email requester", "requester": "cust_noemail", "group": "support"}],
        "categories": [{"key": "cat", "name": "General"}],
        "sections": [{"key": "sec", "name": "How-to", "category": "cat"},
                     {"key": "subsec", "name": "Advanced", "category": "cat", "parent": "sec"}],
        "user_segments": [{"key": "seg_staff", "name": "Agents and admins", "user_type": "staff", "built_in": True},
                          {"key": "seg_signed_in", "name": "Signed-in users", "user_type": "signed_in_users",
                           "built_in": True}],
        "articles": [
            {"key": "a_public", "title": "Public article", "section": "sec",
             "body": "<h1>Welcome</h1><p>PUBLIC-ARTICLE-BODY</p>",
             "attachments": [{"key": "hc_pdf", "name": "guide.pdf", "content": "%PDF article attachment"},
                             {"key": "hc_img", "name": "diagram.png", "type": "image/png", "inline": True,
                              "size": 32}],
             "comments": [{"body": "Useful, thanks", "author": "cust_none"}]},
            {"key": "a_sub", "title": "Subsection article", "section": "subsec", "body": "<p>nested</p>"},
            {"key": "a_draft", "title": "Draft article", "section": "sec", "draft": True},
            {"key": "a_staff", "title": "Staff article", "section": "sec", "segment": "seg_staff",
             "body": "<p>STAFF-ONLY</p>"},
            {"key": "a_signed", "title": "Signed-in article", "section": "sec", "segment": "seg_signed_in"}],
    }


def small(*, real_clock: bool = True) -> dict[str, Any]:
    """An admin, a group, two customers and three tickets: for paging, fault and token scenarios."""
    return {
        "subdomain": SUBDOMAIN, "public_base": PUBLIC_BASE, "cdn_base": CDN_BASE, "seed": 3, "clock": "real" if real_clock else "virtual", "oauth": dict(OAUTH),
        "groups": [{"key": "support", "name": "Support", "default": True}],
        "organizations": [{"key": "org", "name": "Org", "shared_tickets": True}],
        "users": [{"key": "admin", "name": "Admin", "email": "admin@corp.example", "role": "admin",
                   "groups": ["support"]},
                  {"key": "client", "name": "Suite", "email": CLIENT_EMAIL, "role": "admin", "groups": ["support"]},
                  {"key": "alice", "name": "Alice", "email": "alice@customer.example", "organization": "org"},
                  {"key": "bob", "name": "Bob", "email": "bob@customer.example", "organization": "org"}],
        "tickets": [{"key": f"t{n}", "subject": f"Ticket {n}", "requester": "alice" if n % 2 else "bob",
                     "group": "support"} for n in range(1, 4)],
        "categories": [{"key": "cat", "name": "General"}],
        "sections": [{"key": "sec", "name": "How-to", "category": "cat"}],
        "articles": [{"key": "a1", "title": "Article 1", "section": "sec"}],
    }


def it_seed() -> dict[str, Any]:
    """The account the stack suite `integration-tests/connectors/zendesk` reads, with the ids its constants name:
    ticket 4 (in a group, with a requester, an assignee, tags, a public and a private comment) and one published
    article (39571438662930) in a section. The subdomain and OAuth client come from the suite's environment
    (ZENDESK_TEST_SUBDOMAIN, _CLIENT_ID, _CLIENT_SECRET); a caller replaces `subdomain` and `oauth` to match."""
    return {
        "subdomain": SUBDOMAIN, "public_base": PUBLIC_BASE, "cdn_base": CDN_BASE, "seed": 11, "clock": "real", "oauth": dict(OAUTH),
        "groups": [{"key": "support", "name": "Support", "default": True}],
        "organizations": [{"key": "org", "name": "Org"}],
        "users": [{"key": "admin", "name": "Admin", "email": "admin@corp.example", "role": "admin",
                   "groups": ["support"]},
                  {"key": "client", "name": "Suite", "email": CLIENT_EMAIL, "role": "admin", "groups": ["support"]},
                  {"key": "agent", "name": "Agent", "email": "agent@corp.example", "role": "agent",
                   "groups": ["support"]},
                  {"key": "customer", "name": "Customer", "email": "customer@customer.example"}],
        "tickets": [
            {"id": 1, "subject": "Spare seed ticket", "requester": "customer", "group": "support",
             "assignee": "agent", "tags": ["pipeshub-seed"]},
            {"id": 4, "key": "seed", "subject": "PipesHub seed ticket", "requester": "customer", "group": "support",
             "assignee": "agent", "tags": ["pipeshub-seed"], "comments": [
                 {"author": "customer", "body": "Seed ticket opening comment", "public": True},
                 {"author": "agent", "body": "INTERNAL-ONLY seed note", "public": False}]}],
        "categories": [{"key": "cat", "name": "General"}],
        "sections": [{"key": "sec", "name": "How-to", "category": "cat"}],
        "articles": [{"id": 39571438662930, "key": "seed_article", "title": "PipesHub seed article",
                      "section": "sec", "body": "<p>Seed article body</p>"}],
    }


def _emails() -> list[str]:
    docs = (desk(custom_roles=True), small(), it_seed())
    return sorted({u["email"] for d in docs for u in d["users"] if u.get("email")})


# Everyone who may read something in any account: each has a PipesHub account (made once per session), so the permission
# query can be asked for them. OUTSIDER is a workspace member with no Zendesk account. The addresses the cases hand to a
# new account (a re-used address) are the ones above.
PEOPLE = [*_emails(), OUTSIDER]


def bulk(n_tickets: int, n_users: int, n_articles: int) -> list[dict[str, Any]]:
    """Operations that add end users, tickets across the desk's groups, and articles."""
    return ([{"op": "add_user", "key": f"bulk_{n}", "name": f"Bulk {n}", "email": f"bulk{n}@customer.example",
              "organization": "org_shared" if n % 2 else "org_private"} for n in range(n_users)]
            + [{"op": "add_ticket", "subject": f"Bulk ticket {n}", "requester": f"bulk_{n % n_users}",
                "group": ("support", "billing", "secret", None)[n % 4]} for n in range(n_tickets)]
            + [{"op": "add_article", "title": f"Bulk article {n}", "section": "sec"} for n in range(n_articles)])

"""The app lock that orders grant writers and the external-collaborator reaper: one contract for the three graph stores.

The reaper of an app removes the flagged (``isExternalUser``) memberships of the app that no grant chain keeps. A chain is a principal's own grant
on a record or record group of the app, or its membership of a group, role or team holding such a grant. One lock per (org, app) orders them:

- a write that can make such a chain exist for app A (a grant on a record or record group of A, a membership of a group, role or team whose
  grants reach A, a restore of a record of A) holds A's lock shared, from before it writes until its transaction ends;
- the reaper of A holds A's lock exclusively from before it decides until its transaction ends.

A team, or a group or role without a connector, can keep memberships of any app of the org, apps added later included: a write of its
membership holds the org's lock instead, exclusively, and every reaper holds the org's lock (shared where the store has a shared lock) before
its app's. Locks are taken in that order: the org's, then the apps' by key.

So the reaper decides once every writer holding the lock has committed (its chain is read) or rolled back, and a writer that comes after it
waits, its chain then newer than the reaper's decision (the membership is made again by the writer's membership step, as for a reaper that
decided first). PostgreSQL holds the lock as an advisory lock; Neo4j and ArangoDB, without a shared lock, hold it exclusively on the app's node
or document, so writers of one app take turns.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

GRANTED = ("records", "recordGroups")      # a grant on one keeps memberships of the node's app
MEMBERSHIPS = ("groups", "roles")          # a membership of one: the app of the group's connector, or the org's lock without one
EVERY_APP = ("teams",)                     # a membership of a team: the org's lock (a team has no connector)


def lock_targets(grants: Sequence[tuple[str, str, str, str]]) -> list[tuple[str, str]]:
    """The nodes whose locks a write of ``grants`` (holder collection, holder, target collection, target) takes, as (collection, key),
    sorted: each grant's target. A record's, record group's, group's or role's is its app's (its ``connectorId``); a team's, or a group's or
    role's without a connector, the org's."""
    return sorted({(tc, ti) for _, _, tc, ti in grants if tc in (*GRANTED, *MEMBERSHIPS, *EVERY_APP)})

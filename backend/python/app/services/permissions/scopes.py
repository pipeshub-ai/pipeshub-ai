"""`ViewerScopes`: what one viewer may reach, in the one shape every vector store translates.

A search pre-filter for any vector store reads only this object, so the semantics live here and not in a store. It is plain data: frozen, sorted on the wire, no store types.

Ids are the graph's ids. Apps and record groups are *containers* (a record that carries no scope fields of its
own is readable exactly when its record group or app is). A *scope* is a record or record group with an
access list of its own; a *restricted* scope is one that cannot be passed on its parent's access alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

#: `ViewerScopes.version` when the scopes were not read through the version counter (cache off or unreachable).
UNVERSIONED = -1


@dataclass(frozen=True)
class ViewerScopes:
    org_id: str
    user_key: str
    #: Apps the viewer passes the connector gate for, less `legacy_apps`. This is the gate, NOT "may read every record of the app": no
    #: filter may match `connectorIds` against it. It tells the post-check and the "path reaches the app" arm.
    gated_apps: frozenset[str] = frozenset()
    #: Gated apps whose membership admits by itself (APP_LEVEL permission model or a KB). The only apps a filter may
    #: match `connectorIds` against.
    open_apps: frozenset[str] = frozenset()
    #: Record groups the graph check admits. Filter fields: `recordGroupIds`, `rootRecordGroupIds`.
    readable_groups: frozenset[str] = frozenset()
    #: Scopes (records or groups with their own access list) the graph check admits.
    readable_scopes: frozenset[str] = frozenset()
    #: Restricted scopes of the viewer's apps that the graph check does not admit: a path through one hides.
    unreadable_restricted: frozenset[str] = frozenset()
    #: `u:<user>` and `g:<id>` for every group, role, team and the organisation the viewer holds.
    principal_tokens: frozenset[str] = frozenset()
    #: Scopes whose subtree is being rewritten; the filter hides them like unreadable restricted ones.
    pending: frozenset[str] = frozenset()
    #: Gated apps this object does not describe: their groups, scopes and open flag are left out, so a filter built from
    #: it admits nothing of them. Their records must be read through the legacy filter (the path filter is not exact
    #: for them). A consumer that ignores this field under-admits; it cannot over-admit.
    legacy_apps: frozenset[str] = frozenset()
    #: The ACL version the scopes were computed at; `UNVERSIONED` when not read through the counter.
    version: int = UNVERSIONED

    @property
    def readable_containers(self) -> frozenset[str]:
        """What a record that carries no scope fields of its own is matched against: its record group, or its app if
        the app opens everything."""
        return self.open_apps | self.readable_groups

    def with_version(self, version: int) -> ViewerScopes:
        return replace(self, version=version)

    def to_json(self) -> str:
        return json.dumps({
            "org": self.org_id, "user": self.user_key, "gated": sorted(self.gated_apps), "open": sorted(self.open_apps),
            "groups": sorted(self.readable_groups), "scopes": sorted(self.readable_scopes),
            "unreadable": sorted(self.unreadable_restricted), "tokens": sorted(self.principal_tokens),
            "pending": sorted(self.pending), "legacy": sorted(self.legacy_apps), "version": self.version,
        }, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> ViewerScopes:
        """Raises ValueError, KeyError or TypeError on anything that is not what `to_json` wrote."""
        d = json.loads(raw)
        return cls(d["org"], d["user"], frozenset(d["gated"]), frozenset(d["open"]), frozenset(d["groups"]), frozenset(d["scopes"]),
                   frozenset(d["unreadable"]), frozenset(d["tokens"]), frozenset(d["pending"]), frozenset(d["legacy"]),
                   int(d["version"]))

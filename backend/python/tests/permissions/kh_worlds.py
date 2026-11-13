"""Worlds the Knowledge Hub conformance layer checks beside the golden scenarios and the generated worlds.

CROSS2: records of one connector reached through the groups of another (a knowledge base, an app-level connector, a record that belongs
straight to an app, a hierarchy-only parent). Every arm of the listing must apply the same connector check: a row the access check denies
is neither listed nor counted. Written in the same shape as the golden worlds; it is not a scenario (nothing changes in it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from .scenario import world_from_data

if TYPE_CHECKING:
    from .model import World

# more cross-connector shapes: BELONGS_TO the app, hierarchy-only, a KB, an app-level connector, a shared record group name
CROSS2 = world_from_data(yaml.safe_load("""
principals: [{id: u}, {id: w}]
nodes:
  - {id: M, kind: app, app_type: MAIL}
  - {id: D, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: KB, kind: app, app_type: KB}
  - {id: mb, kind: group, connector: M, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [M], belongs: [M]}
  - {id: dg, kind: group, connector: D, rule: OPEN, parents: [D], belongs: [D]}
  - {id: kg, kind: group, connector: KB, rule: OPEN, parents: [KB], belongs: [KB]}
  - {id: m1, kind: record, connector: M, rule: OPEN, parents: [mb], inherits: [mb], belongs: [mb]}
  - {id: y1, kind: record, connector: D, rule: OPEN, parents: [mb], belongs: [M], indexing: FAILED}
  - {id: y2, kind: record, connector: D, rule: STRICT, parents: [mb], indexing: FAILED}
  - {id: y3, kind: record, connector: M, rule: STRICT, parents: [kg], belongs: [kg], indexing: FAILED}
  - {id: y4, kind: record, connector: M, rule: STRICT, parents: [dg], belongs: [dg], indexing: FAILED}
  - {id: y5, kind: record, connector: D, rule: OPEN, parents: [dg], inherits: [dg], belongs: [dg]}
  - {id: y6, kind: record, connector: KB, rule: STRICT, belongs: [mb], indexing: FAILED}
  - {id: y7, kind: record, connector: D, rule: STRICT, belongs: [KB], indexing: FAILED}
grants: [[u, mb], [u, dg], [u, kg], [w, kg]]
gates: [[u, M], [u, D], [u, KB], [w, KB]]
"""))


# a folder that holds a hidden child (the arrow must not say it has children), one with none, and one that holds only hidden ones
ARROW = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: g, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: withhidden, kind: record, connector: A, rule: OPEN, parents: [g], inherits: [g], belongs: [g]}
  - {id: hiddenkid, kind: record, connector: A, rule: RESTRICTED, parents: [withhidden]}
  - {id: empty, kind: record, connector: A, rule: OPEN, parents: [g], inherits: [g], belongs: [g]}
grants: [[u, g]]
gates: [[u, A]]
"""))

# a listing of more than one page in one group, so the cursor and the browse path both meet a carried total
WALK = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: g, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
""" + "".join(f"  - {{id: r{i:02d}, kind: record, connector: A, rule: OPEN, parents: [g], inherits: [g], belongs: [g]}}\n" for i in range(12)) + """
grants: [[u, g]]
gates: [[u, A]]
"""))

# A group holding a record the viewer is granted, whose parent the viewer may open: the record is listed under that parent, not under its group, so
# the group lists nothing and shows no arrow (HELD); where the parent is not openable the record is a chain-top listed under its group, which shows one (CHAIN)
HELD = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: p, kind: record, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [p], inherits: [p], belongs: [g]}
grants: [[u, h], [u, r]]
gates: [[u, A]]
"""))

CHAIN = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: p, kind: record, connector: A, rule: RESTRICTED, parents: [h], inherits: [h], belongs: [h]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [p], belongs: [g]}
grants: [[u, h], [u, r]]
gates: [[u, A]]
"""))

# What hangs under a declaration (a RECORD_GROUP_LEVEL group) is read through its membership, not its hierarchy: a browse lists none of the four
# below, though each hangs by a hierarchy edge from a node the viewer may open.
# STRICT: a record granted to the viewer, but strict (only an OPEN node a viewer is granted is a seed)
DECL_STRICT = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: g, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: x, kind: record, connector: A, rule: STRICT, parents: [g], inherits: [g]}
grants: [[u, g], [u, x]]
gates: [[u, A]]
"""))

# SEEDED: a declaration the viewer reaches only below a seed (no grant of its own, no walk from the App) declares no scope
DECL_SEEDED = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: r, kind: group, connector: A, rule: RESTRICTED, parents: [A], belongs: [A]}
  - {id: s, kind: group, connector: A, rule: OPEN, parents: [r], belongs: [r]}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [s], inherits: [s]}
  - {id: b, kind: group, connector: A, rule: STRICT, parents: [d], inherits: [d]}
grants: [[u, s]]
gates: [[u, A]]
"""))

# BELOW: a group inside a declaration is admitted by it, so what hangs under that group is not read through it
DECL_BELOW = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: e, kind: group, connector: A, rule: OPEN, parents: [d], inherits: [d]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [e], inherits: [e]}
grants: [[u, d]]
gates: [[u, A]]
"""))

# MEMBER: a record that belongs to a declaration is admitted by it, even when granted, so what hangs under it is not read through it
DECL_MEMBER = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: m, kind: record, connector: A, rule: OPEN, parents: [d], belongs: [d]}
  - {id: c, kind: record, connector: A, rule: OPEN, parents: [m], inherits: [m]}
grants: [[u, d], [u, m]]
gates: [[u, A]]
"""))

# STUB: a folder whose only child is a stub; the way below the stub is cut by a node the viewer cannot open (q), though the last hop (to z, a node
# granted to the viewer) passes the browse rule: the stub is not listed, so the folder shows no arrow
STUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: q, kind: record, connector: A, rule: RESTRICTED, parents: [s], inherits: [s]}
  - {id: z, kind: record, connector: A, rule: OPEN, parents: [q]}
grants: [[u, h], [u, z]]
gates: [[u, A]]
"""))

# STUBHELD: the stub above, and beside it a granted record that is a chain-top of the same group: the group lists the record, so it shows an arrow
STUBHELD = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: q, kind: record, connector: A, rule: RESTRICTED, parents: [s], inherits: [s]}
  - {id: z, kind: record, connector: A, rule: OPEN, parents: [q]}
  - {id: p, kind: record, connector: A, rule: RESTRICTED, parents: [h], inherits: [h], belongs: [h]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [p], belongs: [f]}
grants: [[u, h], [u, z], [u, r]]
gates: [[u, A]]
"""))

# DEEP: a chain of three stubs that leads to a granted record: each is listed, as the way to it
DEEP = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: s1, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: s2, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [s1], inherits: [s1], belongs: [f]}
  - {id: s3, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [s2], inherits: [s2], belongs: [f]}
  - {id: z, kind: record, connector: A, rule: OPEN, parents: [s3], inherits: [s3]}
grants: [[u, h], [u, z]]
gates: [[u, A]]
"""))

# MIXED: two stubs under one folder, one leads to a granted record and one to nothing: only the first is listed
MIXED = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: sa, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: za, kind: record, connector: A, rule: OPEN, parents: [sa], inherits: [sa]}
  - {id: sb, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
grants: [[u, h], [u, za]]
gates: [[u, A]]
"""))

# CUT: a stub whose way is cut in the middle (a node the viewer cannot open, then a second stub) before a granted record
CUT = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [h], inherits: [h], belongs: [h]}
  - {id: s1, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: q, kind: record, connector: A, rule: RESTRICTED, parents: [s1], inherits: [s1]}
  - {id: s2, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [q], inherits: [q]}
  - {id: z, kind: record, connector: A, rule: OPEN, parents: [s2], inherits: [s2]}
grants: [[u, h], [u, z]]
gates: [[u, A]]
"""))

# OPENCUT: an App that opens everything to its members does not open what hangs below a seed it reaches only through a declaration: the seed is
# read as a seed, so a strict node under it is not listed
OPENCUT = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: k, kind: record, connector: A, rule: OPEN, parents: [d]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [k], inherits: [k]}
grants: [[u, k]]
gates: [[u, A]]
"""))

# OPENKEPT: but a record that belongs to such an App is read as a member wherever it hangs, so the same shape with a member is listed
OPENKEPT = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: k, kind: record, connector: A, rule: OPEN, parents: [d]}
  - {id: m, kind: record, connector: A, rule: STRICT, parents: [k], inherits: [k], belongs: [A]}
grants: [[u, k]]
gates: [[u, A]]
"""))

# RESTRICTEDMEMBER: a member of a granted declaration that hangs under a reached folder without inheriting from it is listed there (as the member), but
# the walk from the App does not reach it, so what hangs under it is read as below a seed: a strict child is not listed, and its parent shows no arrow
RESTRICTEDMEMBER = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: m, kind: record, connector: A, rule: RESTRICTED, parents: [f], belongs: [d]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [m], inherits: [m]}
grants: [[u, d], [u, f], [u, m]]
gates: [[u, A]]
"""))

# CHAINLINK: a granted chain-top that the group only links to (no hierarchy edge) is listed under the group as a chain-top; the stub below it is
# read as below a seed (a strict stub is not listed), so the chain-top shows no arrow
CHAINLINK = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [{id: h, rel: LINKED_TO}], belongs: [h]}
  - {id: s, kind: record, connector: A, rule: STRICT, placeholder: true, parents: [r]}
  - {id: z, kind: record, connector: A, rule: OPEN, parents: [s]}
grants: [[u, h], [u, r], [u, z]]
gates: [[u, A]]
"""))

# FILEDSTUB: in an App that opens everything a stub filed in a record is listed only when the way below it is open at every hop
FILEDSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: fr, kind: record, connector: A, rule: OPEN, parents: [g], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, belongs: [fr]}
  - {id: q, kind: record, connector: A, rule: RESTRICTED, parents: [s]}
  - {id: z, kind: record, connector: A, rule: OPEN, parents: [q]}
grants: [[u, z]]
gates: [[u, A]]
"""))

# OPENSTUB: in an App that opens everything a stub the walk from the App reaches is listed as the way to a strict node and what hangs below it
OPENSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: p, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [g], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: STRICT, parents: [p]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [s]}
grants: []
gates: [[u, A]]
"""))

# APPCHAIN: a group listed under the App that holds a granted record with no hierarchy edge: opening the group lists the record as a chain-top, so its
# arrow is on
APPCHAIN = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: r, kind: record, connector: A, rule: OPEN, belongs: [h]}
grants: [[u, h], [u, r]]
gates: [[u, A]]
"""))

# DECLSTUB: a stub in a declared group the walk from the App reaches, granted to the viewer, over a strict node it leads to: a browse of the stub
# starts below a seed (the walk does not pass the declaration), where the strict node is not read, so the group lists no stub and shows no arrow
DECLSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [g], inherits: [g], belongs: [g]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [d], belongs: [d]}
  - {id: t, kind: record, connector: A, rule: STRICT, parents: [s], inherits: [s]}
grants: [[u, g], [u, s]]
gates: [[u, A]]
"""))

# TWOWAY: a stub below two folders, which the App reaches through q and not through p (p does not pass it on): a browse of the stub starts from
# the App and lists the strict node below it, so the browse of p lists the stub as the way to it, with an arrow
TWOWAY = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: p, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [p, q], inherits: [q]}
  - {id: t, kind: record, connector: A, rule: STRICT, parents: [s], inherits: [s]}
grants: [[u, p], [u, q]]
gates: [[u, A]]
"""))

# SCOPEDTWO: the same through the declared-scope arm: p lists the stub as a member of a declared group the viewer is granted, though p does not pass
# it on, and the App reaches it through q
SCOPEDTWO = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: p, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: RESTRICTED, placeholder: true, parents: [p, q], inherits: [q], belongs: [d]}
  - {id: t, kind: record, connector: A, rule: STRICT, parents: [s], inherits: [s]}
grants: [[u, d], [u, p], [u, q], [u, s]]
gates: [[u, A]]
"""))

# FOLDERSTUB: a folder (a record) holding a stub its rule lists and its reach reaches, beside more records than one page holds: the stub is one row
FOLDERSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: f, kind: record, connector: A, rule: OPEN, parents: [g], inherits: [g], belongs: [g]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [s], inherits: [s]}
""" + "".join(f"  - {{id: f{i}, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f]}}\n" for i in range(8)) + """
grants: [[u, g]]
gates: [[u, A]]
"""))


def _deep_chain(app: str, grants: bool) -> World:
    """56 OPEN records in a chain below a group of the App: past 50 hops the walk from the App stops, and each is read as a seed (``grants``) or as
    a member of an App that opens everything."""
    nodes = "".join(f"  - {{id: c{i}, kind: record, connector: A, rule: OPEN, parents: [{'g' if i == 0 else f'c{i - 1}'}], "
                    f"inherits: [{'g' if i == 0 else f'c{i - 1}'}], belongs: [A]}}\n" for i in range(DEEP_CHAIN))
    granted = "".join(f", [u, c{i}]" for i in range(DEEP_CHAIN)) if grants else ""
    return world_from_data(yaml.safe_load(f"""
principals: [{{id: u}}]
nodes:
  - {app}
  - {{id: g, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A], inherits: [A]}}
{nodes}grants: [[u, g]{granted}]
gates: [[u, A]]
"""))


# DEEPSEED / DEEPOPEN: a chain deeper than the walk's cap; the oracle admits every link with no hop cap (a seed, a member of an App that opens
# everything), so each is listed under its parent and opens on the next, past depth 50 as above it
DEEP_CHAIN = 56
DEEPSEED = _deep_chain("{id: A, kind: app, app_type: DRIVE}", grants=True)
DEEPOPEN = _deep_chain("{id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}", grants=False)

# A group of another connector filed under a declared group: the declared scope is read group to group, whatever the connector, but the check
# decides a node in its own App, so the group is not listed in this App, and the declared group, which holds nothing else, has no arrow (XDECL).
XDECL = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: B, kind: app, app_type: DRIVE}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: x, kind: group, connector: B, rule: OPEN, parents: [d], inherits: [d], belongs: [d]}
  - {id: y, kind: record, connector: B, rule: OPEN, parents: [x], inherits: [x], belongs: [x]}
grants: [[u, d]]
gates: [[u, A], [u, B]]
"""))

# OPENGROUP: an App that opens everything reads a record that belongs to it as a member wherever it hangs, but not a group: a strict group that
# belongs to the App, below a seed the walk from the App does not reach, is not listed; an OPEN group that inherits from the seed is (below a seed).
OPENGROUP = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: k, kind: record, connector: A, rule: OPEN, parents: [d]}
  - {id: gs, kind: group, connector: A, rule: STRICT, parents: [k], belongs: [A]}
  - {id: go, kind: group, connector: A, rule: OPEN, parents: [k], inherits: [k], belongs: [A]}
grants: [[u, k]]
gates: [[u, A]]
"""))

# XSTUB: stubs of another connector inside this App's hierarchy (one a direct child of the App, one below a record), each over a record of this App
# the walk reaches: a row is listed only in its own App, so neither stub is listed here, and the record above the second shows no arrow.
XSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: B, kind: app, app_type: DRIVE}
  - {id: sa, kind: record, connector: B, rule: OPEN, placeholder: true, parents: [A]}
  - {id: za, kind: record, connector: A, rule: OPEN, parents: [sa]}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [g]}
  - {id: sb, kind: record, connector: B, rule: OPEN, placeholder: true, parents: [r]}
  - {id: zb, kind: record, connector: A, rule: OPEN, parents: [sb]}
grants: []
gates: [[u, A], [u, B]]
"""))

# XHOP: the walk from App A passes through x, a RESTRICTED record of App B under A that the viewer is granted in B: a grant counts on a node of any
# gated App. So c (a STRICT record of A under the folder f and under x, inheriting from x only) is read through x and listed and counted in the
# flatten, and g (a group of A under x, inheriting from it) is opened from A through x and lists d.
XHOP = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: B, kind: app, app_type: DRIVE}
  - {id: f, kind: record, connector: A, rule: OPEN, parents: [A]}
  - {id: x, kind: record, connector: B, rule: RESTRICTED, parents: [A], inherits: [A]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [f, x], inherits: [x]}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [x], inherits: [x], belongs: [A]}
  - {id: d, kind: record, connector: A, rule: OPEN, parents: [g], inherits: [g], belongs: [g]}
grants: [[u, f], [u, x]]
gates: [[u, A], [u, B]]
"""))

KH_WORLDS = [CROSS2, ARROW, WALK, HELD, CHAIN, DECL_STRICT, DECL_SEEDED, DECL_BELOW, DECL_MEMBER, STUB, STUBHELD, DEEP, MIXED, CUT, OPENCUT, OPENKEPT,
             RESTRICTEDMEMBER, CHAINLINK, FILEDSTUB, OPENSTUB, APPCHAIN, DECLSTUB, TWOWAY, SCOPEDTWO, FOLDERSTUB, XDECL,
             OPENGROUP, XSTUB, XHOP]

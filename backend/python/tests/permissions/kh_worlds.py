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

# STUBREACH: a granted OPEN stub inside a declaration, above a strict node that inherits from it. Whether the stub is listed under the declaration must be read with the reach a browse of
# the stub itself starts with, never the declaration's: the stub is listed (with an arrow) exactly when opening it lists something
STUBREACH = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [d], inherits: [d], belongs: [d]}
  - {id: t, kind: record, connector: A, rule: STRICT, parents: [s], inherits: [s]}
grants: [[u, d], [u, s]]
gates: [[u, A]]
"""))

# STUBTOP: a placeholder group whose only content is a chain-top: a granted record that belongs to it and hangs from nothing. Opening the group lists the record, so the folder above
# lists the group, with an arrow. (Not in KH_WORLDS: Neo4j's folder leaves the group out and ArangoDB lists the record under the App instead.)
STUBTOP = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: s, kind: group, connector: A, rule: OPEN, placeholder: true, parents: [f], inherits: [f], belongs: [f]}
  - {id: r, kind: record, connector: A, rule: OPEN, belongs: [s]}
grants: [[u, f], [u, r]]
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

# What the check admits by an arm the browse's own rules do not read is listed too: a node below two parents is read through the one the walk from the
# App reaches (SECOND); a member of an App that opens everything is read as one wherever it hangs (APPMEMBER).
SECOND = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: c, kind: record, connector: A, rule: OPEN, parents: [d, h], inherits: [h]}
grants: [[u, h], [u, d]]
gates: [[u, A]]
"""))

APPMEMBER = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: m, kind: record, connector: A, rule: STRICT, parents: [d], belongs: [A]}
grants: []
gates: [[u, A]]
"""))

# A grant to a principal of another connector opens the node as any grant does: a record of App B granted only to a role of App A (a role
# belongs to the first App) that the viewer holds is listed in its folder, opens, and passes the record check (XGRANT). Every principal's grants
# count, as in the oracle and the batch check.
XGRANT = world_from_data(yaml.safe_load("""
principals: [{id: u}, {id: r, kind: role}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: B, kind: app, app_type: GMAIL}
  - {id: f, kind: group, connector: B, rule: OPEN, parents: [B], belongs: [B]}
  - {id: m, kind: record, connector: B, rule: OPEN, parents: [f], belongs: [f]}
  - {id: n, kind: record, connector: B, rule: OPEN, parents: [f], belongs: [f]}
grants: [[u, f], [r, m]]
memberships: [[u, r]]
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

# DECLDEEP: a declaration the walk from the App reaches through a group (App -> G -> D), not granted, is a scope: its member M, which hangs under
# a folder F it does not inherit from, is listed there; M is no seed and the walk does not reach it, so what hangs under it is not read through it
# (a node below M that only inherits from it is not listed, and M shows no arrow).
DECLDEEP = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: G, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: D, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [G], inherits: [G], belongs: [G]}
  - {id: F, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: M, kind: record, connector: A, rule: STRICT, parents: [F], belongs: [D]}
  - {id: c, kind: record, connector: A, rule: OPEN, parents: [M], inherits: [M]}
grants: [[u, G], [u, F]]
gates: [[u, A]]
"""))

# CROSSREACH: a record R the walk from the App reaches through a second parent q, listed under a seed s the walk does not reach; a strict child of
# R that inherits from it is what opening R lists, so the folder listing that keeps only rows with children (the sidebar) keeps R.
CROSSREACH = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: x, kind: group, connector: A, rule: RESTRICTED, parents: [A], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: OPEN, parents: [x]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: R, kind: record, connector: A, rule: OPEN, parents: [s, q], inherits: [s, q]}
  - {id: t, kind: record, connector: A, rule: STRICT, parents: [R], inherits: [R]}
grants: [[u, s], [u, q]]
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

# SIDEKID: a record R whose only child C the walk reaches through another parent q (C does not inherit from R): opening R lists C (the check
# admits it), so R shows an arrow, and the sidebar (rows with children) keeps R.
SIDEKID = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: R, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: C, kind: record, connector: A, rule: STRICT, parents: [R, q], inherits: [q]}
grants: [[u, f], [u, q]]
gates: [[u, A]]
"""))

# APPSIDE: a record R under the App that the App's hop does not pass (the check admits it through q), with a strict child that inherits from it:
# opening R lists the child, so the App's sidebar keeps R.
APPSIDE = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: R, kind: record, connector: A, rule: STRICT, parents: [A, q], inherits: [q]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [R], inherits: [R]}
grants: [[u, q]]
gates: [[u, A]]
"""))

# STUBQ: a stub S whose only child C the walk reaches through another parent Q (C does not inherit from S): opening S lists C, so S is listed.
STUBQ = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: S, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [h], inherits: [h]}
  - {id: Q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: C, kind: record, connector: A, rule: STRICT, parents: [S, Q], inherits: [Q]}
grants: [[u, h], [u, Q]]
gates: [[u, A]]
"""))

# SIDESEED: a record R under a granted folder f, whose only child C inherits only from a seed s the walk from the App does not reach (s hangs under
# a restricted group the viewer is not granted): the check admits C below the seed, so opening R lists C, R shows an arrow, and the sidebar of f
# (rows with children) keeps R.
SIDESEED = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: R, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f]}
  - {id: x, kind: group, connector: A, rule: RESTRICTED, parents: [A], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: OPEN, parents: [x]}
  - {id: C, kind: record, connector: A, rule: OPEN, parents: [R, s], inherits: [s]}
grants: [[u, f], [u, s]]
gates: [[u, A]]
"""))

# STUBSEED: the same below a stub: a stub S under a granted group h whose only child C inherits only from a seed s the walk does not reach. Opening
# S lists C (the check admits it below the seed), so S is listed and h shows an arrow.
STUBSEED = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: S, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [h], inherits: [h]}
  - {id: x, kind: group, connector: A, rule: RESTRICTED, parents: [A], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: OPEN, parents: [x]}
  - {id: C, kind: record, connector: A, rule: OPEN, parents: [S, s], inherits: [s]}
grants: [[u, h], [u, s]]
gates: [[u, A]]
"""))

# NESTSTUB: a stub S whose only child is a stub T, whose only child C the check admits through another parent q (C does not inherit from T).
# Opening T lists C, so T is listed under S, so S is listed under h and h shows an arrow.
NESTSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: S, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [h], inherits: [h]}
  - {id: T, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [S], inherits: [S]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: C, kind: record, connector: A, rule: STRICT, parents: [T, q], inherits: [q]}
grants: [[u, h], [u, q]]
gates: [[u, A]]
"""))

# CAPSTUB: a stub S with one child more than an arrow asks the check about (`_KH_UNDECIDED_MAX`, the first by id): none of the first is readable,
# and the last, k200, is (through q). Opening S lists k200 (a browse asks about every undecided child), but S's listing under h asks about the
# first ones only, so S is not listed there (reported, as the arrow's sample); k200 is listed under q too.
CAP_KIDS = 201
CAPSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: h, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: S, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [h], inherits: [h]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
""" + "".join(f"  - {{id: k{i:03d}, kind: record, connector: A, rule: STRICT, parents: [S]}}\n" for i in range(CAP_KIDS - 1)) + f"""\
  - {{id: k{CAP_KIDS - 1:03d}, kind: record, connector: A, rule: STRICT, parents: [S, q], inherits: [q]}}
grants: [[u, h], [u, q]]
gates: [[u, A]]
"""))

# WIDE: a group f with more children its rules leave to the check than one read asks it about (`_KH_UNDECIDED_MAX`): each is readable only
# through q, the first ten and the last thirty (past the first 200 by id) are, the rest are not. A browse of f lists all forty in order, over
# pages whose first ones are decided before every child is asked about (a total that says it is a lower bound). g's ten children are all left to
# the check too, few enough to ask about at once: its second page starts at one of them.
WIDE_KIDS = 230
WIDE = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: g, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
""" + "".join(f"  - {{id: j{i:02d}, kind: record, connector: A, rule: STRICT, parents: [g, q], inherits: [q]}}\n" for i in range(10)) + "".join(
    f"  - {{id: k{i:03d}, kind: record, connector: A, rule: STRICT, parents: [f, q], inherits: [q]}}\n" if i < 10 or i >= 200 else
              f"  - {{id: k{i:03d}, kind: record, connector: A, rule: STRICT, parents: [f]}}\n" for i in range(WIDE_KIDS)) + """\
grants: [[u, f], [u, q], [u, g]]
gates: [[u, A]]
"""))

# HIDESEED: SIDESEED where C also hangs under a group H that hides its children. The check admits C below the seed (it does not read the display
# flag), but a browse of C refuses to open it (only the walk from the App, which never passes H, opens a node below a group that hides its
# children), so a browse of R does not list C, and R shows no arrow.
HIDESEED = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: R, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f]}
  - {id: H, kind: group, connector: A, rule: OPEN, hide_children: true, parents: [A], belongs: [A]}
  - {id: x, kind: group, connector: A, rule: RESTRICTED, parents: [A], belongs: [A]}
  - {id: s, kind: record, connector: A, rule: OPEN, parents: [x]}
  - {id: C, kind: record, connector: A, rule: OPEN, parents: [R, s, H], inherits: [s]}
grants: [[u, f], [u, s]]
gates: [[u, A]]
"""))

# FLATSIDE: a record f of a declared group D, with a member y as its hierarchy child. The flatten's sidebar keeps a record by the flatten's own
# arrow (the stricter rule): f's is off there, so the flatten's sidebar holds D alone, though a browse of D shows f with an arrow.
FLATSIDE = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: D, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: f, kind: record, connector: A, rule: OPEN, parents: [D], belongs: [D]}
  - {id: y, kind: record, connector: A, rule: OPEN, parents: [f], belongs: [D]}
grants: [[u, D]]
gates: [[u, A]]
"""))

# REACHSTUB: a stub S under R and q, and its child C, both inheriting only from q: the hops R -> S -> C fail, but the walk from the App reaches S
# through q, so a browse of R lists S (`reached_stubs`), as opening S lists C (the check admits it). R shows an arrow, and the sidebar of f keeps it.
REACHSTUB = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: R, kind: record, connector: A, rule: OPEN, parents: [f], inherits: [f]}
  - {id: q, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: S, kind: record, connector: A, rule: OPEN, placeholder: true, parents: [R, q], inherits: [q]}
  - {id: C, kind: record, connector: A, rule: STRICT, parents: [S, q], inherits: [q]}
grants: [[u, f], [u, q]]
gates: [[u, A]]
"""))

# DEPTHDEL: a readable record r three hierarchy hops below the App, through a deleted folder x. A flatten from the App bounded to three hops lists r (the
# bound counts hops through any node, as Neo4j's walk does); bounded to two it does not.
DEPTHDEL = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: G, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: x, kind: record, connector: A, rule: OPEN, deleted: true, parents: [G], inherits: [G]}
  - {id: r, kind: record, connector: A, rule: OPEN, parents: [x]}
grants: [[u, G], [u, r]]
gates: [[u, A]]
"""))

# SCOPEDSIDE: FLATSIDE's twin below a folder. R under a granted group G holds a strict child c that inherits from it. A flatten of G reads its rows'
# arrows from G's reach (the walk reaches G), so R shows an arrow there, and that flatten's sidebar keeps R.
SCOPEDSIDE = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: G, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: R, kind: record, connector: A, rule: OPEN, parents: [G], inherits: [G]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [R], inherits: [R]}
grants: [[u, G]]
gates: [[u, A]]
"""))

# DECLHIDE: HIDESEED's twin through the declared scope. c hangs under the granted folder f, which the walk from the App reaches, and under a group H
# that hides its children; the hop f -> c fails (c is strict and does not inherit from f), and c is a member of the granted declaration D. The
# check admits c by its declared arm, but a browse of c refuses to open it (the walk does not reach it, and the declared arm opens only where no
# hiding group lies above), so a browse of f does not list it, though the walk reaches f.
DECLHIDE = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: D, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: H, kind: group, connector: A, rule: OPEN, hide_children: true, parents: [A], belongs: [A]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [f, H], belongs: [D]}
grants: [[u, D], [u, f]]
gates: [[u, A]]
"""))

# KEYCLASH: an App that opens everything, and a strict record m below a seed the walk does not reach, which belongs to no App. The test adds, on
# ArangoDB only, a record group whose key is the App's key and files m in it: m belongs to that group, not to the App, so it is not listed.
KEYCLASH = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: k, kind: record, connector: A, rule: OPEN, parents: [d]}
  - {id: m, kind: record, connector: A, rule: STRICT, parents: [k]}
grants: [[u, k]]
gates: [[u, A]]
"""))

# OPENGRANT / OPENGRANTTEAM: OPENGROUP with the strict group granted to the viewer, directly or through a group of users. A grant does not open a strict group
# on its own: a strict node is read through a chain of passing hops from its App (R7) or a declaration (R8), and only an OPEN node can be a seed (R9) or lie
# below one (R10). Here the only chain runs through d, a declaration of the App (skipped by the walk), so gs stays unread and unlisted however it is granted.
OPENGRANT = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: k, kind: record, connector: A, rule: OPEN, parents: [d]}
  - {id: gs, kind: group, connector: A, rule: STRICT, parents: [k], belongs: [A]}
  - {id: go, kind: group, connector: A, rule: OPEN, parents: [k], inherits: [k], belongs: [A]}
grants: [[u, k], [u, gs]]
gates: [[u, A]]
"""))
OPENGRANTTEAM = world_from_data(yaml.safe_load("""
principals: [{id: u}, {id: t, kind: group}]
nodes:
  - {id: A, kind: app, app_type: DRIVE, model: APP_LEVEL}
  - {id: d, kind: group, connector: A, rule: OPEN, model: RECORD_GROUP_LEVEL, parents: [A], belongs: [A]}
  - {id: k, kind: record, connector: A, rule: OPEN, parents: [d]}
  - {id: gs, kind: group, connector: A, rule: STRICT, parents: [k], belongs: [A]}
  - {id: go, kind: group, connector: A, rule: OPEN, parents: [k], inherits: [k], belongs: [A]}
grants: [[u, k], [t, gs]]
memberships: [[u, t]]
gates: [[u, A]]
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

# XLINK: the viewer reads App B as the source account s, which is granted y (of B) and x (of C), both RESTRICTED records under App A. A source account's
# grant counts inside its own connector only: the walk from A passes y, so e (under y) is read, and not x, so c (under x) is not.
XLINK = world_from_data(yaml.safe_load("""
principals: [{id: u}, {id: s}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: B, kind: app, app_type: DRIVE}
  - {id: C, kind: app, app_type: DRIVE}
  - {id: y, kind: record, connector: B, rule: RESTRICTED, parents: [A], inherits: [A]}
  - {id: e, kind: record, connector: A, rule: STRICT, parents: [y], inherits: [y]}
  - {id: x, kind: record, connector: C, rule: RESTRICTED, parents: [A], inherits: [A]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [x], inherits: [x]}
grants: [[s, y], [s, x]]
gates: [[u, A], [u, C]]
links: [[u, s, B]]
"""))

# XBADGE: the viewer holds role r, a role of App R (a group, role or team is a connector's entity). A grant to a principal counts on a node of any
# gated App, whatever the principal's own connector: r's grant on x (a RESTRICTED record of App B on A's walk) opens c below it, and its grant on a
# (a RESTRICTED record of A) opens a itself.
XBADGE = world_from_data(yaml.safe_load("""
principals: [{id: u}, {id: r, kind: role}]
nodes:
  - {id: R, kind: app, app_type: DRIVE}
  - {id: A, kind: app, app_type: DRIVE}
  - {id: B, kind: app, app_type: DRIVE}
  - {id: x, kind: record, connector: B, rule: RESTRICTED, parents: [A], inherits: [A]}
  - {id: c, kind: record, connector: A, rule: STRICT, parents: [x], inherits: [x]}
  - {id: a, kind: record, connector: A, rule: RESTRICTED, parents: [A], inherits: [A]}
grants: [[r, x], [r, a]]
memberships: [[u, r]]
gates: [[u, A], [u, B]]
"""))

# XLINKBADGE: the viewer reads App B as the source account s, which holds role r of App R. The source account's grants, its roles' included, count
# inside B only, whatever the role's own connector: r's grant on y (of B, on A's walk) opens e below it; its grant on z (of A) opens nothing.
XLINKBADGE = world_from_data(yaml.safe_load("""
principals: [{id: u}, {id: s}, {id: r, kind: role}]
nodes:
  - {id: R, kind: app, app_type: DRIVE}
  - {id: A, kind: app, app_type: DRIVE}
  - {id: B, kind: app, app_type: DRIVE}
  - {id: y, kind: record, connector: B, rule: RESTRICTED, parents: [A], inherits: [A]}
  - {id: e, kind: record, connector: A, rule: STRICT, parents: [y], inherits: [y]}
  - {id: z, kind: record, connector: A, rule: RESTRICTED, parents: [A], inherits: [A]}
grants: [[r, y], [r, z]]
memberships: [[s, r]]
gates: [[u, A]]
links: [[u, s, B]]
"""))

# UNPLACED: three granted records under a folder p the viewer may not open. r1's own group `shut` is closed and hangs from nothing, so no node lists r1
# but the App: it is listed there. r2's own group `open` is granted (a chain-top of the App itself), so r2 is listed under it and not under the App.
# r3 is granted but RESTRICTED and inherits nothing: the check denies it, and it is listed nowhere.
UNPLACED = world_from_data(yaml.safe_load("""
principals: [{id: u}]
nodes:
  - {id: A, kind: app, app_type: DRIVE}
  - {id: f, kind: group, connector: A, rule: OPEN, parents: [A], belongs: [A]}
  - {id: p, kind: record, connector: A, rule: RESTRICTED, parents: [f], inherits: [f], belongs: [f]}
  - {id: shut, kind: group, connector: A, rule: RESTRICTED}
  - {id: open, kind: group, connector: A, rule: OPEN}
  - {id: r1, kind: record, connector: A, rule: OPEN, parents: [p], belongs: [shut]}
  - {id: r2, kind: record, connector: A, rule: OPEN, parents: [p], belongs: [open]}
  - {id: r3, kind: record, connector: A, rule: RESTRICTED, parents: [p], belongs: [shut]}
grants: [[u, r1], [u, r2], [u, r3], [u, open]]
gates: [[u, A]]
"""))

KH_WORLDS = [CROSS2, ARROW, WALK, HELD, CHAIN, DECL_STRICT, DECL_SEEDED, DECL_BELOW, DECL_MEMBER, STUB, STUBHELD, DEEP, MIXED, CUT, OPENCUT, OPENKEPT,
             RESTRICTEDMEMBER, CHAINLINK, FILEDSTUB, OPENSTUB, APPCHAIN, DECLSTUB, TWOWAY, SCOPEDTWO, FOLDERSTUB, XDECL,
             SECOND, APPMEMBER, XGRANT, OPENGROUP, DECLDEEP, CROSSREACH, XSTUB, SIDEKID, APPSIDE, STUBQ, OPENGRANT, OPENGRANTTEAM, SIDESEED,
             STUBSEED, NESTSTUB, CAPSTUB, WIDE, HIDESEED, FLATSIDE, REACHSTUB,
             SCOPEDSIDE, XHOP, XLINK, DECLHIDE, XBADGE, XLINKBADGE, UNPLACED]

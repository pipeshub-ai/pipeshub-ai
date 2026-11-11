"""Worlds the Knowledge Hub conformance layer checks beside the golden scenarios and the generated worlds.

CROSS2: records of one connector reached through the groups of another (a knowledge base, an app-level connector, a record that belongs
straight to an app, a hierarchy-only parent). Every arm of the listing must apply the same connector check: a row the access check denies
is neither listed nor counted. Written in the same shape as the golden worlds; it is not a scenario (nothing changes in it).
"""

from __future__ import annotations

import yaml

from .scenario import world_from_data

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

KH_WORLDS = [CROSS2, ARROW, WALK, HELD, CHAIN, DECL_STRICT, DECL_SEEDED, DECL_BELOW, DECL_MEMBER, STUB, STUBHELD, DEEP, MIXED, CUT, OPENCUT]

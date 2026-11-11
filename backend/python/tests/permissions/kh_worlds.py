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

KH_WORLDS = [CROSS2, ARROW, WALK, HELD, CHAIN]

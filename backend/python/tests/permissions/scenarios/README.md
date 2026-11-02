# Permission scenarios

One corpus, three kinds of check, run against every layer that decides read access
(today the graph check on Neo4j and ArangoDB). The oracle (`../oracle.py`) is the product's rule written once;
a scenario lists what the *vendor* shows, and `deltas.yaml` lists every place the product knowingly differs.

| Folder | Tier | What |
|---|---|---|
| `rules/` | T1 | The 96 rule cases (principals, gates, grants, hops, seeds, record groups, uploads, citation), one file per letter |
| `golden/` | T0 | Base worlds: the worked examples of each connector, written the way the connector writers write them. Not scenarios themselves |
| `confluence/`, `sharepoint/`, `drive/`, `linear/` | T0 | `static.yaml` (verdict tables, where there is one) and `changes.yaml` (one scenario per change row), each referencing a golden world. Linear has `static.yaml` and `changes.yaml`; Drive and SharePoint the same; Confluence also has `deep.yaml` (a deep hierarchy) and `mixed.yaml` |
| `gmail/`, `slack/`, `kb/` | T0 | One `scenarios.yaml` each (statics and changes together) |
| `regressions/` | T2 | One scenario per bug. No fix merges without the scenario that fails before it |
| `deltas.yaml` | | Product differs from the vendor: direction (over-share, under-share), reason, fix |
| `MATRIX.md` | | Generated: connector x combining rule x change kind, covered or gap |

## A scenario

```yaml
- id: CONF-C-04                    # <CONNECTOR>-<S static | C change | X cross-cutting>-NN
  title: Compensation restriction lifted; Dave, named on the bands, now sees them
  source: permission model doc, confluence section     # or a vendor doc, a simulator preset, a bug id
  rule: intersection
  world: golden/confluence_hr      # a base world file, or an inline principals/nodes/grants/memberships/gates/links
  mutations:                       # `mutation:` for a single one; kinds: tests/permissions/model.py MUTATION_KINDS
    - {kind: rule_set, node: page:Compensation, value: STRICT}
    - {kind: grant_removed, node: page:Compensation, ref: g:hr-comp}
  expect:
    before: {dave: {page:2027-bands: deny}}          # optional: the same pairs before the change
    verdicts: {dave: {page:2027-bands: admit}}       # what the vendor shows after it
    index_writes: 0                # 0 | 1 | subtree | delete | unlink (the full-sync sweep unlinks the node)
    during: fail_closed            # recorded on subtree rewrites: the subtree is hidden until the rewrite is done; the oracle layer does not evaluate it, a layer that rewrites an index does
    source_delta: DELTA-02          # only when the product differs; see deltas.yaml (DELTA-02, DELTA-05, DELTA-17)
  tags: [confluence, "cell:confluence/restriction-change"]
```

* `expect.verdicts` lists the pairs that matter. The runner also checks **every** viewer x node pair of the
  changed world against the oracle, on each layer, and reports over-admits and under-admits apart.
* Nodes are `app`, `group` (record group) or `record`. Edges live on the node that leaves: `parents` (hierarchy,
  `{id, rel}` for an attachment or a link), `inherits`, `belongs`. `grants` are `[grantee, node, type]`,
  `memberships` `[member, group]`, `gates` `[who, app]`. The reserved id `org` is the organization.
* Add `cell:<group>/<kind>` tags so the scenario counts toward the coverage matrix.
* Regenerate the matrix after adding scenarios: `python -m tests.permissions.matrix` (from `backend/python`).

## Running

```
pytest tests/permissions -m "not integration"      # oracle, scenarios, round-trip, deltas, matrix; no services
```

Only the oracle layer runs here so far. Running the same scenarios against the graph stores (Neo4j and ArangoDB) is
not part of this corpus yet.

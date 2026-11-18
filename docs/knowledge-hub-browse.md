# Knowledge Hub browse on Neo4j: access from compiled classes

Status: proposal. Read it before touching `get_knowledge_hub_connector_page_v3` (browse, `flatten=false`) on Neo4j.

## Why

A Neo4j browse page is one generated statement that evaluates the whole permission model for the page. The browse rule
(`_kh_v3_child_rule`) is pasted in once per use: the expansion, each row's arrow, the stubs, the undecided children.
Each grant test in it becomes a `PERMISSION` subquery at that node (`_kh_grants_by_probe`).

Neo4j plans every `EXISTS` subquery as a query of its own, and plans it again for each join order it tries. Thread dumps of
the planner show the time in `SubqueryExpressionSolver`. So planning cost grows much faster than the text:

| Measured (November 2026) | |
| --- | --- |
| Browse statements captured on the demo server | 62–104 KB |
| Largest | 262 `EXISTS`, 101 per-node grant tests, 36 variable-length or quantified paths |
| Planning on the live server | 100–434 s per statement (the apps held 17 and 1,270 records) |
| Planning on an idle 1-CPU Neo4j, 62 KB statement | over 10 minutes |
| Execution once planned | milliseconds |
| Browse rule per use, 1 Nov → 16 Nov | 1.5 KB / 6 `EXISTS` → 4.9 KB / 18 `EXISTS` (#5222, #5704, #5734, #5745, #5878) |

Cached plans don't last. A sync shifts index statistics and Neo4j discards the plan ("Discarded stale query", 1,029 times
in one day on the demo server). Every correctness fix to the rule made every browse dearer to plan.

#6024 (the request deadline) stops an abandoned page from holding the database. It does not make a page cheaper.

## What a browse must return

It is defined by the Knowledge Hub conformance layer, `tests/permissions/kh_layer.py` (`_browse`, `chain_tops`,
`hidden_by_navigation`), against the access oracle, `tests/permissions/oracle.py`. Opening node `N` lists:

1. `N`'s hierarchy children (`PARENT_CHILD` and `ATTACHMENT`) that are not deleted and that the access check admits;
2. the chain-tops placed under `N` (`place_chain_tops`): granted OPEN nodes whose hierarchy parents the viewer can't open, under their own
   group, or under the App when they have none they can open;
3. less anything a group that hides its children holds or lies above (`hideChildren` is a display flag, the check ignores it);
4. a stub (placeholder) child only where opening it lists something;
5. nothing of another App.

A row's arrow is on exactly when opening the row lists something. `totalItems` and `counts` count each row once. The
known gaps the layer reports rather than fails are unchanged: the hop cap (`refused_by_cap`) and the undecided-children
sample (`_KH_UNDECIDED_MAX`).

So **a browse is a function of per-node admission and the structure**. Only the admission depends on the viewer.

## The approach

Decide admission per node with the class compiler (`app/services/permissions/classes.py`). Compose the page in Python from
small reads.

The compiler turns a node's access into a viewer-independent formula:

- the App's gate, AND
- an OR of clauses, where each clause is a set of nodes the viewer must hold a grant on (`SELF` is the node's own grant).

It mirrors the oracle's arms R7–R12 and is held to the oracle by `tests/permissions/test_classes_oracle.py`. What it can't
express exactly it **refuses**:

- too many clauses or paths;
- a seed it can't prove exact in an App with record-group-level groups;
- a node missing from the map.

A refused node is decided by the canonical batch check, as PostgreSQL's listing already does (`_kh_class_split`). PostgreSQL
stamps classes in its typed graph. On Neo4j they are compiled per request, from a structure read with
`Neo4jStampGraph.nodes` (`neo4j/permission_stamps.py`, about 1 KB of Cypher).

Per request, for a browse of `N`:

| Step | Statement | Size |
| --- | --- | --- |
| 1. Viewer: grantees, gated Apps, other connectors' grantees | the existing access reads | unchanged |
| 2. Start admitted (the start's breadcrumb, as today) | the existing batch check | unchanged |
| 3. Candidates: `N`'s hierarchy children with sort keys and display fields | one indexed match on `N`'s edges | < 1 KB |
| 4. Structure: candidates and their ancestors, level by level up to the App | `Neo4jStampGraph.nodes` per level | ~1 KB |
| 5. Compile each candidate's class | Python (`classes.Compiler`) | – |
| 6. Which of the classes' literal nodes the viewer holds a grant on | one statement: `n.id IN $lits` with the grant test written by `_kh_grants_by_probe`, as today | < 2 KB |
| 7. Admit by class; refused → batch check (bounded, as `_KH_UNDECIDED_MAX`) | the existing batch check | unchanged |
| 8. Chain-tops placed under `N` | the existing shared rule (`_kh_v3_chain_tops`) | unchanged |
| 9. Hidden-by-navigation, stubs, sort, keyset, page, counts | Python | – |
| 10. Arrows: for each page row, "does opening it list something" | steps 3–7 one level down, stopping at the first row found | < 1 KB each |

Every statement has a fixed text, so it plans once, in milliseconds, and stays planned. A statistics eviction then costs
milliseconds, not minutes.

### Freshness

Nothing is cached in the first version. Structure and grants are read with each request, so a page is exactly as current
as today's. A revoked grant or a moved node shows on the next request.

Caching the compiled classes per connector can come later. It would be keyed by the connector's structure generation
(`permissions/structure_generation.py`), which moves inside the transaction that writes a structural change.

### Bounds

- **Children per node.** Large folders are read a window at a time in sort order until the page is full, with a row and
  time budget. The same shape as PostgreSQL's `BUDGET_S` / `MAX_SCANNED`, with the same "resume" contract.
- **Ancestors.** Read level by level with a visited set (never a variable-length path, which enumerates paths). Shared
  ancestors are read once per request.
- **Refusals.** The batch check gets at most the sample it gets today.

## Correctness

The bar is that nothing a viewer may not read is listed, and nothing they may read is left out. Each check holds the new
path to it:

1. **The conformance layer:**
   - `test_conformance_kh` (golden worlds, the review shapes, generated worlds from every generator) passes unchanged on Neo4j;
   - it checks rows, totals, counts, facets, arrows, stubs and chain-tops against the oracle.
2. **The real-store permission suite** (`tests/integration/graph_permissions/`): its browse, arrow, stub, chain-top, declared, seed,
   placeholder and parity modules pass unchanged.
3. **A parity harness:** for every world and viewer, the old statement and the new path return the same pages: rows, order, cursors,
   totals, counts, arrows and stubs. Any difference fails, whichever side the oracle favours, so it can be examined.
4. **The compiler's own oracle tests**, plus a test that a refused class always reaches the batch check, and never an empty or a full answer.
5. **Shadow mode in production** (below). Over-admits are alerts.

## Rollout

`KH_BROWSE_MODE`:

- **`legacy`** (default first): today's statement.
- **`shadow`:**
  - answers with today's statement;
  - for a sampled fraction of browses, also runs the new path in a detached task, with its own budget, and compares;
  - counts rows only in one side, plus arrow and total differences;
  - logs counts and the organisation, never record ids.
- **`classes`:** the new path, falling back to today's statement on any error of its own.

Switch a deployment to `classes` after shadow reports no differences over a representative period. The demo server
first.

## Out of scope

- **The global flatten (All Records)** keeps `kh_scope` and today's statement. The same approach can follow.
- **ArangoDB.**

## Risks

| Risk | Mitigation |
| --- | --- |
| A difference between the compiler and the walk the oracle doesn't see | Parity harness and shadow mode compare against today's statement, not only the oracle |
| A folder with hundreds of thousands of children | Windowed reads with a budget and a resume cursor, as on PostgreSQL |
| Grant reads for viewers with very many grants | Only the literals of the candidates' classes are asked, through the same probe test as today |

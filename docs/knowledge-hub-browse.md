# Knowledge Hub browse on Neo4j: the split browse

Status: the default. `KH_BROWSE_MODE=legacy` opts out (see Rollout). Read it before touching `get_knowledge_hub_connector_page_v3` (browse, `flatten=false`) on Neo4j.

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

#6027 (one planner per statement) and #6024 (the request deadline) stop a cold plan from piling up and an abandoned page from holding the
database. Neither makes a page cheaper to plan.

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
layer reports two known gaps of the legacy statement rather than failing them: a row past the hop cap that it lists and then
refuses to open (`refused_by_cap`), and a stub whose readable child lies past the undecided-children sample (`_KH_UNDECIDED_MAX`).

So **a browse is a function of per-node admission and the structure**. Only the admission depends on the viewer.

## The approach

Decide the listing in Python from small fixed statements, with admission from the canonical batch check
(`IGraphDBProvider.check_access`, through `_kh_v3_admitted`). The page statement then reads the decided ids, and keeps
everything else as it was: start, trail, filters, order, cursor, counts and rows.

For a browse of `N` (`flatten=false`), in `get_knowledge_hub_connector_page_v3`:

| Step | What | Statement |
| --- | --- | --- |
| 1 | The viewer, the start's admission and the chain-tops placed under `N` | unchanged |
| 2 | `N`'s live hierarchy children of `N`'s App, less a group that carries access only (a Jira security level: never a row), each with its stub flag and whether a hiding group holds it or lies above it | `_kh_split_children_cypher`, under 1.7 KB, at most 3 `EXISTS` |
| 3 | Which of the visible real children the check admits. A child whose own hop up to `N` passes, where the check's walk from the App reaches `N`, is admitted by that walk without asking: the walk above `N` is read once for all of them, and only their ids are read. Only the rest are asked, and the check reads their shared ancestry once as well (below) | `_kh_split_admitted_by_hop` (under 3 KB, 7 `EXISTS`), then the batch check |
| 4 | Which stub children list something: the same question one level down, recursively, to the walks' hop cap | steps 2–3 for the stubs |
| 5 | The listed ids: admitted visible children, stubs that list something, chain-tops placed under `N` | Python (`_kh_split_listed`) |
| 6 | The page | the page statement, reading `$kh_listed` by id in place of the walk, and dropping any of them deleted or moved to another App since step 2 |
| 7 | Each page row's arrow: does opening it list something | `_kh_split_lists`, windows of children of 64, then ×4, stopping at the first admitted |

A group's arrow also counts a chain-top the walk places under it. Only groups holding a granted live record are asked of
that walk (`_kh_split_held`, then `_kh_v3_lists_a_chain_top`, `_KH_SPLIT_WALKS_AT_ONCE` at a time). The sidebar keeps every group and
folder, and a record exactly where its arrow is on, so it asks only the plain records (`_kh_split_plain_records`).

Admission is decided by the check, one statement that reads structure and grants together. The structure read of step 2 only proposes
candidates, so no decision combines a stale path with a current grant.

Every statement has a fixed text, so it plans once, in milliseconds, and a statistics eviction costs milliseconds.
Nothing is cached between requests, so a page is as current as the legacy one.

The class compiler (`app/services/permissions/classes.py`) could replace step 3 for most children with no check at all.
It is not needed for the cost: the check is a fixed statement already.

### Bounds

- **Children read per node:** `_KH_SPLIT_MAX_CHILDREN` (5,000), first by id. A browse whose start has more is answered by the legacy statement
  for that request: its rules decide in the database, and its check is windowed with a budget and a resume cursor. A row's arrow asks about that
  many children; one admitted among them decides it, and with none the page is answered by legacy (below), so an arrow is never off for a
  child past the bound.
- **Stubs below stubs:** followed to `_KH_SPLIT_STUB_DEPTH` (50), the walks' hop cap.
- **One request asks about a row once** (a memo shared by the listing and the arrows).
- **The check reads siblings' ancestors once.** On Neo4j, when it is asked about more than one node, the batch check first decides its walk
  from the App and its walk below a seed once for each parent of the asked nodes, and admits each node whose own hop up to that parent passes
  (`_kh_v3_accessible_rows`, under 11 KB, 9 `EXISTS`; 23 in the probe form). Both walks are matched up from the parent and their ends tested
  after them, so the planner cannot start one at the App or at every seed of the App. A route out of a node through a parent is that hop plus at most 49
  more, so this admits exactly what those two arms admit; every node it does not admit is asked of the full check, by every arm. A
  5,000-file folder then reads the folder's ancestry once, not 5,000 times, for every caller that asks by node id (arrows, selections, and a browse's viewer reached only through a grant on the folder).
- **A numbered page** is reached by reading the rows before it, 1,000 at a time (`kh_browse_skip_rows`), then the page. Those reads decide
  the listing (steps 1-5) once between them and skip step 7, since none of their rows is shown (`KH_SKIP_READS`, set by
  `KnowledgeHubService._walk_pages` for that request alone and keyed by the viewer's access). The page itself decides afresh, so page 50 costs
  two decisions, not fifty, and lists only what the viewer may open when it is read
  (`tests/integration/graph_db/test_kh_split_numbered_pages_real_neo4j.py`).

Shortcuts, each bounded by the above:

- A wide start should be read in windows of the page's order, with a resume cursor, rather than handed to legacy.
- Each page of a folder with many children recomputes the start's whole child admission (about 0.35 s per page for 343 children on
  the live copy); carrying the admitted window in the cursor would remove that.
- An arrow asks the check in growing windows, but reads each row's children to the bound at once (with their hidden-ancestor tests);
  reading the children in the same windows would cut the rows read for a page of wide folders.
- A page with a row split cannot decide within these bounds is answered by legacy: a row (or stub) with more than 5,000 children and
  none admitted among the first 5,000, or stubs below stubs left unfollowed at the depth cap (`_KhSplitUndecided`). A "lists nothing"
  past a bound is never shown, or remembered, as an answer. Reading the rest of such a row in windows would keep it on split.

## Where it differs from legacy

Each difference is where legacy breaks the definition above and the split browse keeps it:

- **A row past the hop cap.** Legacy lists `dp-50` under `dp-49` (51 hops below the App), and opening it is refused. The split browse lists only
  what the check admits (`test_provider_v3_shapes.py::test_the_deep_pages_agree`).
- **A record filed under a record in an App that opens everything.** Legacy shows an arrow on the holder, and opening it lists nothing: the
  filed record has no hierarchy parent, so no browse lists it (the layer's `unlisted` shortcut). The split browse shows no arrow
  (`test_provider_v3_arrow_matches_expansion.py`).
- **A stub whose readable child lies past legacy's sample.** Legacy leaves the stub out. The split browse lists it, because opening it lists the
  child (`test_conformance_kh.py`, CAPSTUB).
- **No time budget.** Legacy ends a page at the last child it had time to ask about and resumes there. The split browse lists a start's children
  in one page sequence, within the bound above.

## Correctness

1. **The real-store permission suite** (`tests/integration/graph_permissions/`): its 18 browse, arrow, stub, chain-top, declared, seed,
   placeholder, sidebar and parity modules pass with the default, split, and with `KH_BROWSE_MODE=legacy`. The modules that pin
   legacy's statement text or its gaps set `KH_BROWSE_MODE=legacy` themselves.
2. **The conformance layer** (`tests/permissions/test_conformance_kh.py`): golden worlds, review shapes and generated worlds against the oracle.
   CI runs it with Neo4j on the default, split, and a second time on legacy (`permission-conformance.yml`, shards 8 and 9).
3. **Live data.** A read-only copy of the demo server's graph: 33,096 nodes, 66,685 relationships, its 163 indexes and constraints. All 11 active
   users were taken through `KnowledgeHubService.get_nodes`, 40 opens each, every page, as a listing and as a sidebar, in both modes. Over 440
   opens and 789 reads, no row, order, arrow, stub flag, total or count differed. From a cold start the worst open took 70.2 s on legacy and
   11.3 s on split; opening an App took 70.2 s against 2.5 s.
4. **Legacy unchanged.** Six browse modules, recorded on the base and on this change: the same 2,901 statements, of 200 distinct texts.
5. **Shadow mode in production** (below).

## Rollout

`KH_BROWSE_MODE`:

- **`split`** (the default; also what an unset, empty or unknown value means, an unknown one with a warning logged once): the split browse.
- **`legacy`** (the opt-out): the one-statement browse, byte for byte as before.
- **`shadow`:** answers with legacy. For a sampled fraction of browses (`KH_BROWSE_SHADOW_SAMPLE`, default 0.1), it also runs the split browse in
  a detached task and compares them. It logs counts (rows only in one side, arrow and total differences), never ids. At most
  `_KH_SHADOW_MAX_RUNNING` (2) comparisons run at once per process; a sampled browse past that is not compared, so shadow adds a bounded
  load however busy the server is. A legacy page cut by its time budget is not compared: split has no budget, so they differ by construction.
  Nor is a page split would hand to legacy (past its bounds): its replay runs as `split-compare`, which reports that instead of answering
  with legacy. Arrow and stub flags are compared on every row both pages hold.

Split became the default after the live-data comparison above (440 opens, 789 reads, no differences). To go back to the one-statement browse,
set `KH_BROWSE_MODE=legacy` on the services that serve the Knowledge Hub and restart them; `shadow` compares the two on a running server.

## Out of scope

- **The global flatten (All Records)** keeps `kh_scope` and the legacy statement.
- **ArangoDB and PostgreSQL** are unchanged.

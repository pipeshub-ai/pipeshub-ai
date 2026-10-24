# GitHub Teams connector against a simulated GitHub

These tests run the real GitHub Teams connector end to end on Neo4j and ArangoDB. Only GitHub, the message broker and the config store are replaced. After each sync they compare PipesHub with GitHub, for every user and every record, through PipesHub's own access check (`filter_accessible_record_ids`).

They cover:
- first, incremental and full syncs, and resuming after an interruption;
- every record type, with parents, attachments and code files;
- users, teams and the permission hierarchy, and the creator's link to the connected GitHub account;
- deletions, transfers, renames, restores, issues turned off, and repositories leaving the scope;
- sync and indexing filters, including changing them later;
- rate limits, 5xx errors, timeouts, dropped connections, revoked tokens, store failures;
- token handling, SSRF and download limits.

A test fails when PipesHub and GitHub disagree, when a sync that did not work reports success, or when the connector uses something the simulator does not model. So a gap in the simulator can never pass for a working connector.

## Run

They need the integration graph stores from `deployment/docker-compose/docker-compose.integration.graph-db.yml`, and `graphql-core` (in the `dev` extra):

```bash
cd deployment/docker-compose && docker compose -f docker-compose.integration.graph-db.yml up -d --wait neo4j-graph-it arango-graph-it
cd ../../backend/python
pytest tests/integration/github_teams -m integration -k neo4j --timeout=300
pytest tests/integration/github_teams -m integration -k arango --timeout=300
```

To use other servers, set `NEO4J_IT_URI`, `NEO4J_IT_PASSWORD`, `ARANGO_IT_URL` and `ARANGO_IT_PASSWORD` (see `tests/integration/real_graph.py`).

- The `backend-matrix` workflow runs them on every pull request that touches the connector, its GitHub clients, the data processor or the graph stores.
- Without a graph store they skip. When the workflow configured one, they fail instead.
- Each test deletes only what it wrote: its org, its connector and everything carrying their ids.
- Users are looked up by email. A killed run can leave users in the database, and they confuse the next run. Clear that database before rerunning.

## Layout

- `sim/`: the simulator.
  - `model.py`: accounts, orgs, teams, repositories, issues, PRs, git history, uploads.
  - `rest.py`: REST over a real socket, plus the httpx transport the connector is given.
  - `gql.py`: GraphQL, validated against GitHub's published schema.
  - `spec.py`: request checks and response building from GitHub's OpenAPI description.
- `specs/`: GitHub's REST description, trimmed to the operations the simulator serves, and GitHub's GraphQL schema. `refresh_specs.py` rebuilds both from GitHub's sources.
- `harness.py`: wires the real connector, data source, processor, graph store and graph database to the simulator. It also models indexing between syncs, and the full sync a filter change triggers.
- `helpers.py`: fault injection (a status, a hook, the n-th request).
- `test_*.py`: the scenarios. Modules that set `BOTH_TRASH_MODES = True` run with the trash (soft delete) off and on.

## How the simulator is held to GitHub

- Every REST request is matched to an operation in GitHub's OpenAPI description:
  - a query parameter GitHub does not define fails the test;
  - an enum value GitHub would refuse gets GitHub's 422;
  - an operation the simulator does not serve fails the test.
- Every JSON body the simulator sends is validated against the operation's response schema.
- Response fields the simulator does not model:
  - required ones are filled with sentinels (`__sim_unmodelled__`, `-987654321`), and reading one fails the test;
  - reading a field absent from the payload fails the test, except fields GitHub itself omits there (`harness.ABSENT_ON_PURPOSE`, each with its reason).
- GraphQL queries are parsed and validated against GitHub's schema. A field the simulator does not model fails the test; it is never answered with null.

## What each simulated behaviour is based on

"Live" means checked read-only against api.github.com on public repositories (2026-10-13). "Switch" names a `sim.model.Behaviour` field. A scenario that depends on an undocumented answer runs under each answer.

| Behaviour | Source | Confidence |
|---|---|---|
| `GET /repositories/{id}` answers like `/repos/{owner}/{repo}` (200 readable, 404 otherwise). It is not in the OpenAPI document. | Live: 200 for cli/cli's id, 404 for an unknown id | High |
| Issue listing `since` is inclusive (an issue updated exactly at `since` is returned) | Docs say "after". Live: inclusive | High |
| `page=N` on listings is an offset, so a list that changes between requests shifts | Docs ("page" parameter). Live: the issues `Link` header also offers an `after=` keyset cursor, which the connector does not use | High |
| Pull request listing has no `since`. `sort=updated&direction=desc` with offset pages | OpenAPI params; live Link header uses `page=` | High |
| Deleted issue: `GET` answers 410 (404 without read access). Transferred issue: 301 to its new home. Both leave the listing | OpenAPI description of issues/get | High |
| Editing a comment updates the issue's `updated_at` | Docs silent. Live: in 12 of 12 edited comments on two busy repos, the issue's `updated_at` was at or after the edit, often to the exact second. Switch `comment_edit_bumps_issue` | High (behavioural evidence, not documentation) |
| A new comment updates the issue's `updated_at` | Docs; live data above | High |
| GraphQL `repository.issues` excludes PRs, transferred-away and deleted issues. `totalCount` equals the REST count of non-PR issues | Schema. Live: 7 = 7 on demo-repository | High |
| GraphQL `issues(first, after)` cursors are keysets (an item removed mid-walk cannot shift another) | Live: cursor decodes to `cursor:v2:[created_at, id]` | High |
| GraphQL `Issue.updatedAt` equals the REST `updated_at` of the same issue (both to the second) | Schema (`updatedAt: DateTime!`); same field on both APIs. Not compared live issue by issue | Medium |
| Issues listing read newest first (`direction=desc`, as the connector does): an issue leaving the listing between two page requests moves later issues up one place | Follows from `page=N` being an offset (row above) | High |
| Commit history cursor `"<oid> <offset>"` | Existing connector code (`timestamps.py`), used as GitHub returns it | Medium |
| `affiliation=all` collaborators include outside collaborators, direct members, team members (child teams included), base-permission members and owners. `permissions` booleans give the base role | OpenAPI description of repos/list-collaborators | High |
| `/collaborators` needs push access (403 otherwise) | OpenAPI description | High |
| Internal repositories readable by organisation members, not by outside collaborators | GitHub docs (quoted in `projects.py`) | High |
| Public repositories readable by everyone. PipesHub mirrors that as an ORG grant | Connector's documented intent | High (as a product decision) |
| `/orgs/{org}/members` shows nothing to a non-member (concealed members only). `/orgs/{org}/outside_collaborators` needs an owner | GitHub docs | Medium |
| `organizationVerifiedDomainEmails` only for an org owner, only with a verified domain | GitHub docs (quoted in `users.py`) | Medium |
| `github.com/user-attachments/...` with an API token answers 404, even for a public repository. Without one it answers 302 to signed storage (public repository) | Live, demo-repository attachment: 302 anonymous, 404 with `Bearer` and with `token` | High for public. Private repositories not tested (token has no `repo` scope) |
| Private-repository attachments: anonymous gets 404 | Assumption (GitHub restricts private uploads to signed-in users) | Low. Needs a live check |
| Contents API returns empty `content` and `encoding: none` for 1–100 MB files | OpenAPI description of repos/get-content | High |
| Recursive tree truncates over 100,000 entries / 7 MB (`truncated: true`) | GitHub docs. Switch `tree_truncate_over` | High |
| Compare: status `ahead`/`behind`/`diverged`, ≤300 files, ≤250 commits. Pure moves reported as `renamed` | GitHub docs. Switch `compare_detects_renames` | Medium (rename detection is similarity-based in reality) |
| Rate limits: 5,000/h core, 5,000 points/h GraphQL (modelled as 1 per query), 30/min search. A spent pool answers 403 with `x-ratelimit-remaining: 0`. A secondary limit answers 403 with `retry-after` | GitHub docs | Medium (GraphQL point costs are simplified) |
| Repository rename or transfer: `/repos/old/name` redirects (301) to `/repositories/{id}` | GitHub docs ("Follow redirects") | High |
| Deleted repository can be restored by an owner | GitHub docs | High |

Not modelled (a connector call to these would fail the test as a gap):
- webhooks;
- search beyond name matching with `org:`/`user:` qualifiers;
- issue types, issue fields and dependencies beyond an empty `blocking` list;
- GitHub Enterprise Server;
- fine-grained token permissions.

# Dropbox integration tests: design

Six modules, 46 tests, one marker (`dropbox_sim`), both Dropbox connectors (`Dropbox`, team, OAuth as a team
admin; and `Dropbox Personal`, OAuth). Each test guards a finding of the audit or an area no harness scenario
can reach (vectors, search, the stack around the connector). Cases the harness already proves on the real
connector are left to it, which keeps the suite short.

| Module | Classes | Tests | What it covers |
|---|---|---|---|
| `dropbox_sim_test.py` | `TestDropboxTeam`, `TestDropboxPersonal` | 7 + 4 | first sync, incremental, suspension, a failed listing, a cursor reset, streaming, a revoked token; personal creator-only access, incremental, streaming, a file renamed out of the filter |
| `dropbox_sim_access_test.py` | `TestDropboxTeamAccess`, `TestDropboxTeamMemberRoot` | 5 + 3 | groups and people: group members, a membership change, an outside collaborator added and removed, a new member, a deleted group; on the team member root layout, a folder in the team folder that stops inheriting, then is deleted |
| `dropbox_sim_filters_test.py` | `TestDropboxTeamSyncFilters` | 6 | extensions (in, not in), files renamed across the filter, the filter cleared, modified before with an edit leaving it, created between |
| `dropbox_sim_lifecycle_test.py` | `TestDropboxTeamLifecycle` | 8 | delete (graph and vectors), restore, folder delete, a move into a team folder and back, team folder archive, an edit (vectors and stream), "Index Shared Items" off, a stream after the token is refused |
| `dropbox_sim_trash_test.py` | `TestDropboxTeamTrash` | 2 | delete and restore with the trash on (the setting is deployment-wide; the module restores it) |
| `dropbox_sim_scenario_matrix_test.py` | `TestDropboxScenarioMatrix` | 11 | the shared scenario matrix (`connectors/scenario_matrix.py`); scheduled sync is skipped, as for every connector but Nextcloud |

`dropbox_sim.py` is the simulator's control client and the tenants. `dropbox_it.py` holds what the modules share:
the connector set-up, syncs, filter saves, and the checks. `conftest.py` has the class-scoped fixtures.

Each class declares its Dropbox (`TENANT`), whether what syncs is indexed (`AUTO_INDEX`) and any starting sync
filter (`SYNC_FILTERS`). The `dropbox_team` / `dropbox_personal` fixture builds a connector for that class alone.
`pytest.ini` orders tests per module (`--order-scope=module`), so the `order` marks run on through a module and
never repeat between its classes. Repeating them would interleave the classes and rebuild each connector.

Access is checked against the simulator, not against a list in the test. `assert_access_matches_source` asks the
simulator who Dropbox lets read each file (`/_sim/dropbox/expected`, `readers`). It then checks, for every
PipesHub user the tenant has, that PipesHub opens the file exactly for those people. A file nobody may read has
no live record.

## 1. What is real and what is simulated

A real Dropbox test needs a Dropbox Business team whose members, groups, team folders and sharing a test can
change on demand. It must also be able to suspend a member, expire a cursor and fail one call. Dropbox can't do
most of that on demand. So the Dropbox API is simulated, and everything else is real.

| Part | In these tests |
|---|---|
| PipesHub (Node API, connector service, indexing, graph, KV, broker) | real, the integration compose stack |
| Dropbox connectors, the Dropbox SDK (`dropbox` 12.x) | real |
| Dropbox API v2 (files, sharing, team, team_log, users), the OAuth token endpoint, temporary links | the Dropbox simulator (`connectors-simulator`, simulator `dropbox`), compose service `dropbox-sim` |
| Credentials | the simulator's own OAuth app and refresh token (`dropbox_sim.env`); none of them work at Dropbox |

The simulator checks every request and every answer against Dropbox's Stone spec (`dropbox-api-spec`). It answers
409 or 400 to whatever Dropbox would refuse. It records any request it does not model. Each test ends by
asserting that both lists are empty, so a test cannot pass on a call the real API would refuse.

## 2. How the connector reaches the simulator

The Dropbox clients read `DROPBOX_API_BASE_URL` (backend, `app/sources/client/dropbox/endpoint.py`). The SDK pins
its own CA bundle, so a TLS stand-in can't take Dropbox's place. When the variable is set, the SDK gets a
requests session that sends `api.dropboxapi.com`, `content.dropboxapi.com` and `notify.dropboxapi.com` to that
origin and keeps the Host header. Any other host is refused. A malformed value is refused rather than guessed at.
The connector logs a warning when the variable is set. It is empty in production and empty by default in both
integration compose files.

Temporary links point at `CONNSIM_DROPBOX_LINK_BASE_URL` (`http://dropbox-sim.internal`). The connector streams
from them through the web address guard, so the integration compose adds `dropbox-sim.internal` to
`WEB_CONNECTOR_ALLOWED_HOSTS`.

The test process reaches the simulator on its published port (`DROPBOX_SIM_URL`, default
`http://localhost:8109`).

## 3. Authentication

The fixture creates the connector with the simulator's app key and secret. It then exchanges the simulator's
refresh token at the simulator's token endpoint (`authenticate_connector_with_refresh_token`). The connector
refreshes there too, through the redirected session. Revoking the refresh token (`DBX-T-08`) makes the next
refresh fail as Dropbox does, with `invalid_grant`.

## 4. The tenants

**Team** (`team_scenario`). The members are alice (team admin), bob, carol and dan, each with a PipesHub
account under the same email. Engineering is a group of bob and carol. Projects is a team folder for
Engineering and alice. Design is carol's shared folder, shared with bob. Secret sits inside Projects, does not
inherit, and has dan only. alice shares `notes.txt` with bob.

**Personal** (`personal_scenario`). The connected Dropbox account carries a colleague's email, not the
admin's. Another account shares a folder with it and has a private file of its own. Only the admin, who
created the connector, may open the synced files. The colleague and other users may not.

The other tenants build on these. Each function's docstring in `dropbox_sim.py` describes it.

- `access_scenario`: two groups, two team folders, a fifth member in nothing, and gus, an outside collaborator
  with a PipesHub account.
- `filter_scenario`: files of every extension and date the filters tell apart.
- `lifecycle_scenario`: files with known text, a file to move, a folder tree to delete, and a second team
  folder to archive. The trash module uses it too.
- `matrix_scenario`: alice and bob only.

A file given `text` serves those bytes (simulator op `add_file` / `edit`). Any other file's bytes are generated
from its key, version and size.

## 5. Cases

Each module's docstring lists its cases (`DBX-T-*` and `DBX-P-*`; `DBX-G-*` groups and people; `DBX-F-*` filters;
`DBX-R-*` the team member root layout; `DBX-L-*` lifecycle and content; `DBX-TR-*` trash). Within
a class they run in order on one connector. Each fixture's teardown deletes the connector and checks that its
graph is gone.

What Dropbox has no equivalent for is not tested: a folder or drive scope filter (Drive's `folder_ids` and
`drive_ids` suites), shortcuts, and Google-native export limits. The "Index Files" indexing filter is not declared
by either Dropbox connector, so the UI can't set it.

The scenario matrix runs on the Neo4j leg only, as for every connector (`SCENARIO_MATRIX_GRAPHS` widens it). Its
scheduled-sync scenario is skipped, because it runs on the Nextcloud matrix only.

## 6. Running

```bash
# DROPBOX_SIM_IMAGE: an image of connectors-simulator with the dropbox simulator (CONNSIM_DROPBOX_REF in the workflow)
docker compose -p <your project> -f deployment/docker-compose/docker-compose.integration.neo4j.yml \
  --profile dropbox-sim up -d
set -a; . integration-tests/connectors/dropbox_sim.env; set +a
cd integration-tests
pytest -m dropbox_sim -n 2 --dist loadgroup connectors/dropbox_sim
```

With the simulator unreachable or the variables unset, the fixtures fail when `dropbox_sim.env` is loaded
(`DROPBOX_API_BASE_URL` set, as CI sets it on the shard that runs the suite) or on a run meant to cover every
connector, and skip otherwise (`source_unavailable`).

CI runs the suite on the `connectors-4` shard. The workflow builds the simulator image from `CONNSIM_DROPBOX_REF`
and loads `connectors/dropbox_sim.env`.

# Collaborative-chats real-HTTP lane

Journeys marked `collab_stack` run over real HTTP into the real Node API (`backend/nodejs/apps`,
started from source with `ts-node --transpile-only`) on the host, against throwaway containers.
Nothing else in `integration-tests` boots a stack; the other journeys still expect a remote
or compose deployment.

## Run

```bash
integration-tests/collaborative-chats/stack/run.sh              # all collab_stack journeys
integration-tests/collaborative-chats/stack/run.sh -k j01       # extra arguments go to pytest
```

Needs Docker, Node 22 (`~/.local/node/bin`, or `PCC_E2E_NODE_BIN`) and the integration venv
(`uv sync --frozen` in `integration-tests`; point `PCC_E2E_VENV` or `PCC_E2E_PYTHON` at it). The
first run installs the Node dependencies with `npm ci`. Keep `TMPDIR` short (default
`/tmp/pcc-e2e/tmp`); Unix socket paths have a length limit.

| Env | Meaning |
| --- | --- |
| `PCC_E2E_NODE_ROOT` | Node app to boot (default: this checkout). Point it at the PH-00 worktree to capture the baseline. |
| `PCC_E2E_PROJECT` | Compose project name, `pcc-e2e-<id>`. Generated per run. |
| `PCC_E2E_CAPTURE=write` | J-02 writes the baseline fixtures instead of diffing against them. |
| `PCC_E2E_KEEP_RUN_DIR=1` | Keep the Node logs (`node-<port>.log`) after the run. |
| `PCC_E2E_REAL_STALENESS_WAIT` | `1`: J-06 waits in real time (up to ~65 s) for a change made outside Node to take effect, instead of asserting the cache state. |
| `PCC_E2E_REPLICA_SET_AVAILABLE` | `false` (default, as in docker-compose) or `true`: whether the API uses Mongo transactions (PH-05's `fencedWrite` does on a replica set). Mongo itself is always a replica set. Run the lane in both modes. |

## Real-Python mode

`PCC_E2E_REAL_PYTHON=1` (or `run.sh --real-python`) starts the Python **query** (`app.query_main`) and **connectors**
(`app.connectors_main`) services from source on the host and points Node at them, so the journeys that need Python run against the
real thing. Everything else about the lane is the same.

```bash
integration-tests/collaborative-chats/stack/run.sh --real-python                       # Neo4j (default)
integration-tests/collaborative-chats/stack/run.sh --real-python --graph arangodb      # Arango
PCC_E2E_REAL_PYTHON=1 PCC_E2E_GRAPH=arangodb python -m pytest collaborative-chats -k test_agent_handles   # without run.sh
```

| Piece | Real or fake | Notes |
| --- | --- | --- |
| Node API | real | as above; also writes the Arango and Qdrant settings and the service endpoints into the KV store at boot |
| Query service `:random` | real | `uvicorn app.query_main:app` with the repo venv (`backend/python/venv`, also looked for in the main checkout of a linked worktree, or `PCC_E2E_SERVICES_PYTHON`); agent loop, write guard, tools, create-from-chat, handle allocator |
| Connectors service `:random` | real | `uvicorn app.connectors_main:app`; teams, KB upload, record reads with the PDP, `entity-events` consumer, the Python migrations |
| Graph | real, own container | Neo4j 5.26 (default) or ArangoDB 3.12 (`PCC_E2E_GRAPH=arangodb`), tmpfs, `127.0.0.1:<random>` |
| Vector store | real, own container | Qdrant (gRPC on a random host port, from the `grpcPort` Node stores); nothing is indexed |
| Mongo, Redis | real, own containers | Redis is the KV store, the broker (`MESSAGE_BROKER=redis`) and the cache |
| LLM and embedding model | fake | the lane's fake serves an OpenAI-compatible `/v1` (`helper/collab_stack/fake_llm.py`); the configured `openAICompatible` models point at it |
| Indexing, Docling, Parsing, Extraction, Embedding | not started | nothing here indexes a document, and the embedding model is remote (the fake), so no local model is loaded |

Boot order: containers, fake, Node (so the secrets and endpoints are in the KV store), connectors, orgs and users published as
`orgCreated`/`userAdded` on the `entity-events` stream (the connectors service builds the graph from them, as in production), query,
then the AI models through `POST /api/v1/configurationManager/ai-models/providers` (which makes the query service health-check the fake).

Scripting the model: `fake.script_llm(llm_turn("text"), llm_turn(tool_calls=[tool_call("artifacts__save_artifact", {...})], when=offers("artifacts__save_artifact")))`.
A turn answers the first call its `when` accepts, so the query service's own side calls (titles, follow-ups) cannot take a turn meant
for the agent loop. `fake.llm_calls(mark)` returns what the model was shown; `helper/collab_stack/real_python.py` has the shared bits
(knowledge-base upload, `stream_turn` which sends the `client-name` header the web UI sends, `latest_tool_result`).
`stack.python.graph({"op": ...})` runs operations through the product's own `IGraphDBProvider` (`helper/collab_stack/graph_seed.py`).

Tests marked `collab_real_python` need this mode and skip without it; the other `collab_stack` journeys script the fake AI routes and skip
in this mode; `collab_both_modes` holds in both. Env: `PCC_E2E_GRAPH` (`neo4j` | `arangodb`), `PCC_E2E_SERVICES_PYTHON` (interpreter for the
services), `PCC_PERF01_USERS` (graph filler users for PERF-01, default 20000). A cold start takes about a minute on an idle host.

## Suites

| File | What it covers |
| --- | --- |
| `integration_test_j01_security_regressions.py` | PH-01 fixes observable over HTTP |
| `integration_test_j02_flag_off_parity.py` | every chat, agent and project-list call, flag off, diffed with the PH-00 capture |
| `integration_test_ph03_migrations_and_acl.py` | boot-time migrations, indexes, `aclVersion` bumps |
| `integration_test_ph04_access_flag_on.py` | guards, lists, caches with the flag on |
| `integration_test_j03_handover.py` | PH-05: a writer continues the owner's chat (identity, history, authorship, Node-minted `runId`) |
| `integration_test_j04_concurrency.py` | PH-05: busy lease and release, duplicate `clientMessageId`, cancel with a wrong `runId`, two Node instances, a crashed instance |
| `integration_test_j09_resume_binding.py` | PH-05: only the person asked answers a card (body `resume` and the `User selections:` text), regenerate is asker-only |
| `integration_test_j05_revoke_mid_stream.py` | PH-06: the run completes after a revoke (collaborator removed, project visibility flipped, project member removed); next send, feed and collaborators are 404 |
| `integration_test_j06_team_membership.py` | PH-06: team share by level; membership change through the Node Teams route (`teamsVersion` bump) and outside Node (cache bound); team delete cleanup |
| `integration_test_j11_transfer_owner_inactive.py` | PH-06: ownership transfer, `OWNER_INACTIVE` / `OWNER_STATUS_UNAVAILABLE`, user and owner deletion (offboarding) |
| `integration_test_ph06_collaboration.py` | PH-06: collaborators views, invite rules, org-wide, 200 cap, leave/archive, feed (304, paging), readiness, `unreadCount`, agent kind, flag-off 404s, rate limit |
| `integration_test_j07_consent.py` | PH-07: file and artifact consent (`filesShared`, `shareToolResults`), revocation on the next check, cross-conversation isolation |
| `integration_test_j08_project_inheritance.py` | PH-07: project-visible chats, project ceiling, private flip, explain `via`, preview gains/loses |
| `integration_test_ph07_content_access.py` | PH-07: explain visibility and team redaction, preview writes nothing, project ceiling PATCH, internal check auth and no-oracle, flag-off behaviour |
| `integration_test_j10_cross_user_injection.py` | PH-08 PR-08f: what Node sends Python in a shared chat (`collaboration`, `authorRef`, stable refs, no ids or addresses) on follow-up, stream, agent kind and regenerate; solo and single-author chats send none |
| `integration_test_stack_harness.py` | self-tests of the lane (held streams, failures, restart, flag toggle, replica-set transactions) |
| `integration_test_j03_..._real_python.py` | J-03 on real Python: retrieval (the knowledge tools) runs as the sender, history and attribution |
| `integration_test_j07_..._real_python.py` | J-07 on real Python: consent through the connectors record read route and Node's PDP, files and artifacts |
| `integration_test_j09_..._real_python.py` | J-09 on real Python: the question card at Node and at the query service's own wall |
| `integration_test_j10_..._real_python.py` | J-10 on real Python: the write guard blocks another participant's address in the real agent loop |
| `integration_test_j12_..._real_python.py` | J-12 on real Python: a note reaches the model as information, `@assistant` is answered |
| `integration_test_j13_..._real_python.py` | J-13 on real Python: `draft_agent`, create-from-chat, handles and privacy on the real graph |
| `test_team_ids_resolution.py` | PERF-01: team ids from the real connectors service, 2 teams in a large org and 300 teams |
| `test_agent_handles_real_graph.py` | PH12-03: concurrent agent creates, one wins the plain handle; Neo4j constraint and Arango index |
| `test_migrations_rerun.py` | PH12-02: every migration twice, a flag-delete re-run writes nothing, the feature off leaves data readable |
| `test_broker_redis_streams.py` | PH12-05: share and mention notifications through Redis Streams, once (both modes) |

PH-07 content access is split across two halves. This lane covers the Node half over real HTTP: turns carry consent
(`filesShared` / `shareToolResults`), and the decisions are asserted on Node's PDP, `POST /api/v1/authz/internal/check`
with an `authz:check` service token, called as Python would (attachment record id from the turn row, `ownerUserId` = the
uploader, `runId` for artifacts), plus explain, preview and the project ceiling. The Python half is not on this lane,
because the Python services are a fake here: the record read routes that ask the PDP (`NodePdpClient`, `can_read_record`,
the `aclVersion` cache, the 404 on stream/download/signed URL) are Python unit tests under `backend/python/tests/unit/`
(`modules/authz/`, `connectors/api/test_router_chat_content_access.py`). The preview/download path sends no `aclVersion`,
so J-07 asserts revocation on the very next check.

J-10 is also split. This lane covers the Node half: Python is a fake here, so it asserts the request Node sends (the current sender's
ref, each message's `authorRef`, no user id or address in `collaboration`). That the write guard then denies the email tool is
the Python half, covered by the real agent loop in `backend/python/tests/unit/modules/agents/collaboration/` (`test_write_guard.py`, `test_injection.py`).
J-09's Python half (a card answer resumes only for the person asked, with or without `resume`) runs through the real
`/chat/stream` handler with a scripted LLM in `backend/python/tests/integration/test_collab_resume_binding.py`.

J-12 and J-13 have their own fake-lane files; their Python halves are the `_real_python` files above.

## What runs

| Piece | Where | Notes |
| --- | --- | --- |
| Node API | host process, random free port | `NODE_ENV=test`, `KV_STORE_TYPE=redis`, `MESSAGE_BROKER=redis` (no etcd, no Kafka), `REPLICA_SET_AVAILABLE` from `PCC_E2E_REPLICA_SET_AVAILABLE` (default `false`) |
| MongoDB 8.0 | container `pcc-e2e-<id>-mongo-1` | single-node replica set `rs0`, tmpfs, `127.0.0.1:<random>` |
| Redis 7.4 | container `pcc-e2e-<id>-redis-1` | KV store, streams, authz and team-id caches, `127.0.0.1:<random>` |
| Python services | one aiohttp fake on the host (`helper/collab_stack/fake_backend.py`) | AI backend chat and AG-UI streams, agent chat, cancel, attachment validation, connectors teams and KB |

Teardown runs on success, failure, Ctrl-C and SIGTERM: the session fixture, an `atexit` hook and the
`trap` in `run.sh` each remove the compose project (`down -v`). `run.sh` fails the run if a container
of the project is still there. It never touches a container it did not create.

Not started in the default (fake) mode, because the Node API does not need them at boot: Kafka, etcd, Neo4j/ArangoDB, Qdrant and every Python service (see Real-Python mode).
`/migrations/chat_kb_filters_v1` is preseeded in the KV store: it otherwise waits minutes for the Python-owned `kb_apps_v1` flag.

## Writing a journey

```python
pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack]

def test_x(stack, api, fake, roster, flag_on):       # fixtures from collaborative-chats/conftest.py
    chat = chats.create_chat(api, roster.owner)
    fake.on("chat_stream", held_stream(fake.gate("hold")))   # script the next AI reply
    ...
    fake.requests_for("chat_stream")[0].body                  # what Node sent Python
```

* **Identity** (`roster`): `admin`, `owner`, `write_recipient`, `read_recipient`, `project_viewer`,
  `project_editor`, `team_writer`, `team_reader`, `stranger`, `disabled` (org `acme`) and `other_org`
  (org `globex`). Users are Mongo rows; `Directory.session_token(actor)` signs the same JWT the API
  verifies, `Directory.scoped_token(actor)` the internal-route token, `conversation_permissions_token` the
  service token. Teams live in the fake (`fake.add_team(org_id, name, {user_id: role})`) and are
  served on `/api/v1/entity/user/team-ids` and the team CRUD routes.
* **Fake backend** (`fake`): `on(route, *replies)` queues one-shot replies; `default(route, reply)` and
  `with failing(route, status=...)` change a route for a while. Replies are `Reply(body, status, delay)`,
  `Sse([...])` (frames, `Pause`, `Hold(gate)`, `Drop`) or a callable. `held_stream(gate)` keeps a stream open
  until `gate.open()`; `gate.wait_reached()` tells the test the stream is parked. Every request is recorded:
  `requests_for(route)`, `mark()` / `since(mark)`, with `.body`, `.headers`, `.user_id` (from the forwarded JWT).
  `fake.reset()` runs before each test.
* **Flag** (`flag_on`, `flag_off`, `flag_on_for_module`): written through the admin settings route, then
  the fixture polls a probe until the API enforces it. The API caches flags for 10 s, so each toggle costs
  about 10 s; prefer the module-scoped fixture.
* **Streams**: `api.stream(path, actor, json_body=...)` returns a `StreamCall` read on a thread (`wait_for`,
  `events`, `result`, `abort()`), so a test can act while a run is open.
* **State**: `stack.reset_state()` (automatic) empties chats, projects and the authz caches. Direct
  Mongo seeds are in `helper/collab_stack/seeds.py` (`insert_session`, `insert_project`); use them for states
  the API cannot produce on purpose (legacy rows, team rows).
* **Restart**: `stack.node.restart()`; stop it, edit Mongo or Redis, `start()` to test boot-time behaviour. A module that builds state across tests sets `STACK_KEEP_STATE = True` to skip the per-test reset.
* **World**: `helper/collab_stack/worlds.py::seed_collab_world(stack)` seeds one chat shared directly, by team and through a project (roles in `SHARED_ROLES`), with the teams in the fake.
* **Scripted calls**: `helper/collab_stack/parity.py` holds the call table (`CALLS`, ids C1-C16 and A1-A13 as in PH-04) and `request()` to play one as an actor.
* **Faults**: `stack.infra.failing_finds(db, collection, skip=n)` makes Mongo fail `find` on one collection (the `failCommand` fail point; the compose file enables test commands).

## Flag-off parity (J-02)

`fixtures/j02_flag_off_baseline.json` holds the calls of `helper/collab_stack/parity.py` captured from
origin/main at the last merge into the branch (`8d48f1be3`, the tree without collaborative chats) on a fresh
database with the same seed, with ids, timestamps and request ids normalised. It was first captured from the
PH-00 tree (`77fbd5618`, which changed no Node source) and re-captured when origin/main was merged in.
To refresh it after the next merge, check out that
origin/main commit, set `BASELINE_COMMIT` in `integration_test_j02_flag_off_parity.py` to it, and run:

```bash
git worktree add /tmp/pcc-e2e/base <origin/main commit> --detach
PCC_E2E_NODE_ROOT=/tmp/pcc-e2e/base/backend/nodejs/apps PCC_E2E_CAPTURE=write \
  integration-tests/collaborative-chats/stack/run.sh -k j02_capture
```

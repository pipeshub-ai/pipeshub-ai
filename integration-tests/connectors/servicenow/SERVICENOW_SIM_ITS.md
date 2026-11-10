# ServiceNow connector against the ServiceNow simulator: integration tests

The `servicenow_sim` suite (`servicenow_sim_*_test.py`, marker `servicenow_sim`) runs the real PipesHub stack
against the connectors-simulator's ServiceNow simulator (`connsim serve servicenow`). It needs no ServiceNow
account and no secrets, so it runs on every CI run that selects its shard. The live `servicenow` suite
([README.md](README.md)) keeps running against a real instance when its secrets are set.

The simulator answers the Table, Aggregate and Attachment APIs of a Zurich instance over a generated instance:
14 knowledge bases, one per access pattern (default criteria, a group, a department, roles, a location, a
company, a user list, match-all, a scripted criterion, no criteria, Cannot Read, an inactive criterion, an
inactive knowledge base, a group whose members are only in child groups), with versions, wiki bodies,
attachments, inline images and feedback. Who may read what follows ServiceNow's rules, each verified on a real
instance (connectors-simulator `docs/SERVICENOW-SIMULATOR.md`, §5).

## Running it

First set up the integration suite as [integration-tests/README.md](../../README.md) describes: its
[prerequisites](../../README.md#prerequisites) and the PipesHub login, organization, admin account and graph
database settings in its [environment variables](../../README.md#environment-variables-reference). Then:

```bash
cd deployment/docker-compose
docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.servicenow-sim.yml up -d
cd ../../integration-tests
set -a; . connectors/servicenow_sim.env; set +a
pytest -m servicenow_sim -v
```

The `servicenow-sim` image is the connectors-simulator's (`docker build -t pipeshub/connectors-simulator:it .` in
that repository, at a commit with `connsim/sources/servicenow`). The connector reaches it as
`http://servicenow-sim`: a private address, allowed for a source an admin configures unless
PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on, over plain http, which the override allows with
`SERVICENOW_ALLOW_INSECURE_HTTP=1` on the PipesHub container. The tests drive the simulator's control API on
`127.0.0.1:8109`. Against a stack run from source, serve the simulator on a private address of the host
(`connsim serve servicenow --host 172.17.0.1`), set both URLs in the env file to it, and set
`SERVICENOW_ALLOW_INSECURE_HTTP=1` for the connectors service.

## How a test decides

`sim_world.py`: each test resets the instance (`/_sim/reset`), creates a connector that signs in with basic auth
as the integration user (an admin), syncs, changes the instance through the control API (`mutate`,
`clock/advance`, faults, rate-limit rules) and syncs again. Then a few people (PipesHub accounts with ServiceNow
users' emails, chosen so every article has three probed readers and three probed non-readers where it has them)
open every record of the connector. That must match the simulator's oracle, narrowed to what ServiceNow search
shows (no inactive knowledge base, nothing past `valid_to`): LEAK, DENIED, MISSING, STALE, DUPLICATE, UNSETTLED,
and any request the simulator does not model, fail the test. A fault that never fired fails the test too.

## Cases

| Id | What it checks |
| --- | --- |
| SYNC-001 | The first sync: every published article (one record per number) and its files, each opened by exactly its readers |
| SYNC-002 | A batch of instance changes (edits, versions, new, retired and deleted articles, files, criteria, memberships) |
| SYNC-003 | A new published version keeps the article's record |
| SYNC-004 | A full sync after changes |
| SYNC-005 | A knowledge base filter, then clearing it |
| ACC-001 | A deactivated user loses access |
| ACC-002 | Joining and leaving the group a Can Read criterion names |
| ACC-003 | `glide.knowman.block_access_with_no_user_criteria` turned off and on |
| FAIL-001 | 503s on the article listing are retried |
| FAIL-002 | 429s with Retry-After are waited out |
| FAIL-003 | An hourly rate-limit rule stops the run with a notice; the next sync after the rule is gone finishes |
| FAIL-004 | A refused sign-in (401) stops the run with a re-authorize notice |

## Not covered here

OAuth sign-in and token refresh, the connection test's refusals, streaming article bodies and inline images, and
notices other than the two above: the live suite and the simulator's in-process harness
(`harness/servicenow_connector_in_process.py`) cover them.

# Zammad connector against the Zammad simulator: integration tests

The `zammad_sim` suite (`zammad_sim_*_test.py`, marker `zammad_sim`) runs the real PipesHub stack against the
connectors-simulator's Zammad simulator (`connsim serve zammad`), so it can create the cases a shared Zammad cannot:
agents whose group access is only change, create or overview, access through roles, customers and shared
organisations, groups and roles switched off, knowledge-base answers in every state (scheduled ones too), Zammad 6.4.1
and 6.5 behaviour, revoked tokens and faults mid-listing. The live `zammad` suite (`zammad_integration_test.py`,
`zammad_scenario_matrix_test.py`) keeps running against the real `zammad-source` containers.

## Running it

First set up the integration suite as [integration-tests/README.md](../../README.md) describes: its
[prerequisites](../../README.md#prerequisites) and the PipesHub login, organization, admin account and graph
database settings in its [environment variables](../../README.md#environment-variables-reference). Then:

```bash
cd deployment/docker-compose
docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.zammad-sim.yml up -d
cd ../../integration-tests
set -a; . connectors/zammad_sim.env; set +a
pytest -m zammad_sim -v
```

The `zammad-sim` image is the connectors-simulator's (`docker build -t pipeshub/connectors-simulator:it .` in that
repository, at a commit with `connsim/sources/zammad`; CI pins `CONNSIM_ZAMMAD_REF`). The connector reaches it as
`http://zammad-sim` (a private address, allowed for a self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is
on); the tests drive its control API on `127.0.0.1:8112`. The stack's default
`ZAMMAD_FULL_TICKET_LISTING_INTERVAL_MS=0` makes every sync list each group in full, which the 6.4 deletion case
relies on.

## How a test decides

`sim_world.py`: each test loads an installation (`sim_instances.py`), creates a connector for it, syncs, changes the
installation, syncs again. After each sync the simulator's oracle (`GET /_sim/zammad/expected`) says who may read every
ticket, attachment and answer, and PipesHub's permission query (`filter_accessible_record_ids`, what search relies on)
is asked, for every person of the installation (each has a PipesHub account with the same email), which of the
connector's records they may read. The rules encoded are Zammad's, not the connector's:

- a group's readers are the active users holding `ticket.agent` with `read` or `full` on it, directly or through an
  active role; `change`, `create` or `overview` alone give nothing; a deactivated group gives nobody anything and its
  tickets leave PipesHub;
- an internal article's attachment is read only by agents who may read the group;
- an answer's state is its latest passed timestamp (archived wins over published; a scheduled one does not count yet);
  a published answer is everyone's, an internal one belongs to the roles with reader or editor access to its category
  (a role without a permission row has the access its `knowledge_base.reader` or `.editor` permission gives), a draft
  or archived one to the editor roles; a deleted answer leaves PipesHub;
- the scope is what the token's search lists, in the groups it can list (6.4.1 lists groups to `admin.group` only),
  active and allowed by the group filter, and the answers its `knowledge_bases/init` lists.

MISSING, STALE, DUPLICATE, PARENT, `LEAK:<rule>`, `DENIED:<rule>`, SIM_UNMODELED and SIM_SPEC fail a test, except
the differences named in `KNOWN_GAPS` (sim_world.py): today a customer's own tickets and their shared organisation's
tickets, which PipesHub does not grant to customers yet. Remove an entry once the connector grants it.

A sync whose reads were refused or failed must not report `succeeded`, and must leave every record and every grant as
it was (`assert_holdings_kept`); the next sync must match Zammad. A fault that never fired fails the test.

## Cases

Each test file's docstring lists its cases (TC-ZMSIM-ACL, -SYNC, -KB, -FAIL). They are the stories of the audit
harness's scenarios (connectors-simulator `harness/zammad/scenarios.py`, which run the connector in-process with a
real graph database), told through the deployed product. Several fail on code that still treats every access level
as group membership, ignores role group access, reads an answer's state from which timestamps are set, or reports a
sync that read nothing as succeeded; they pass with the audit's connector fixes.

## Not covered here

Notifications (no notification reader in this suite), the 10,000-ticket search window and dense windows (the harness
runs them; too slow for CI), changes landing mid-listing (deferred ops), the knowledge-base reader token, and a 6.4
Zammad that lists groups once a day (the stack lists every sync).

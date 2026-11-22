# BookStack connector against the BookStack simulator: integration tests

The `bookstack_sim` suite (`bookstack_sim_*_test.py`, marker `bookstack_sim`) runs the real PipesHub stack
against the connectors-simulator's BookStack simulator (`connsim serve bookstack`), so it can create the cases a
shared BookStack cannot: role permissions that open books but not pages, explicit denies across roles, an
instance in New York time, BookStack's default rate limit, 429s with Retry-After, purges, restores, moves through
the UI, and faults.
The live `bookstack` suite (`bookstack_integration_test.py`, `bookstack_scenario_matrix_test.py`) keeps running
against the real `bookstack-source` container.

## Running it

First set up the integration suite as [integration-tests/README.md](../../README.md) describes: its
[prerequisites](../../README.md#prerequisites) and the PipesHub login, organization, admin account and graph
database settings in its [environment variables](../../README.md#environment-variables-reference). Then:

```bash
cd deployment/docker-compose
docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.bookstack-sim.yml up -d
cd ../../integration-tests
set -a; . connectors/bookstack_sim.env; set +a
pytest -m bookstack_sim -v
```

The `bookstack-sim` image is the connectors-simulator's (`docker build -t pipeshub/connectors-simulator:it .` in
that repository, at a commit with `connsim/sources/bookstack`). The connector reaches it as `http://bookstack-sim`
(a private address, allowed for a self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on); the tests
drive its control API on `127.0.0.1:8110`.

## How a test decides

`sim_world.py`: each test loads an instance (`sim_instances.py`), creates a connector, syncs, changes the
instance, syncs again, and then every probed person (a PipesHub account with a BookStack user's email) opens every
record of the connector. That must match the simulator's oracle exactly: LEAK, DENIED, MISSING, STALE,
DUPLICATE, UNSETTLED, and any request the simulator does not model, fail the test. So does any stored shelf
(SHELF): only books, chapters and pages are synced. A fault that never fired fails the test too.

## Cases

The IDs, what each guards and where else it is covered are in the audit's `IT_CASES.md`; each test file's
docstring lists its cases. They are a subset of the audit harness's scenarios (connectors-simulator
`harness/bookstack/scenarios.py`), which run in-process with the real connector, processor and graph database;
these run the same stories through the deployed product.

## Not covered here

Notifications (no notification reader in this suite), the connection test, streaming under a 429, the upgrade
path and clock skew: the harness covers them.

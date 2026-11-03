# Testing a connector

A connector has three layers of tests. Unit tests are required for every connector. Integration tests are required unless the connector is listed in the coverage baseline, and that list should only shrink.

| Layer | Where | Needs | Runs in CI |
| --- | --- | --- | --- |
| Contract | `backend/python/tests/unit/connectors/test_connector_contract.py` | nothing | every PR (`unit-test.yml`) |
| Unit and behaviour | `backend/python/tests/unit/connectors/sources/` | nothing; your suite's `no_network` fixture blocks the network (there is no global block) | every PR, coverage gate |
| Integration | `integration-tests/connectors/<name>/` | a running PipesHub stack and a real source account | PRs from same-repo branches, nightly, manual dispatch |

## Contract test

You do not write this one; it finds your connector through the factory registry and checks it. It fails when:

- the class leaves an abstract method of `BaseConnector` unimplemented;
- `create_connector` does not name the base parameters (`logger`, `data_store_provider`, `config_service`, `connector_id`, `data_entities_processor`). It does not check `scope` or `created_by`; a missing one makes the factory return `None` at runtime, so take them as in `02-low-level-design.md`;
- the registry key is not lower-case ASCII letters, digits and underscores;
- two registry keys point at the same class by accident;
- anything under `app/connectors/sources/` imports `neo4j`, `arango`, `qdrant_client`, `pymongo`, `kafka` or `aiokafka` instead of going through the data store and the provider interfaces.

Run it as soon as the connector is registered:

```bash
cd backend/python && pytest tests/unit/connectors/test_connector_contract.py -x
```

A second contract test, `tests/unit/connectors/test_graph_call_contract.py`, parses every `graph_provider.<method>(...)` and `tx_store.<method>(...)` call in the connector code and checks it against the real provider signatures. It catches a call to a method that was renamed or never existed, which would otherwise fail only at sync time. Do not add to its `KNOWN_INTERFACE_GAPS` list; call a method that exists.

Do not add your connector to `KNOWN_INCOMPLETE` in the first file. That list is for placeholder beta connectors that predate the test.

## Unit and behaviour tests

Write behaviour tests: drive the real connector class against a fake of the source API, and assert on what it writes through `DataSourceEntitiesProcessor`. A test that mocks the connector's own private methods proves little; a test that runs `run_sync()` against a fake source and checks the records, record groups, permissions and checkpoints that come out catches real regressions.

Follow the layout the newer suites use, for example `tests/unit/connectors/sources/slack/behaviour/`:

```
tests/unit/connectors/sources/<name>/behaviour/
  conftest.py                 # autouse no_network fixture, the fake source, fake stores, recorded sleeps
  <name>_behaviour_fakes.py   # an in-memory source: users, groups, containers, items, ACLs, change feed
  test_<name>_<area>_behaviour.py
```

Copy these from the Slack `conftest.py` rather than writing new ones:

- an autouse `no_network` fixture that fails any socket connection to a real host;
- the fake source installed under the real HTTP layer (`httpx.MockTransport`, or the SDK's transport hook), so the connector's own client code runs;
- a fixture that records `asyncio.sleep` calls instead of sleeping, so rate-limit backoff is fast and can be asserted.

Root fixtures in `tests/conftest.py` (`logger`, `mock_graph_provider`, `mock_config_service`, `mock_blob_store`) are there for the narrow cases that need them.

Cover at least:

1. **First sync:** users, groups, containers and items from the fake become the expected `AppUser`, `AppUserGroup`, `RecordGroup` and `Record` objects, with the parent links and permissions the source has.
2. **Permissions:** an item shared with one user only is not readable by another; an item that inherits from its container gets no grants of its own; a group grant needs the group to exist. See `06-permissions.md` for what to assert.
3. **Incremental sync:** a change after the saved checkpoint is picked up; an unchanged item is not re-sent for indexing; a failed item holds the checkpoint before it (`FailedItems`).
4. **Deletion and move:** an item deleted or moved at the source is deleted or moved in PipesHub.
5. **Source errors:** 401 or revoked credentials, 403, 404, 429 with `Retry-After`, and 5xx each end the way `07-errors-and-logging.md` says: the right log content, no secret in the log, the right stream status, and no silent data loss.
6. **Content:** `stream_record` returns the bytes or blocks for each record type, and maps source failures through `stream_errors`.
7. **Filters:** each sync and indexing filter the connector declares changes what is fetched or indexed; a narrowed filter does not orphan records whose parents it excludes.
8. **Principals:** nested groups come out flattened; a deactivated user is in no member list; a user with a hidden email is granted through a pseudo-group that migrates when the email appears; a failed member listing leaves the stored members alone.
9. **Pagination:** more than one page of users, groups, members, containers, items, comments, attachments and permissions.
10. **Sync timing:** a change made in the fake while the first full listing runs arrives on the next sync; an expired cursor re-lists the container.
11. **Corner cases:** an empty container, a very large file, unicode names, a deleted user, a restricted item, an archived container.
12. **Ids and links:** two object types that reuse the same numeric id stay distinct; an association-only change and a permission-only change show up without a content re-index; a search or list cap does not skip the rest; a 200 with an empty body does not delete or count as an empty container; an expiring download URL is fetched again at stream time; a role set with a deny is not granted from another role.

Run your tests, then the whole unit suite:

```bash
cd backend/python
pytest tests/unit/connectors/sources/<name> -x
pytest tests/unit
```

`pytest.ini` adds `-q --timeout=30` and `asyncio_mode=auto`. Run pytest from `backend/python`; from the repo root it collects nothing and still exits 0.

## Integration tests

Integration tests run the connector inside a real PipesHub stack against a real source account, then check the graph, the vector store and search. Use `integration-tests/connectors/linear/` as the model.

A new suite needs:

1. `integration-tests/connectors/<registry key>/` with a `conftest.py` that creates the connector through `PipeshubClient`, and test modules named `*_test.py`.
2. A `*_scenario_matrix_test.py` that plugs the connector into the shared scenarios in `integration-tests/connectors/scenario_matrix.py` (add, update content, rename, share and unshare, delete, full sync, filter change, manual indexing, scheduled sync). It needs a `ScenarioAdapter` subclass for the source-side actions; a test class that subclasses `ConnectorScenarioMatrix`, carries the connector's marker, and lists what the source cannot do in `UNSUPPORTED` and product bugs you found in `KNOWN_BUGS` (strict xfail); and a module-scoped `scenario_adapter` fixture that creates the connector and yields the adapter.
3. A pytest marker for the connector in `integration-tests/pytest.ini`, on every test class in the suite.
4. The marker added to one of the `CONN_SHARD_*` lists and to the dispatch choices in `.github/workflows/integration-tests.yml`, and the credentials it needs wired from repository secrets there.
5. The credential variables, empty, in `integration-tests/env.local.template` and `env.prod.template`, named `<NAME>_TEST_<WHAT>`.
6. A skip when credentials are missing: call `source_unavailable()` from `integration-tests/helper/source_credentials.py`. It skips locally and fails in the nightly, where `PIPESHUB_REQUIRE_CONNECTOR_SECRETS` is set.
7. Test data that the suite creates and removes itself, with names that carry a run id, so two runs do not collide and a failed run can be swept.

The suite runs against both graph backends (Neo4j and ArangoDB) in CI; include revoked access and connector deletion (delete, then re-create: a clean sync with no duplicates).

The connector type a suite asks for must match the name passed to `ConnectorBuilder` exactly, spaces included. `integration-tests/unit/test_connector_types_are_registered.py` checks this on every PR.

## CI gates you will meet

| Gate | Workflow | Fails when |
| --- | --- | --- |
| Unit tests and coverage | `unit-test.yml` | a test fails, or line and branch coverage of `backend/python/app` (excluding `sources/external`) falls below the `--fail-under` value (89.8 today). A large, lightly tested connector can fail this on its own. |
| Ruff lint delta | `main.yml` (`ruff-lint-pr`) | a changed Python file gains a new finding (`.github/scripts/new_lint_findings.py`), or a changed test file has F811 (a test shadowed by another of the same name) |
| Connector coverage | `main.yml` (`connector-coverage`) | a newly registered connector with sync support has no `integration-tests/connectors/<registry key>/` directory containing a `*_test.py` or `test_*.py` file, and is not in `scripts/connector_coverage_baseline.json`. The directory is named by the registry key (or an `ALIASES` entry in `scripts/connector_coverage.py`), not the package name. |
| Registered connector types | `integration-suite-guards.yml` | a suite asks for a connector type the service does not register |
| OpenAPI | `openapi-validator.yml` | the spec fails Spectral lint, a route is missing from the spec, or route changes come without a spec update |
| Pyright | `main.yml` (`pyright-type-check-pr`) | non-fatal; read its output anyway |

Do not add a new connector to `connector_coverage_baseline.json` to get past the coverage gate unless the human agreed that it ships without integration tests. Say so in the PR.

With no credentials and no agreement to join that baseline, stop. A skip-only suite (`source_unavailable()`) passes a pull request when the secrets are absent and fails the nightly run, where `PIPESHUB_REQUIRE_CONNECTOR_SECRETS` is set. Do not add an empty `integration-tests/connectors/<key>/` to look done, and do not add the baseline entry yourself.

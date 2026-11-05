# Salesforce integration tests: design

## Two sources

| Mode | When | What it may do |
|---|---|---|
| Simulator | `connectors/salesforce_sim.env` is loaded (`SALESFORCE_SIM_URL` set) | Load its own org, change it, inject faults, ask the simulator who may read what |
| Live org | `SALESFORCE_TEST_INSTANCE_URL`, `SALESFORCE_TEST_CLIENT_ID`, `SALESFORCE_TEST_CLIENT_SECRET`, `SALESFORCE_TEST_REFRESH_TOKEN` | Read only: nothing is written to Salesforce. Cases that change the org skip |

Neither configured: the suite skips (`helper/source_credentials.source_unavailable`). The
simulator configured but not answering: the suite fails, so a misconfigured run never passes
having tested nothing.

What stays real: PipesHub (Node API, connector service, indexing, graph, KV store, broker),
the Salesforce connector and its HTTP client. What is simulated in simulator mode: the
Salesforce REST, Composite, Connect (Chatter) and OAuth endpoints (the `salesforce` simulator
of pipeshub-ai/connectors-simulator, `connsim/sources/salesforce`, docs/SALESFORCE-SIMULATOR.md
there). The scenario asks for its wall clock (`"clock": "wall"`): PipesHub keeps its own time.
The simulator refuses what Salesforce would
refuse (unknown fields, SOQL restrictions, composite limits) and answers 501 to anything it
does not model; the suite fails when the connector touched such a gap
(`GET /_sim/salesforce/unmodeled`). Where Salesforce's documentation and a live org disagreed
(getDeleted's window, refresh-token rotation, what an edited post or reply moves, Chatter's
hourly limit), the simulator does what the live org did.

## Authentication

The connector is created with the OAuth app fields (`instance_url`, `clientId`,
`clientSecret`), then `helper/oauth_token_helper.authenticate_connector_with_refresh_token`
exchanges the refresh token and writes the credentials into the KV store, as the OAuth
callback would. In simulator mode the refresh token is the one the simulator issued when the
scenario loaded. Note: the platform sends later refreshes to `login.salesforce.com` (or the
OAuth app's Salesforce login URL); the simulator's access token outlives the suite, so no
refresh happens during a simulator run.

## Cases

Access is always PipesHub's own permission query (`filter_accessible_record_ids`, what search
uses) for every user and every record of the connector, compared with the simulator's oracle.
A file is one record per record it is attached to and takes that record's readers, so files are
compared per document (a user reads some copy exactly when Salesforce lets them read the file),
and a copy readable by someone who cannot read its record fails the case. A record out of scope
fails the case only while someone can read it: a full sync unlinks what it no longer sees.

| Id | Case | Simulator | Live |
|---|---|---|---|
| TC-SF-001 | Records in PipesHub match Salesforce, type by type | all types against the oracle | deals, cases and products against SOQL counts |
| TC-SF-002 | Every user's access to every record equals Salesforce's; a user who may read only products reads only products; an inactive user reads nothing | yes | skips |
| TC-SF-003 | Hierarchy: a deal and a case under their account, a task under its record, a file under its record and inheriting from it, an account's file in the account's group, a task under nothing | yes | the structural checks |
| TC-SF-004 | An incremental sync picks up an edit and a new case | yes | skips |
| TC-SF-005 | A new post, an edited post, a new reply and an edited reply each send the deal for indexing again | yes | skips |
| TC-SF-006 | Sharing a deal, then removing the share | yes | skips |
| TC-SF-007 | An owner change moves access, and back | yes | skips |
| TC-SF-008 | A deactivated user loses all access and regains it when reactivated | yes | skips |
| TC-SF-009 | A frozen user loses all access and regains it when unfrozen | yes | skips |
| TC-SF-010 | A file taken off one record leaves that record only; a new version replaces the streamed content | yes | skips |
| TC-SF-011 | A deleted deal leaves with its task and file; restored, it comes back | yes | skips |
| TC-SF-012 | A deleted case, task, product and account (with its deal and task) leave | yes | skips |
| TC-SF-013 | Narrowing the date filter (a full sync) leaves only what is in its window readable, also after later syncs; clearing it brings everything back | yes | skips |
| TC-SF-014 | Two full syncs in a row leave every file readable | yes | skips |
| TC-SF-015 | A Salesforce read that fails (503) fails the sync and removes nothing; the next sync catches up | yes | skips |
| TC-SF-016 | A deal with 30 posts streams in full, quickly (no paging loop) | yes | skips |
| TC-SF-017 | With more than 1,000 records every user gets access, and a share and its removal apply | yes | skips |

Syncs are triggered with the product's resync call and repeated (minutes apart, as in the
product) until PipesHub matches Salesforce or five syncs have passed: Salesforce's getDeleted
ignores seconds, so a deletion shows from the next whole minute on.

Token refresh is not exercised here: the platform sends it to `login.salesforce.com` (or an
https `*.salesforce.com` login host), which the simulator cannot be. The connector audit's
in-process scenarios cover it.

## Running

Locally against a running PipesHub (`integration-tests/.env` with `PIPESHUB_TEST_ENV=local`, and
`.env.local` with `PIPESHUB_BASE_URL`, an admin's `PIPESHUB_TEST_USER_EMAIL`/`_PASSWORD`,
`TEST_NEO4J_*`, the instance's Redis (`REDIS_*`, `KV_STORE_TYPE=redis`) and `SECRET_KEY`, which
the suite needs to write the connector's tokens as the OAuth callback does). The simulator must be
on the PipesHub network as `salesforce-sim.internal` and published for the test process:


```bash
# PipesHub integration stack with the simulator service (image built from connectors-simulator)
docker build -t pipeshub/connectors-simulator:salesforce-it <connectors-simulator checkout>
SALESFORCE_SIM_IMAGE=pipeshub/connectors-simulator:salesforce-it docker compose \
  -f docker-compose.integration.neo4j.yml -f docker-compose.integration.salesforce-sim.yml up -d
cd integration-tests && set -a && . connectors/salesforce_sim.env && set +a && pytest -m salesforce -v
# or the simulator alone, beside a PipesHub of your own (it must reach it as salesforce-sim.internal)
connsim serve salesforce --host 127.0.0.1 --port 8118 --public-url http://salesforce-sim.internal
```

CI: shard `connectors-3` (and a manual run with `connectors: salesforce`). The workflow checks
out connectors-simulator at `CONNSIM_SALESFORCE_REF`, builds `SALESFORCE_SIM_IMAGE`, adds
`docker-compose.integration.salesforce-sim.yml` to every compose call and loads
`connectors/salesforce_sim.env`, as it does for the BookStack simulator. No secret: the
simulator issues the OAuth app and refresh token the suite uses.

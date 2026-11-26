# Linear against the simulator (`linear_sim`)

The suite runs the real PipesHub stack against the Linear simulator of `connectors-simulator` (`connsim`, port 8114). The
connector calls fixed hosts over HTTPS (`https://api.linear.app` for GraphQL and the OAuth token endpoint,
`https://linear.app` for the authorize page, `https://uploads.linear.app` for files), so the stack gets a TLS front that
answers those names; nothing in the product is switched. The GraphQL client is aiohttp and the file download is httpx: both
trust the front's CA through `SSL_CERT_FILE`.

## Run it

```bash
deployment/docker-compose/sim-tls/make-certs.sh "$SIM_TLS_DIR"       # one CA and certificate for every vendor front
docker compose -f deployment/docker-compose/docker-compose.integration.neo4j.yml \
               -f deployment/docker-compose/docker-compose.integration.linear-sim.yml up -d
cd integration-tests && set -a && . connectors/linear_sim.env && set +a
pytest -m linear_sim connectors/linear
```

The suite needs the docker CLI (`linear_front` checks where the Linear names resolve inside the PipesHub container and
that both clients verify the front's certificate; name the container with `LINEAR_SIM_APP_CONTAINER` when several stacks
run). The live suite of this folder (marker `linear`) is unchanged; `conftest.py` only imports this suite's fixtures.

The suite needs a simulator that has per-operation faults (`PUT /_sim/linear/op-faults`): every GraphQL request is
`POST /graphql`, so the core's fault rules (method, path, route) cannot tell one operation from another.

## How it fails closed

- `linear-tls` takes `api.linear.app`, `linear.app` and `uploads.linear.app` as network aliases and answers with the shared
  certificate; `pipeshub-ai` trusts its CA through `SSL_CERT_FILE`.
- `linear-guard` is healthy only while the three names resolve to the front; `pipeshub-ai` starts after it.
- `linear_front` checks the same inside the container before any connector exists, and that aiohttp and httpx both get an
  answer from the front (fails, never skips); AUTH-009 asserts nothing unmodelled reached the simulator; AUTH-010 is the
  negative control (a Linear name nobody aliased does not resolve to the front).

## Sign-in

Most tests use a personal API key (`Authorization: <key>`). AUTH-001/002 go through the app's authorize and callback routes
(authorization code, `Authorization: Bearer`); AUTH-007 revokes the tokens and signs in again; AUTH-008 lets the platform's
own refresh run (a 60 s access token: the platform refreshes at once and Linear replaces the refresh token).

## What a test asserts

After a sync, the simulator's oracle (`/_sim/linear/expected`) says who may read every issue, project, link, document and
file under Linear's rules (private, restricted and public teams, sub-teams, guests, suspended people). For every person (each
has a PipesHub account with the same address, plus `outsider@acme.example` with no Linear account) PipesHub's permission
query (`filter_accessible_record_ids`) is compared with it. Types: MISSING, STALE, DUPLICATE, TEAM, LEAK, DENIED,
SIM_UNMODELED, SIM_SPEC. The pure comparison and its negative controls are in `integration-tests/unit/test_linear_sim_compare.py`.

Scope of a sync: issues of the synced teams that are not trashed (an archived issue is tolerated), projects (also archived)
of those teams, links, documents, and files linked from issue, comment and project text on the upload host (images are
inlined, not records).

Faults name a GraphQL operation (`op_fault("issues", 403, after_n=1)`); the operations a first sync sends are listed in
`linear_sim_sync_test.py` (`SYNC_OPERATIONS`) and SYNC-008 fails when the sync sends another. A fault that never fired fails
its test.

## Known gaps (strict)

| What | Where |
|---|---|
| A team whose issues could not be read (403) does not fail the run: it is reported by a notification and the run is `succeeded` | `connector.py:817-829`; FAIL-004 is the strict xfail; safety (nothing lost, nobody gains, the next sync equals the source) holds and is asserted by FAIL-003 |
| `_run_step` steps (comments, reconciliation) that fail leave the run `succeeded` too | `connector.py:913-928` |

## Not covered

- A person with no e-mail address: Linear's schema declares `User.email` as `String!` (the simulator validates against it), so Linear never answers one without an address and the connector's skip of such a user cannot be reached.
- Signed file URLs, webhooks and subscriptions (not modelled by the simulator, not used by the connector).

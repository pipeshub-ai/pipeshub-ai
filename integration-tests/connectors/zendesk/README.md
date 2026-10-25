# Live Zendesk connector tests

This suite runs **20 tests** against a real Zendesk tenant, the running PipesHub
API, and the configured Neo4j or ArangoDB graph. The Zendesk API is never mocked.
It creates a temporary PipesHub connector and removes it after the module.
Zendesk source content is read-only.

Prepare a small, dedicated Zendesk test tenant with:

- One non-deleted ticket in a group, with a requester, assignee, at least one
  public comment, and stable subject/tags/status. A private comment makes the
  public-only stream assertion stronger, but is optional. Tag it
  `pipeshub-seed`. If the ticket in `constants.py` stops qualifying (Zendesk's
  AI agent escalating it clears the assignee and adds `escalated_by_ultimate`),
  the suite uses the oldest other `pipeshub-seed` ticket that still does, so
  keep a spare one and turn off automations that reassign seed tickets.
- One published, unrestricted Help Center article with a non-empty body in a
  section. Avoid editing the ticket or article while the suite is running.
- A **confidential** OAuth client (Admin Center > Apps and integrations > APIs >
  OAuth clients > Add OAuth client, client kind *Confidential*). Zendesk shows
  its secret only once, on save. Its tokens act as the admin who created it, so
  that admin must be able to read tickets, users, groups, comments, and Help
  Center articles; the tokens stop working if that admin loses the role. The
  suite requests only the `read` scope.

Set these Zendesk variables in `integration-tests/.env.local` or `.env.prod`
(or as environment variables):

```text
ZENDESK_TEST_SUBDOMAIN=your-subdomain
ZENDESK_TEST_CLIENT_ID=your-oauth-client-id
ZENDESK_TEST_CLIENT_SECRET=your-oauth-client-secret
ZENDESK_TEST_ACCESS_TOKEN_FILE=/tmp/zendesk-access-token
SECRET_KEY=the-same-value-as-the-running-pipeshub-stack
```

The authorize and token URLs are derived from `ZENDESK_TEST_SUBDOMAIN`; they
are not separate test variables. `ZENDESK_TEST_CLIENT_ID` and
`ZENDESK_TEST_CLIENT_SECRET` belong to the Zendesk OAuth client. The plain
`CLIENT_ID` and `CLIENT_SECRET` above belong to PipesHub's API client.

The running PipesHub stack also needs `PIPESHUB_BASE_URL`; either
`CLIENT_ID`/`CLIENT_SECRET` for the PipesHub API or, in local mode,
`PIPESHUB_TEST_USER_EMAIL`/`PIPESHUB_TEST_USER_PASSWORD` so the suite can create
an API client; and graph settings (`TEST_GRAPH_DB_TYPE=neo4j`,
`TEST_NEO4J_URI`, `TEST_NEO4J_USERNAME`, `TEST_NEO4J_PASSWORD`, optionally
`TEST_NEO4J_DATABASE`). The token injection helper must reach the same KV store
as the app (`REDIS_HOST`, `REDIS_PORT`, and `REDIS_PASSWORD` when Redis requires
one). `SECRET_KEY` must match the app. The PipesHub admin account must already
have one default LLM and one default embedding model configured; the Zendesk
suite reuses those models. See the [integration test guide](../../README.md) for the
ArangoDB and remote stack alternatives.

Set `ZENDESK_TEST_TICKET_ID` and `ZENDESK_TEST_ARTICLE_ID` in
[`constants.py`](constants.py) to the numeric IDs of the seeded ticket and
article. They are fixed test records, so their IDs live in source control
instead of GitHub secrets. The suite fails with a setup message until both IDs
are set.

Mint a token before running pytest:

```bash
cd integration-tests
python connectors/zendesk/prepare_token.py
pytest -m zendesk connectors/zendesk/zendesk_integration_test.py
```

The preparation step mints a 6-hour access token with the `client_credentials`
grant and writes it to the file named by `ZENDESK_TEST_ACCESS_TOKEN_FILE`.
Zendesk issues no refresh token for this grant, so nothing rotates: runs may
overlap, and nothing is saved between them. The tests also accept
`ZENDESK_TEST_ACCESS_TOKEN` for a one-off local run; this value expires and is
not used by CI.

The GitHub Actions **Integration Tests** workflow offers `zendesk` as a manual
dispatch choice. Set `ZENDESK_TEST_SUBDOMAIN`, `ZENDESK_TEST_CLIENT_ID` (the
client's unique identifier, not its numeric ID), and
`ZENDESK_TEST_CLIENT_SECRET` in the protected `integration-test-dev`
environment. Each run mints its own token and fails before testing if Zendesk
refuses it. Zendesk runs in the `connectors-2` shard on the nightly and on pull
requests from this repository, and alone when dispatched with `zendesk`. Ordinary
local runs skip when credentials are absent.

# Live Zendesk connector tests

This suite runs **19 tests** against a real Zendesk tenant, the running PipesHub
API, and the configured Neo4j or ArangoDB graph. The Zendesk API is never mocked.
It creates a temporary PipesHub connector and removes it after the module.
Zendesk source content is read-only.

Prepare a small, dedicated Zendesk test tenant with:

- One non-deleted ticket in a group, with a requester, assignee, at least one
  public comment, and stable subject/tags/status. A private comment makes the
  public-only stream assertion stronger, but is optional.
- One published, unrestricted Help Center article with a non-empty body in a
  section. Avoid editing the ticket or article while the suite is running.
- An OAuth access token for an account that can read tickets, users, groups,
  comments, and Help Center articles. The token must have at least the `read`
  scope. No write scope is needed.

Set these variables in `integration-tests/.env.local` or `.env.prod` (or as
environment variables), alongside the normal PipesHub and graph settings from
the [integration test guide](../../README.md):

```text
ZENDESK_TEST_SUBDOMAIN=your-subdomain
ZENDESK_TEST_CLIENT_ID=your-oauth-client-id
ZENDESK_TEST_CLIENT_SECRET=your-oauth-client-secret
ZENDESK_TEST_ACCESS_TOKEN_FILE=/tmp/zendesk-access-token
ZENDESK_TEST_TICKET_ID=12345
ZENDESK_TEST_ARTICLE_ID=67890
SECRET_KEY=the-same-value-as-the-running-pipeshub-stack
```

Put `ZENDESK_TEST_REFRESH_TOKEN=your-current-refresh-token` in
`integration-tests/.env.refresh_tokens`. That ignored file is updated after
each successful exchange.

After a fresh OAuth login, prepare a token before running pytest:

```bash
cd integration-tests
python connectors/zendesk/prepare_token.py
pytest -m zendesk connectors/zendesk/zendesk_integration_test.py
```

The preparation step requests a 24-hour access token, writes it to the file
named by `ZENDESK_TEST_ACCESS_TOKEN_FILE`, and replaces the refresh token in
`integration-tests/.env.refresh_tokens`. Keep that file outside version control
and do not run two local Zendesk suites using the same refresh token at once.
The tests also accept `ZENDESK_TEST_ACCESS_TOKEN` for a one-off local run; this
value expires and is not used by CI.

The GitHub Actions **Integration Tests** workflow offers `zendesk` as a manual
dispatch choice. Set `ZENDESK_TEST_SUBDOMAIN`, `ZENDESK_TEST_CLIENT_ID`,
`ZENDESK_TEST_CLIENT_SECRET`, `ZENDESK_TEST_REFRESH_TOKEN`,
`ZENDESK_TEST_TICKET_ID`, `ZENDESK_TEST_ARTICLE_ID`, and
`ZENDESK_TEST_SECRETS_WRITE_TOKEN` in the protected `integration-test-dev`
environment. Use a fine-grained GitHub token scoped to this repository with
`Environments: write` permission for the write token. Each run refreshes the
Zendesk token, saves the newly rotated refresh
token back to that environment, and then runs the tests. It fails before testing
if refresh or persistence fails. Zendesk dispatch jobs are serialized across
runs; a `both` graph selection uses one job with sequential backends.

An initial Zendesk OAuth login is required to obtain a valid refresh token.
After that, the CI job maintains the rotating token. Zendesk remains outside automatic CI
shards until these environment secrets have been provisioned. Ordinary local
runs skip when credentials are absent.

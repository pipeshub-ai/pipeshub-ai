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
ZENDESK_TEST_ACCESS_TOKEN=your-oauth-token
ZENDESK_TEST_TICKET_ID=12345
ZENDESK_TEST_ARTICLE_ID=67890
SECRET_KEY=the-same-value-as-the-running-pipeshub-stack
```

Run from `integration-tests/`:

```bash
pytest -m zendesk connectors/zendesk/zendesk_integration_test.py
```

The GitHub Actions **Integration Tests** workflow also offers `zendesk` as a
manual dispatch choice. Set the four `ZENDESK_TEST_*` repository environment
secrets before selecting it. A selected run fails if they are missing; ordinary
local runs skip when credentials are absent. Zendesk is not yet in an automatic
CI shard, because that would make nightly runs fail until the dedicated tenant
and secrets are provisioned.

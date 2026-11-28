# GitLab connector against the GitLab simulator: integration tests

The `gitlab_sim` suite (`gitlab_sim_*_test.py`, marker `gitlab_sim`) runs the real PipesHub stack against the
connectors-simulator's GitLab simulator (`connsim serve gitlab`), so it can create the cases a shared GitLab cannot:
members at every access level, confidential issues and their assignees, internal notes, blocked, deactivated and awaiting
members, a project shared with a group, group-inherited access, a project renamed or deleted, a token revoked or
expired, GitLab that hides members' e-mail or pages its tree by two, and faults on every endpoint. The live `gitlab`
suite (`gitlab_integration_test.py`, `gitlab_scenario_matrix_test.py`) keeps running against gitlab.com's fixture group.

## Running it

First set up the integration suite as [integration-tests/README.md](../../README.md) describes: its
[prerequisites](../../README.md#prerequisites) and the PipesHub login, organization, admin account and graph
database settings in its [environment variables](../../README.md#environment-variables-reference). Then:

```bash
cd deployment/docker-compose
docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.gitlab-sim.yml up -d
cd ../../integration-tests
set -a; . connectors/gitlab_sim.env; set +a
pytest -m gitlab_sim -v
```

The `gitlab-sim` image is the connectors-simulator's (`docker build -t pipeshub/connectors-simulator:it .` in that
repository, at a commit with `connsim/sources/gitlab`). The connector reaches it as `http://gitlab-sim`: GitLab is
self-hosted, so the instance URL is the service name, a private address PipesHub allows for a source an admin
configures unless `PIPESHUB_BLOCK_PRIVATE_ADDRESSES` is on. The GitLab client refuses to send a token over plain http to
any host but loopback unless the server sets `PIPESHUB_GITLAB_ALLOW_INSECURE_HTTP=true`, which the overlay sets on the
app container (a compose network is a network the operator trusts). The tests drive the simulator's control API on
`127.0.0.1:8113`.

## How a test decides

`sim_world.py`: each test loads an instance (`sim_instances.py`), creates a connector for one project of it (the connector
syncs exactly one repository), syncs, changes the instance, syncs again. The connector is created as the app does for an
OAuth connector, and the sync account's access token is stored as the OAuth flow stores it (`inject_access_token`);
`auth="api_token"` makes a personal-access-token connector instead. After each sync the simulator's oracle
(`GET /_sim/gitlab/expected`) says who may read every issue, merge request, file, folder and upload, and PipesHub's
permission query (`filter_accessible_record_ids`, what search relies on) is asked, for every person of the instance
(each has a PipesHub account with the same e-mail; the sync account is the login the suite runs as), which of the
connector's records they may read. The rules encoded are GitLab's, not the connector's:

- a private project is read by its members (direct, through its groups, through a group it is shared with, capped at the
  share's role); blocked, deactivated, banned and awaiting members read nothing; Minimal Access reaches no project;
- Guests read issues; Planners and up read confidential issues and internal notes (the author and assignees of a
  confidential issue read it too); Reporters and up read code and merge requests of a private project;
- an upload is read with the issue or merge request that links it; an upload linked only from internal notes has no
  record (the connector makes none);
- instance administrators and auditors read everything; a public project is read by anyone, an internal one by any
  signed-in user who is not external.

MISSING, STALE, DUPLICATE, PARENT, `LEAK:<rule>`, `DENIED:<rule>`, SIM_UNMODELED and SIM_SPEC fail a test, except
the differences named in `KNOWN_GAPS` (sim_world.py): today `DENIED:project-visibility`, because the connector grants a
project's members only. Remove an entry once the connector grants visibility. Two strict expected failures say so
(`TC-GLSIM-ACL-007`, `-008`).

A sync whose reads were refused or failed must not report `succeeded` unless PipesHub then equals GitLab, and must leave
every record and every grant as it was (`assert_holdings_kept`); the next clean sync must match GitLab. A fault that
never fired fails the test. The outcome of a lasting 403 is pinned both ways (`SILENT_403`).

## Cases

Each test file's docstring lists its cases (TC-GLSIM-SYNC, -AUTH, -ACL, -INC, -DEL, -FAIL, -FILT, -PAGE, -LIFE, -VAR,
-REIDX, -SEC, -PERS). They are the stories of the live suite's cases and of the audit harness's scenarios
(connectors-simulator `harness/gitlab/scenarios.py`), told through the deployed product.

## Not covered here

The browser sign-in (the OAuth endpoints of the connector are fixed to gitlab.com), the refresh of an OAuth token (the
platform's token service does it, against gitlab.com), the address policy (`PIPESHUB_BLOCK_PRIVATE_ADDRESSES`) and the
plain-http opt-in (both are server settings: a case needs another stack), a credential on a redirect to another host
(the simulator has no cross-host redirect; the client's guard has unit tests), the token of a blocked user (the simulator
still authenticates it), a lasting 503 (python-gitlab retries it for about two minutes a request), webhooks, epics and
wikis (not modelled or not synced).

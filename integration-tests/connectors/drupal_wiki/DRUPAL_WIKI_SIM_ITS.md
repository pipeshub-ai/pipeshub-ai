# Drupal Wiki connector against the Drupal Wiki simulator: integration tests

The `drupal_wiki_sim` suite (`drupal_wiki_sim_*_test.py`, marker `drupal_wiki_sim`) runs the real PipesHub stack against
the connectors-simulator's Drupal Wiki simulator (`connsim serve drupalwiki`), so it can create the cases a shared wiki
cannot: private spaces with user and group roles, spaces of every type and access status, shares, disabled users and
users with no email, a wiki that refuses the internal GraphQL queries (Drupal Wiki 8.44), the switches for what the
vendor's documentation leaves open, a redirected download, and faults on every endpoint the sync calls.

## Running it

First set up the integration suite as [integration-tests/README.md](../../README.md) describes: its
[prerequisites](../../README.md#prerequisites) and the PipesHub login, organization, admin account and graph
database settings in its [environment variables](../../README.md#environment-variables-reference). Then:

```bash
cd deployment/docker-compose
docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.drupal-wiki-sim.yml up -d
cd ../../integration-tests
set -a; . connectors/drupal_wiki_sim.env; set +a
pytest -m drupal_wiki_sim -v
```

The `drupal-wiki-sim` image is the connectors-simulator's (`docker build -t pipeshub/connectors-simulator:it .` in that
repository, at a commit with `connsim/sources/drupalwiki`). It serves its seed wiki and holds no secret; the token is
its default `pat:dash`. The connector reaches it as `http://drupal-wiki-sim.internal` (a private address, allowed for a
self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on); the tests drive its control API on `127.0.0.1:8108`.

## How a test decides

`sim_world.py`: each test puts the seed wiki back (`POST /_sim/reset`, with the knobs it names), creates a connector,
syncs, changes the wiki (`POST /_sim/drupalwiki/mutate`), syncs again. After a sync the simulator's oracle
(`GET /_sim/drupalwiki/expected`, computed from its tables, never through its routes) says which spaces, pages and
attachments a correct sync holds, where each sits and who may read it, and PipesHub's permission query
(`filter_accessible_record_ids`, what search relies on) is asked, for each of nine people, which records of the
connector they may read: five seed users (one disabled), three the tests add, and one person with a PipesHub account
and none in the wiki. The rules encoded are the wiki's, not the connector's:

- a page is read by whoever may read its home space, and by whoever may read a space it is shared into;
- an open space (anonymous or authenticated) is read by every enabled user; a private one by the holders of any role on
  it, directly or through a group; a disabled user reads nothing;
- only content spaces are part of a sync (basic, container, corporate blog and news): not the trash, a personal
  workspace, the system space, news drafts or the desktop.

MISSING, STALE, DUPLICATE, PARENT, MISSING-GROUP, STALE-GROUP, `LEAK:<rule>`, `DENIED:<rule>` and SIM_UNMODELED fail a
test, except the differences named in `KNOWN_GAPS` (sim_world.py) or in a call's `known=`: each names the open pull
request that fixes it, and the call fails once the gap no longer shows, so the entry goes when the pull request lands.

A sync whose reads were refused or failed must not report `succeeded`, must leave every record and every grant as it was,
and must give nobody a read the wiki does not (`assert_kept_not_overshared`); the next sync must match the wiki. A fault
that never fired fails the test. `integration-tests/unit/test_drupal_wiki_sim_compare.py` runs the comparison on a small
hand-written oracle, with a planted over-share, a missing grant, a missing, stale, duplicated and misfiled record each
required to be caught.

## Cases

Each test file's docstring lists its cases (TC-DWSIM-ACL, -AUTH, -CONT, -DEL, -FAIL, -FLT, -LIFE, -SEC, -SYNC, -VAR).

## Not covered here

The 30-day bound on access kept while the wiki refuses its member query follows the connector's own clock, which this
suite cannot move: `backend/python/tests/unit/connectors/sources/test_drupal_wiki_connector.py` covers it. Attachments
with no `fileName` exist only in the simulator's generated worlds, which this suite does not start. The wiki's
write API, webhooks and page locks are not used by the connector.

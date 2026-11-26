# Jira Data Center connector against the Jira Data Center simulator: integration tests

The `jira_dc_sim` suite (`jira_dc_sim_*_test.py`, marker `jira_dc_sim`) runs the real PipesHub stack against the
connectors-simulator's Jira Data Center simulator (`connsim serve jiradc`), so it can create the cases a shared Jira cannot:
every kind of Browse holder, issue security levels with every kind of holder, users deactivated or renamed or with hidden
e-mails, groups deleted, projects hidden from the sync account or archived, Jira 10.2 to 11 behaviour (no `/user/list`,
the search cap), cut-short embedded comments, deletions only the audit log tells, refused credentials, and faults at every
endpoint the connector calls. The personal connector has its own, smaller file.

## Running it

First set up the integration suite as [integration-tests/README.md](../../README.md) describes. Then:

```bash
cd deployment/docker-compose
docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.jira-dc-sim.yml up -d
cd ../../integration-tests
set -a; . connectors/jira_dc_sim.env; set +a
pytest -m jira_dc_sim -v
```

The `jira-dc-sim` image is the connectors-simulator's (`docker build -t pipeshub/connectors-simulator:it .` in that
repository, at a commit with `POST /_sim/jiradc/mutate` and `/_sim/jiradc/switches`). The connector reaches it as
`http://jira-dc-sim.internal` (a private address, allowed for a self-hosted source unless PIPESHUB_BLOCK_PRIVATE_ADDRESSES is
on); the tests drive its control API on `127.0.0.1:8111`. The world is generated and small (20 people, 12 projects, 60
issues, seed 7); every test starts by resetting it (`POST /_sim/reset`).

## How a test decides

`sim_world.py`: each test resets the site, creates a connector, syncs, changes the site, syncs again. The changes are named
ones (`POST /_sim/jiradc/mutate`: a user deactivated, a grant to any holder kind, a role emptied, a security level on a
named issue, a project hidden from the sync account or archived, ...) or the engine's seeded ones
(`POST /_sim/jiradc/clock/advance`). After a sync, the simulator's oracle says which tickets and attachments a complete sync
holds (`GET /_sim/jiradc/expected?ids=true`, with `ticket_index`) and which issues Jira lets each person read
(`GET /_sim/jiradc/access?email=..&ids=true`, computed from the rows), and PipesHub's permission query
(`filter_accessible_record_ids`, what search relies on) is asked, for every person and every record, who reads it. The rules
encoded are Jira's, not the connector's:

- a person reads an issue when a Browse holder of its project includes them (group, application role, anyone, user,
  project role, project lead; the issue's reporter or assignee when the scheme names those) and, for an issue at a security
  level, one of the level's holders does too; an attachment goes with its issue;
- only an active person with Jira product access is a source user the connector links; the PipesHub test user who creates
  the connector reads what the sync account reads;
- the scope is what the sync account sees, in the projects the project-key filter allows, created and modified inside the
  date filters.

MISSING, STALE, DUPLICATE, PARENT, `LEAK:<rule>`, `DENIED:<rule>` and SIM_UNMODELED fail a test, except what the test names
as a known gap (none today: `KNOWN_GAPS` is empty). A sync whose reads were refused or failed must not report `succeeded`
unless it read everything, must leave every record and grant as they were (`assert_holdings_kept`), and the next sync must
match Jira. A fault that never fired fails the test.

## Cases

Each test file's docstring lists its cases: `TC-JDSIM-` AUTH (setup), SYNC, ACL, INC, DEL, FLT, PAGE, FAIL, CONT, REIDX,
LIFE, VAR, SEC, PERS. The pure comparison has unit tests with negative controls in
`integration-tests/unit/test_jira_dc_sim_compare.py` (no stack).

## The failure matrix

`sim_campaign.py`: every endpoint the connector calls x {401, 403, 429, 500, dropped connection, malformed body}. SAFETY
assertions are never excused (the fault fired; nothing lost; a run that did not succeed changed nothing and gave nothing; a
run that did succeed shows no one what Jira does not; the next clean sync equals Jira). The OUTCOME (a run that could not read
what it needed does not report `succeeded`) is checked against `PARTIAL_SUCCESS`, the explicit set of `(endpoint, kind)` pairs
that still end as a success with the change unread: a pair that does it and is not listed fails, and so does a listed pair that
stops. `unit/test_jira_dc_sim_campaign.py` plants a loss, a leak, a gift and a fault that never fires to prove the assertions
bite. Refusals are bounded (three times the attempts the connector makes for one request of the kind), with one persistent case
per kind on the issue search.

## Known gaps

`sim_world.KNOWN_GAPS` tolerates two differences, both fixed by open #6114 (`jira_dc_sim_gaps_test.py` holds one strict expected
failure each).

## Not covered here

The connector's account removed and the PipesHub directory differences beyond ACL-013, redirects other than the attachment
download (SEC-004; Data Center's documentation does not describe a redirect, the simulator's switch is an assumption), restricted
comments as records (open #6212: TEXT-003 is its strict expected failure).

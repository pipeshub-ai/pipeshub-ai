# Live ServiceNow connector tests

This suite runs against a real ServiceNow instance (a free developer instance works), the running
PipesHub stack and its graph. ServiceNow is never mocked. Each test module seeds its own objects,
syncs them with its own connectors, changes them, and removes everything at the end.

| File | What it covers |
| --- | --- |
| `servicenow_integration_test.py` | sync, hierarchy, versions, deletes, attachments, content, lifecycle, filters |
| `servicenow_permissions_test.py` | each kind of user criterion, and a per-person oracle (below) |
| `servicenow_regression_test.py` | paging, current versions only, token refresh, revoked token, delete during sync, https-only URL, sign-in methods, notices |
| `servicenow_scenario_matrix_test.py` | the shared scenario matrix (`connectors/scenario_matrix.py`), with moves |
| `servicenow_seed.py` | the seeder; also runs on its own (`smoke`, `sweep`) |

## What the suite writes, and how it cleans up

Everything is created as the admin account and named after the run: `PHIT-<run>-…` (KBs,
categories, criteria, groups, departments, article titles), users `phit-<run>-…@example.com`,
roles `phit_<run>_…`. Nothing else on the instance is changed. The fixture users below are only
read, and added to (and removed from) the run's own groups.

- A new KB gets the platform's default criteria ("Any User" can read, "Has atleast one role" can
  contribute, "Guest User" cannot read and cannot contribute). The seeder removes them, except on the one KB that tests
  them.
- Publishing, checkout of a new version, revert and retire go through a background script in an
  admin UI session (`/sys.scripts.do`): writing `workflow_state` directly is reverted to draft, and
  a published row refuses every REST write, even from admin. Each edit of a published article is a
  checkout, a change on the draft, and a publish, as in the UI.
- ServiceNow caches user criteria: a change can read back stale for a few seconds. The oracle reads
  until two reads a few seconds apart agree.
- Teardown deletes the run's articles (every version), attachments, feedback, criteria and their
  links, categories, groups, memberships, roles, users and departments in one background script,
  then deactivates the run's KBs: KBs cannot be deleted through REST, so inactive `PHIT-…` KBs
  accumulate. A run that died before teardown is swept by the next run once its objects are two
  hours old (`STALE_HOURS`).

## Instance fixture

The instance needs, besides the admin account:

- **Versioning on** (`glide.knowman.versioning.enabled`), which the edit scenarios assume.
- **An OAuth app** (`oauth_entity`, type client) that allows the password grant. The suite mints
  an access and a refresh token for the admin account with it, and the connector signs in through
  it (`/oauth_token.do`).
- **Test users** (`sys_user`), each with the email `phqa.<name>@example.com` and a password:

| User | Needs |
| --- | --- |
| `phqa.plain`, `phqa.contrib`, `phqa.author`, `phqa.ogroup`, `phqa.kbmgr`, `phqa.owner` | nothing else (the suite makes them owner, manager, contributor or group member of its KBs) |
| `phqa.hr` | a company |
| `phqa.eng` | a location |
| `phqa.eng2` | a department and a location |
| `phqa.kadmin` | the `knowledge_admin` role |
| `phqa.kmanager` | the `knowledge_manager` role |
| `phqa.roledirect` | the role `phqa_reader` |
| `phqa.rolec` | a role that contains `phqa_reader` |
| `phqa.inactive` | `active=false` |
| `phqa.noemail` | no email (no password needed) |

A persona whose department, location or company is empty skips the case that needs it.

## Settings

Set these in `integration-tests/.env.local` (or `.env.prod`, or the environment):

```text
SERVICENOW_TEST_INSTANCE_URL=https://<instance>.service-now.com
SERVICENOW_TEST_USERNAME=<admin user>
SERVICENOW_TEST_PASSWORD=<admin password>
SERVICENOW_TEST_CLIENT_ID=<OAuth app client id>
SERVICENOW_TEST_CLIENT_SECRET=<OAuth app client secret>
SERVICENOW_TEST_PERSONA_<NAME>_PASSWORD=<one per user above, e.g. SERVICENOW_TEST_PERSONA_HR_PASSWORD>
```

For the other sign-in methods (each case skips without its values):

```text
SERVICENOW_TEST_INTEGRATION_USERNAME=<integration user, for basic auth>
SERVICENOW_TEST_INTEGRATION_PASSWORD=<its password>
SERVICENOW_TEST_API_KEY=<a REST API key of that user, sent as x-sn-apikey>
```

Client credentials (AuthType `CUSTOM`) reuse the OAuth app above: the instance needs the
client-credentials grant enabled (`glide.oauth.inbound.client.credential.grant_type.enabled`) and an
OAuth application user on the app, whom the token acts as. API keys also need an API access policy
on the Table and Attachment APIs that maps the key header profile. Optional:
`SERVICENOW_TEST_SYNC_TIMEOUT` (seconds, default 900).

The PipesHub side needs what every connector suite needs (`PIPESHUB_BASE_URL`, the API client or
test login, graph settings, AI models for indexing), plus:

- `SECRET_KEY` and the KV store settings (`REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`): the suite
  writes the connector's tokens the way the OAuth sign-in would (`inject_connector_credentials`).
- `MONGO_URI` (direct MongoDB access): each test user gets a PipesHub account under the same email
  (`source_user_account`), and logging in as it needs a password the suite sets there. The
  `phqa.*` accounts are kept between runs (an email whose account was deleted cannot join the org
  again); the run's own `phit-…` accounts are deleted at the end.

### Tokens

The suite stores an access token **and** a refresh token. An access token lives 30 minutes, which
is shorter than the suite; after a 401 the connector refreshes once through the linked OAuth app
(`refresh_now` with the stored refresh token). The platform's timed refresh does not run for these
tokens, because the stored credentials carry no expiry time; the 401 path covers them, and
TC-RES-05 tests exactly that path (an invalid access token with a valid refresh token).

## Running

```bash
cd integration-tests
python connectors/servicenow/servicenow_seed.py smoke     # seeder only: every primitive, then cleanup
python connectors/servicenow/servicenow_seed.py sweep     # remove other runs' objects older than 2 h
pytest -m servicenow connectors/servicenow                # the suite
```

The filter and auth-type cases read the connector's source to see whether it registers the filter
key (`knowledge_bases`, `folder_ids`, `modified`/`created`, the article/attachment indexing
switches) or the sign-in method, and skip with that reason until it does. Without the
`knowledge_bases` filter every connector here syncs the whole instance: still correct, slower.

## The permission oracle

`readable_numbers` calls the Table API **as the person**. ServiceNow applies the same read ACL
(`KBKnowledge.canRead`) as the portal, so that answer is the truth. The query is scoped to what is
indexed: published, active, in an active KB, `valid_to` empty or not past. A person who cannot log
in reads nothing. PipesHub's answer is the record endpoint as the person's account (the same access
check search and chat use), the knowledge hub tree as that account, and one search spot check. A
scripted criterion is the one place they differ on purpose: the connector cannot run the script,
so it fails closed (only the owner, managers and admins keep access).

## TC ids

Test case ids are `TC-<area>-<n>`; test names carry the ids they cover. Automated:

| Area | Ids |
| --- | --- |
| Sync | SYNC-01, 02, 03, 04, 05, 06, 12 |
| Hierarchy | HIER-01..08 |
| Permissions | PERM-01, 04..15, 17, 18, 19, 19b, 19c (property as the instance has it), 20..36, 39 |
| Versions, deletes | VER-01..04, DEL-01..07 |
| Attachments, content | ATT-01 (PDF, DOCX, XLSX, TXT, PNG), 02..05; CONT-01..08, 10 (link shape), 11 |
| Filters | FILT-01..11, 14 (skip until registered) |
| Resilience | RES-05, 06 |
| Notices | NOTIF-01, 02 (refused at enable, with the reason), 03, 06, 08 (one sign-in notice, admins only), 05, 07 (scripted criterion in one run summary, not repeated) |
| Sign-in methods | OAuth, basic auth, API key, client credentials |
| Lifecycle | LIFE-01..07 |
| Security | SEC-01 (http URL refused), SEC-04 (as CONT-07; the instance itself already strips `<script>`) |

Manual or unit-only, and why:

| Id | Why not here |
| --- | --- |
| SYNC-07, 08 | needs one article to fail (500 or ACL) while the rest read: unit tests with a fake transport (`backend/python/tests/unit/connectors/sources/servicenow/test_paging_and_checkpoints.py`) |
| SYNC-09 | timing of a change during the first sync is not reproducible |
| SYNC-10 | the order of reads inside a run: unit tests |
| SYNC-11 | needs the integration user's time zone changed, which affects the whole instance |
| PERM-02, 19c (other value) | flip instance-wide properties (`block_access_with_no_user_criteria`, `apply_article_read_criteria`) |
| PERM-03 | public/guest KBs: the guest user has no PipesHub account |
| PERM-16 | child departments: needs a test user's department rewritten in a tree the suite does not own |
| PERM-37, 38 | a 403 on one criteria or member table: unit tests (`backend/python/tests/unit/connectors/sources/servicenow/test_notices_and_access_gating.py`, `test_permissions.py`) |
| ATT-01 (PPTX) | no PPTX generator in the suite's dependencies |
| CONT-09 | a 200 MB attachment: instance size limits and run time |
| CONT-10 (open) | opening the link as a normal user in a browser |
| FILT-12, 13 | extension and size filters are out of scope for this connector |
| RES-01, 02 | needs a rate-limit rule for the integration user, which other runs would hit |
| RES-03, 04 | 5xx and timeouts on one page: unit tests and the ServiceNow simulator |
| RES-07 | an instance that hibernates or cannot be reached |
| RES-08, 09 | removing read ACLs on `sys_user_group` or `sys_properties` changes the instance for everyone |
| RES-10 | concurrency is read from the logs |
| NOTIF-04 | a sync that fails part way for a reason other than sign-in, access or rate limit cannot be caused from outside: unit tests (`backend/python/tests/unit/connectors/sources/servicenow/test_notices_and_access_gating.py`) |
| LIFE-08 | the scheduler test runs on one connector only (the Nextcloud matrix) |
| SEC-02 | secrets in logs: a log scan |
| SEC-03 | the address policy: unit tests |

## CI

The suite runs on the `connectors-2` shard (nightly and pull requests from this repository) and
alone when the **Integration Tests** workflow is dispatched with `servicenow`. Its settings come
from the secrets named in `.github/workflows/integration-tests.yml` (the `SERVICENOW_TEST_*` lines
above); until they exist in the protected environments the suite skips, and on the nightly it fails
instead (`PIPESHUB_REQUIRE_CONNECTOR_SECRETS`). A developer instance hibernates after a few days
without use: the suite then fails at setup with "did not answer as admin".

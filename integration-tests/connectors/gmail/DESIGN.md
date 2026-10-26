# Gmail integration tests: design

Two suites, one per connector:

| Suite | Connector | Marker | Directory |
|---|---|---|---|
| Gmail Workspace | `GMAIL WORKSPACE` (team, service account with domain-wide delegation) | `gmail_workspace` | `connectors/gmail_workspace/` |
| Gmail (personal) | `GMAIL` (personal, OAuth) | `gmail_individual` | `connectors/gmail_individual/` |

Shared pieces live in `connectors/gmail/`: the simulator client (`gmail_sim.py`), the environment reading
(`gmail_env.py`) and this document.

## 1. Why a simulator, and what stays real

A real Gmail test needs real mailboxes whose content a test can create, change and delete on demand, and whose
history Gmail keeps for a week. That makes most cases slow, flaky or impossible (expired history, suspended
users, delegation removed, rate limits). So the Gmail API is simulated, and everything else is real.

| Part | In these tests |
|---|---|
| PipesHub (Node API, connector service, indexing, graph, KV, broker) | real, the integration compose stack |
| Gmail connectors, their Google clients (google-api-python-client, google-auth) | real |
| Gmail API v1, Admin SDK Directory v1, Drive files.get, Google's OAuth token endpoint | the Gmail simulator (`connectors-simulator`, simulator `gmail`), compose service `gmail-sim` |
| Credentials | the Google Drive suites' own: same environment variables, same service account key, same OAuth client and refresh token |

The simulator validates every request and response against Google's published Discovery documents and answers
501 to anything it does not model, so a test cannot pass on a request the real API would refuse.

## 2. How the connector reaches the simulator

The Gmail connectors read `GMAIL_GOOGLE_API_BASE_URL` (backend, `app/connectors/sources/google/common/
gmail_endpoint.py`). When it is set, every Google call the Gmail connectors make (Gmail, the Directory API, Drive
for files linked in mail, and the token endpoint) goes to that origin instead of Google's hosts. Nothing else
reads it: the Google Drive connectors in the same stack keep calling Google, so the Drive suites are unaffected.
It is empty in production and in every compose file by default; the integration compose passes
`${GMAIL_GOOGLE_API_BASE_URL:-}` through, and CI sets it to `http://gmail-sim.internal` only when the Gmail
suites run. The connector logs a warning at start-up when it is set.

The test process reaches the simulator on its published port (`GMAIL_SIM_URL`, default
`http://localhost:8106`) to build mailboxes and change them.

## 3. Authentication: same auth, same credentials as the Drive suites

**Workspace** reads the Drive Workspace variables:

| Variable | Use |
|---|---|
| `GOOGLE_DRIVE_WORKSPACE_SERVICE_ACCOUNT_JSON` | uploaded to the connector exactly as in the Drive suite; its `client_email` and public key (derived from `private_key` by the test) are registered with the simulator |
| `GOOGLE_DRIVE_WORKSPACE_ADMIN_EMAIL` | the connector's admin; an administrator in the simulated Workspace |
| `GOOGLE_DRIVE_WORKSPACE_TEST_USER_EMAIL` | the mailbox owner the suite logs in as |
| `GOOGLE_DRIVE_WORKSPACE_SECOND_USER_EMAIL` | a second mailbox owner (access isolation) |

The connector signs its domain-wide-delegation JWT with the real key; the simulator checks the signature with
the registered public key, the issuer, the audience, the scopes and the impersonated user, and refuses what
Google would refuse. The simulated Workspace's domain is the admin's domain, so the users are the same
addresses the Drive suite uses and the same PipesHub users can log in.

**Personal** reads the Drive personal variables:

| Variable | Use |
|---|---|
| `GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET` | the OAuth app registered in PipesHub (as in the Drive suite) and with the simulator |
| `GOOGLE_DRIVE_REFRESH_TOKEN` | registered with the simulator as the user's refresh token; exchanged by `helper/oauth_token_helper.py` at the simulator's token URL |
| `GOOGLE_DRIVE_TEST_USER_EMAIL` | the connected Gmail account and the PipesHub user who creates the connector |
| `SECRET_KEY` and the Redis variables | as for the Drive personal suite (the token helper writes the credentials into the KV store) |

No request leaves the stack for Google, and no secret is sent anywhere but the local simulator. A missing variable
skips the suite (`helper/source_credentials.source_unavailable`), as the Drive suites do.

## 4. Test data

Each suite builds its mailboxes through the simulator's control API (`PUT /_sim/gmail/scenario`), the way the
Drive suites create files through the Drive API, and changes them with `POST /_sim/gmail/ops` (deliver, archive,
trash, untrash, delete, suspend a user, expire history, ...). The scenario is small and written out in the test
module so a reader can check the expected numbers by eye. `GET /_sim/gmail/expected` answers what a correct sync
must contain; tests compare the graph with it by message id.

Each class gets its own connector instance; teardown disables and deletes it and checks the graph is clean.

## 5. Cases

### Gmail Workspace (`gmail_workspace`)

| Id | Case | Proves |
|---|---|---|
| TC-GMAIL-WS-001 | First sync: every in-scope message copy of the test user and the second user is a MAIL record, every attachment a FILE record under its message; trash and spam are not synced | entity mapping, attachments, scope (checklist 3.1, 3.2, 4.6) |
| TC-GMAIL-WS-002 | Mailbox record groups: one mailbox group per user with INBOX, SENT and OTHERS under it; the owner's grant on the mailbox | record groups (3.5, 4.1) |
| TC-GMAIL-WS-003 | Access: the test user opens their own message; the second user cannot open it (record endpoint as each logged-in user) | access isolation (5.3, 5.15) |
| TC-GMAIL-WS-004 | Incremental: mail delivered after the first sync (including mail a filter archives on arrival) appears | incremental sync (6.3) |
| TC-GMAIL-WS-005 | Archive moves a message from INBOX to OTHERS; moving it back restores it; no re-index | moves (4.4, 3.4) |
| TC-GMAIL-WS-006 | Trash removes the message and its attachments; untrash brings it back | deletion, restore (7.1) |
| TC-GMAIL-WS-007 | Permanent delete and spam remove the message | deletion (7.1) |
| TC-GMAIL-WS-008 | Expired history (404): the mailbox is read again and mail deleted meanwhile is removed | expired cursor (6.6) |
| TC-GMAIL-WS-009 | Narrowing the Received Date filter removes older mail on the next (full) sync; widening adds it back | filter change (8.2) |
| TC-GMAIL-WS-010 | Stream: a message streams its body; an attachment streams its exact bytes | streaming (3.8) |
| TC-GMAIL-WS-011 | Suspending the second user takes their mailbox grant away; reinstating gives it back | deactivated users (5.13) |
| TC-GMAIL-WS-012 | Delegation removed: the sync fails, nothing is removed, and the next sync after it is restored succeeds | failure safety (5.7, 6.10) |
| TC-GMAIL-WS-013 | Deleting the connector removes all its records, groups and sync points | lifecycle (7.6) |

### Gmail personal (`gmail_individual`)

| Id | Case | Proves |
|---|---|---|
| TC-GMAIL-IND-001 | First sync of the connected mailbox; the creator can open the messages, a second user cannot | personal access (5.16) |
| TC-GMAIL-IND-002 | Incremental: new mail appears; trashed mail goes | incremental, deletion |
| TC-GMAIL-IND-003 | Access revoked in Google (refresh token revoked): the sync fails and the creator is told to reconnect | token handling (2.3, 9.1) |
| TC-GMAIL-IND-004 | Deleting the connector removes its data | lifecycle (7.6) |

## 6. Running

Local:

```bash
# simulator (from a connectors-simulator checkout that has the gmail simulator)
CONNSIM_SOURCES=gmail connsim serve gmail --port 8106
# PipesHub stack with the Gmail connectors pointed at it
GMAIL_GOOGLE_API_BASE_URL=http://host.docker.internal:8106 docker compose ... up -d
cd integration-tests && GMAIL_SIM_URL=http://localhost:8106 pytest -m "gmail_workspace or gmail_individual" -v
```

CI: the suites carry the `gmail_workspace` and `gmail_individual` markers and run on connector shard 4 with the
Google Drive suites (the shard that already has the Google credentials). The shard starts the `gmail-sim` compose
profile and loads `connectors/gmail_sim.env`. The workflow's `CONNSIM_REF` must point at a connectors-simulator
commit that contains the `gmail` simulator.

## 7. What these tests do not cover

- Real Gmail behaviour the simulator models from documentation and reports rather than observation; each
  modelled behaviour has a source and a confidence level in the simulator's `behaviours.py`. A periodic run
  against a real Workspace (not part of CI) is the way to catch a difference.
- Quota and rate-limit behaviour at production scale: covered by the connector audit's harness
  (`connectors-simulator/harness/gmail`), not here, because it needs minutes of wall-clock per case.
- The Gmail agent toolset (send, search) and the MCP server.

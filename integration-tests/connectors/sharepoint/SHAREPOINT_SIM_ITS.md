# SharePoint Online simulator integration tests

These tests run the real PipesHub stack against a simulated SharePoint Online tenant. Each test builds a
small tenant, syncs it, changes it, syncs again, and checks that every person can open in PipesHub exactly
what SharePoint lets them read.

They live next to the live SharePoint suite, which reads a real tenant and changes nothing. These tests own
their tenant and change it freely. Marker: `sharepoint_sim`.

## How the suite works

**The simulator.** The `sharepoint` simulator of connectors-simulator answers Entra ID sign-in, Microsoft
Graph and SharePoint REST. It holds one tenant in memory. Tests load a tenant, change it, inject faults and
read its oracle through a control API (`/_sim/...`) on a published port.

**Reaching it from PipesHub.** The connector calls fixed hosts over HTTPS: `graph.microsoft.com`,
`login.microsoftonline.com`, `contoso.sharepoint.com` and `contoso-my.sharepoint.com`. Nothing in PipesHub
can point it elsewhere. So the compose override `deployment/docker-compose/docker-compose.integration.sharepoint-sim.yml`
adds:

- `sharepoint-sim`, the simulator;
- `sharepoint-tls`, an nginx front that takes those four names as network aliases, answers with a
  certificate from a CA made for the run, and passes each request on with its Host header;
- `SSL_CERT_FILE` in the PipesHub container, pointing at the normal public roots plus that CA.

`deployment/docker-compose/sharepoint-sim/make-certs.sh` makes the certificate and the bundle. No
PipesHub code changes. Every Microsoft call from that stack goes to the simulator, so never run the live
`sharepoint` suite on it.

**People.** Every person in the test tenants (alice, bob, carol, dan, erin, frank at contoso.com, and the
guest gus@partner.example) gets a PipesHub account with the same email for the session. Connector
permissions are keyed by email, so these accounts receive what the connector grants.

**The check.** After each sync, the test asks the simulator's oracle (`/_sim/sharepoint/expected`) who may
read each file and page. The oracle is computed from the tenant alone. Then each person, logged in, opens
every record of the connector (`GET /api/v1/knowledgeBase/record/:id`, the access check search and chat
use). The test fails on any difference:

| Problem | Meaning |
|---|---|
| LEAK | Someone opens a record SharePoint does not let them read. |
| DENIED | Someone can read an item in SharePoint but PipesHub refuses the record. |
| MISSING | An item that should be synced has no record. |
| STALE | A record of a deleted item, or of an item outside the filters, still opens for someone. |
| OUT_OF_SCOPE | A filter excludes an item, but its record is still in the graph. A filter change must delete it. |
| EXTRA | A record of something that should never be a record opens for someone. |
| DUPLICATE | Two records for one item, or a page stored again as a file. |
| UNSETTLED | Opening a record gave a server error or an expired login. Nothing is proven. |
| SIM_UNMODELED / SIM_SPEC | The connector sent a request the simulator does not model, or a reply broke Microsoft's API document. |

Folder records are not compared, as in the audit harness these tests come from. The check retries for up
to 90 seconds, because graph writes land shortly after a sync ends.

Each sync is started through the API (enable, resync, or full resync). The test waits until the product
records the run's end, then reads its outcome (`lastSyncOutcome`). A test that injects a fault fails if
the fault never fired. The first sync fails if the connector never signed in at the simulator: that
catches a stack where Microsoft's names do not lead to the simulator.

**Files.**

- `sim_world.py`: the simulator client, connector set-up, sync waits, faults, the check, the fixtures.
- `sim_tenants.py`: the tenants. A base tenant with five sites (HR on an M365 group, Engineering with a
  subsite and a restricted library, a public Intranet, Finance, a OneDrive) and a small one-site tenant.
- `sharepoint_sim_sync_test.py`, `sharepoint_sim_access_test.py`, `sharepoint_sim_deletion_test.py`,
  `sharepoint_sim_failure_test.py`: the tests.

## In CI

`.github/workflows/integration-tests.yml` runs the suite on shard `connectors-3` (and on a dispatch of
`sharepoint_sim`). The SharePoint simulator has its own pin, `CONNSIM_SHAREPOINT_REF` (connectors-simulator's
main), and image, `SHAREPOINT_SIM_IMAGE`: the other simulators' `CONNSIM_REF` is still a branch that is not on
that main. The "Use the SharePoint
simulator" step builds that image, makes the TLS material, loads `sharepoint_sim.env`, and sets
`SHAREPOINT_SIM_COMPOSE`, which adds the override to every compose call of that job. The override sends all of
that stack's Microsoft traffic to the simulator, so the live `sharepoint` (shard 1) and `outlook` (shard 2)
suites must never share its shard.

## Running it locally

1. Build the simulator image from connectors-simulator (`docker build -t pipeshub/connectors-simulator:it .`). The override uses that tag unless
   `SHAREPOINT_SIM_IMAGE` names another.
2. Make the TLS material: `deployment/docker-compose/sharepoint-sim/make-certs.sh /some/dir` and export
   `SHAREPOINT_SIM_TLS_DIR=/some/dir`.
3. Start the stack with the override. From the repository root, in a subshell so you stay there:
   `(cd deployment/docker-compose && docker compose -f docker-compose.integration.neo4j.yml -f docker-compose.integration.sharepoint-sim.yml up -d)`
   (or the ArangoDB file), create the org and admin as CI does.
4. Load `integration-tests/connectors/sharepoint_sim.env` into the environment, with the usual
   integration-test variables (base URL, admin login, graph database, Mongo, Redis, AI models).
5. Run `pytest -m sharepoint_sim connectors/sharepoint` from `integration-tests`, once with
   `TEST_GRAPH_DB_TYPE=neo4j` and once with `arango`.

The tests run one after another. Each creates its own connector and deletes it at the end.

## The tests

Latest run, 2026-10-24: PipesHub built from commit 302b023b2 (the SharePoint fixes, PR #4507), the
simulator built from the connectors-simulator SharePoint source of the same day, and these tests on
upstream main 3b64393fc. Result: 40 of 40 pass on each database. The pytest run took 11.2 minutes on Neo4j and 10.8 minutes
on ArangoDB, not counting starting the stack. Indexing used a local Ollama model (qwen2.5:0.5b with
nomic-embed-text); nothing in these checks waits for indexing.


"Finding" names the finding of the SharePoint connector audit (PR #4507) that the test guards. Those tests fail on upstream main
and pass with the fixes, according to the audit harness. Tests without a finding guard behaviour that was
already right.

The simulator's defaults follow what a live tenant did: delta items carry `parentReference.path` and no
download URL, a deleted folder reports every child, `/drives` does not list Site Pages, and a single-geo
tenant has no data location on its site collections. A test that depends on another answer sets it
explicitly through `/_sim/sharepoint/behaviours`:

- SYNC-001 lists the Site Pages library (the case that stored every page twice).
- DEL-002 has delta report only the deleted folder, not its children.
- ACL-001 has delta not report the children of a folder whose sharing changed.
- The app has the simulator's default roles, which include Graph Sites.FullControl.All (as docs.pipeshub.com asks), except in FAIL-005 and FAIL-006.
- All other tests hide the Site Pages library explicitly, so they do not depend on that default.

### Sync, filters and streaming (`sharepoint_sim_sync_test.py`)

| Test | Set-up | Change | Asserts | Finding | Neo4j | ArangoDB |
|---|---|---|---|---|---|---|
| SYNC-001 first sync matches source | Base tenant; Site Pages listed among the drives (set explicitly) | None | First sync succeeds; the check passes, so no page is stored twice | F-PAGES-AS-FILES | pass | pass |
| SYNC-002 unchanged sync changes nothing | Small tenant | A second sync with no change | Succeeds; no record appears, goes or changes version; the check passes | | pass | pass |
| SYNC-003 incremental picks up changes | Base tenant | Add a file, edit one, rename one, add a folder, move a file into it | Succeeds; the check passes; the edited file's stored revision changes; the rename keeps the same record; every record sits under its source parent | F-ROOT-PARENT | pass | pass |
| SYNC-004 full sync converges | Base tenant | Add a file, delete one, full sync | Succeeds; the deleted file's record is no longer active; the check passes | | pass | pass |
| FLT-001 site filter narrowed then widened | Base tenant | Filter to Engineering only; then clear the filter | After narrowing, records of other sites are deleted and nobody opens them; after widening, everything is back | | pass | pass |
| FLT-002 library filter | Base tenant, filter to Engineering's Documents before the first sync | None | Only that library is synced; nothing else has a record | | pass | pass |
| FLT-003 extension filter narrowed | Base tenant | Filter to pdf | Records of other files are deleted and nobody opens them | | pass | pass |
| FLT-004 library filter narrowed | Base tenant | Filter to Engineering's Documents library after the first sync | Records of other libraries are deleted and nobody opens them | | pass | pass |
| STR-001 files and pages stream | Base tenant | Delete one file after the sync | Three files stream exactly the simulator's bytes; a page streams its text; the deleted file streams 404 | | pass | pass |

### Who can open what (`sharepoint_sim_access_test.py`)

Every test here ends with the check (LEAK, DENIED and the rest) after each sync.

| Test | Set-up | Change | Asserts | Finding | Neo4j | ArangoDB |
|---|---|---|---|---|---|---|
| ACL-001 folder unshare reaches children | Base tenant; HR's Policies folder shared with frank before the first sync; delta does not report inherited children (set explicitly) | Remove frank's share | frank opens the folder's files before, and not after | F-INHERITED-COPIES | pass | pass |
| ACL-002 site-level grant revoked | Base tenant; frank granted Read on the Finance site | Remove the grant | frank loses every Finance file | F-INHERITED-COPIES | pass | pass |
| ACL-003 library permissions tightened | Base tenant | Remove Engineering Visitors from the Documents library | Visitors lose its files | F-INHERITED-COPIES | pass | pass |
| ACL-004 folder moved into restricted folder | Base tenant | Move Specs into Restricted | Only Restricted's readers open the moved files | F-INHERITED-COPIES | pass | pass |
| ACL-005 guest and "Everyone" claims | Base tenant with the guest gus in PipesHub | None | gus opens what "Everyone" may read (Intranet) and not what "Everyone except external users" may read (Engineering) | F-GUEST-EVERYONE-EXT | pass | pass |
| ACL-006 disabled user loses access | Base tenant | Disable carol | carol opens nothing | F-DISABLED-USER | pass | pass |
| ACL-007 deleted user loses access | Base tenant | Delete erin | erin opens nothing | F-DISABLED-USER | pass | pass |
| ACL-008 disabled user enabled again | Base tenant | Disable erin, sync, enable her, sync | erin's access is back | | pass | pass |
| ACL-009 nested group member removed | Base tenant; bob is in Engineering Leads, which is in Engineering | Remove bob from Engineering Leads | bob loses what Engineering gave him | F-NESTED-GROUP-STALE | pass | pass |
| ACL-010 deeply nested group member | Base tenant plus frank in a group inside Engineering Leads | None | frank opens what Engineering may read | F-NESTED-GROUP-DEPTH | pass | pass |
| ACL-011 group membership added and removed | Base tenant | Add frank to Finance, sync; remove him, sync | frank gains, then loses, Finance | | pass | pass |
| ACL-012 sharing link to people | Base tenant | Share the roadmap with frank by link; then delete the link | frank opens it, then does not | | pass | pass |
| ACL-013 deleted SharePoint group | Base tenant plus a Finance "Auditors" group (frank) with access to an Audit folder | Delete the Auditors group | frank loses the Audit folder | F-SITE-GROUP-DELETE | pass | pass |
| ACL-014 owners group titled in French | Base tenant; HR's groups titled "Propriétaires de HR" and "Membres de HR"; a Board folder open to owners only | None | Only the M365 group's owner (dan) opens the Board files, not every member | F-M365-OWNERS-TITLE | pass | pass |
| ACL-015 unique folder user revoked | Base tenant; frank granted Read on HR's Salaries folder (which already has its own permissions) before the first sync | Remove frank's grant | frank opens the Salaries files before, and not after | F-INHERITED-COPIES | pass | pass |

### Deletions and change tracking (`sharepoint_sim_deletion_test.py`)

| Test | Set-up | Change | Asserts | Finding | Neo4j | ArangoDB |
|---|---|---|---|---|---|---|
| DEL-001 deleted file disappears | Base tenant | Delete a file | Its record no longer opens | | pass | pass |
| DEL-002 deleted folder takes children | Base tenant; delta reports only the folder, not its children (set explicitly) | Delete HR's Salaries folder | Its files and subfolder files no longer open | F-FOLDER-DELETE-CHILDREN | pass | pass |
| DEL-003 deleted library disappears | Base tenant | Delete Engineering's Archive library | Its files no longer open | F-CONTAINER-DELETE | pass | pass |
| DEL-004 deleted site disappears | Base tenant | Delete the Finance site | Its files no longer open | F-CONTAINER-DELETE | pass | pass |
| DEL-005 deleted page disappears | Base tenant | Delete the Engineering news page | The page no longer opens | F-PAGE-DELETE | pass | pass |
| DEL-006 expired delta tokens | Base tenant | Delete two items, add one, then make Graph answer 410 to every delta token | After the resync the deleted items are gone and the new one is there | F-DELTA-RESET-LOSES-DELETES | pass | pass |
| DEL-007 delta error keeps deletions | Small tenant | Delete a file; one sync while delta answers 502; then a clean sync | The deleted file is gone after the clean sync | F-DELTA-RESET-LOSES-DELETES | pass | pass |
| DEL-008 empty library listing | Base tenant | One sync while every site's library listing comes back empty | Nothing is removed | | pass | pass |

### Syncs that cannot read everything (`sharepoint_sim_failure_test.py`)

| Test | Set-up | Change | Asserts | Finding | Neo4j | ArangoDB |
|---|---|---|---|---|---|---|
| FAIL-001 [drives-listing] | Base tenant | Full sync while Engineering's library listing answers 503 | The run is not a success; nobody loses access | F-FULLSYNC-PARTIAL-REMOVES, F-SUCCESS-ON-FAILURE | pass | pass |
| FAIL-001 [sharepoint-rest] | Base tenant | Full sync while Engineering's SharePoint REST lists answer 503 | Same | F-FULLSYNC-PARTIAL-REMOVES, F-SUCCESS-ON-FAILURE | pass | pass |
| FAIL-001 [delta] | Base tenant | Full sync while one library's delta answers 503 | Same | F-FULLSYNC-PARTIAL-REMOVES, F-SUCCESS-ON-FAILURE | pass | pass |
| FAIL-002 site permission read fails | Base tenant | Sync while site role assignments answer 503 | The run is not a success; nobody loses access | F-SITE-ACL-FAILURE | pass | pass |
| FAIL-003 group member read fails | Base tenant; Finance group renamed so its members are read again | Sync while group member reads answer 503 | The run is not a success; nobody loses access | F-MEMBER-FAILURE-WIPES | pass | pass |
| FAIL-004 SharePoint REST throttling | Base tenant | Grant frank Read on the Finance site, then sync while SharePoint REST is rate limited (429 with Retry-After) | The run succeeds; frank gains Finance; nobody loses access | F-SPREST-NO-RETRY | pass | pass |
| FAIL-005 app without site access | Base tenant; the app lacks Sites.Read.All | Enable the connector | Enabling is refused and the connector stays off | F-CONNECTION-TEST | pass | pass |
| FAIL-006 app without Graph Sites.FullControl.All | Base tenant; the app has Sites.Read.All but not Sites.FullControl.All, so `GET /sites/root/permissions` answers 403 | Enable the connector | Enabling is refused with a message naming Sites.FullControl.All, and the connector stays off. Without that permission delta never reports user grants changed on items with their own permissions (seen on a live tenant) | | pass | pass |

## Notes from running the suite

**Fixed in the tests while running them (not product problems).**

- STR-001 first failed on Neo4j: `eng-design streamed HTTP 403`. The test streamed as the org admin, and
  the product refused, correctly: the connector's log says "User 6adb7602…fc7 does not have access to
  record …", and that id is the admin account, which has no SharePoint permission on the file. The test
  now streams as a tenant person who may read the item. It then passed on Neo4j and ArangoDB.
- A new connector has no sync status yet; the test waited for "IDLE" before the first sync and never
  started it. It now waits only while a sync is running.
- The tenant accounts are kept after a session: PipesHub refuses to add again an email whose account was
  deleted ("A user with this email already exists"), so deleting them broke the next session.

**Filters.** An earlier run found that a narrowed site or library filter left the excluded records in
the graph: the full sync never visited them, and the platform only unlinked them. #4507 deletes them,
and FLT-001 and FLT-004 now pass.

**Test environment notes.** Indexing sends a classification prompt to the LLM for every record. With a
CPU-only Ollama on a busy machine, that backlog made the LLM health check time out, so the session's
model set-up failed twice before any test ran. Registering the LLM once before the run, and starting from
an idle Ollama, avoided it. The ArangoDB compose file defaults the broker to Kafka; set
`MESSAGE_BROKER=redis` when running it without the kafka profile.

On upstream main 3b64393fc the integration-test harness cannot sign in on a local stack: it creates a
`client_credentials` OAuth app for the admin, and since #4538 such an app must act as a service account,
which is never an admin. That affects every suite, not only this one. The local run here used a small
pytest plugin, kept outside the repository, that signs `PipeshubClient` in with the admin's password
session instead.

## What is not covered

- **The audit scenarios that need code inside the PipesHub process.** A failed database write
  (F-STORE-FAILURE-CURSOR) and the upgrade from data the old code wrote. The audit harness covers both.
- **Things these tests cannot see through the API.** Log contents (F-SECRETS-IN-LOGS), notifications to the
  admin, and messages sent for indexing.
- **Not ported yet, but possible.** Pages that embed a restricted list or image (F-PAGE-EMBED-LEAK, through
  streaming), paging of every listing (F-PAGING-*), short-lived tokens, the trash setting, and the
  indexing filter.
- **Dropped connections.** The nginx front turns a dropped connection into a 502, so the harness's
  "connection reset" faults are not used.
- **Real Microsoft behaviour.** Everything is checked against the simulator. Where Microsoft's documents are
  unclear, the simulator follows one reading; the audit report lists what needs a live check.
- **Search and chat.** The check opens records by id. It does not search or ask questions.

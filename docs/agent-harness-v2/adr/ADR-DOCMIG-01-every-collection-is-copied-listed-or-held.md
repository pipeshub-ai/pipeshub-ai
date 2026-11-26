# ADR-DOCMIG-01: On a move to PostgreSQL every collection is copied, listed as not copied, or held

- Fixes R44SMALL-FU-3 (R45-MIGR, 2026-11-14). Amended by R45-CARRY (2026-11-14): decision 6, carriers.
- Amends: `backend/db/README.md` "No data move" (agent harness), and `docstore-migrate` preflight.
- Related: ADR-WEB-02 D9 (session families), ADR-SLACKID-01 D6 (Slack links), WP-114 (the harness stores on PostgreSQL).

## Context

`docstore-migrate` copies an install's MongoDB documents to PostgreSQL. Its coverage itest was red on main-sync: 54
harness and control-plane collections had neither a mapping nor a not-copied reason, the harness fields of chat
sessions and messages were not carried, and `slack_conversations.orgId` was a path no mapping knew. No gate saw it
because the test ran only where `PG_TEST_BINS` is set. Preflight had the same hole in code: a collection that the
application defines but no mapping covers was skipped (`MONGO_COLLECTIONS.includes(name)`), so a move would have
dropped the harness's runs, journals, audit chain, data keys and the control plane's policies without a word.

The README's premise, "the harness has not shipped, so a switch to `DOC_STORE=postgres` starts with empty harness
tables", stops being true the day the harness ships.

The harness stores on PostgreSQL are not the Mongo documents in another place, so a document-by-document copy is
not enough for them:

- claim-check payloads are named `mongo:<org>:<hash>` in the journal, the runs and the admission receipts, and the
  PostgreSQL payload store reads only `pg:` names. A copy that verified document against row would pass and leave
  every payload unreadable;
- most harness rows keep no Mongo `_id` (runs by run id, workspace heads by workspace, audit records by sequence), so
  a delete the follower sees names no row, and a rollback cannot restore the document's id;
- the audit chain (records, head, unlinked entries, anchors) is hashed and must move as one;
- the data keys open every sealed value; the chat-session, user and org coding-engine fields live in
  `harness.personal_choices` on PostgreSQL, a fan-in from three collections.

## Decision

1. **Three lists, one answer per collection.** Every collection the application stores is in exactly one of
   - `MAPPINGS`: copied, verified, followed and rolled back by the generic path;
   - `IGNORED`: not copied, with the reason, because the move makes it stale: process leases
     (`agentHarnessLeases`), heartbeats (`agentHarnessLiveness`), stream watch leases (`agentHarnessRunWatchers`),
     workspace write leases (`agentWorkspaceWriters`), the
     durability probe (`agentHarnessProbes`), legacy relay rows nothing reads (`agentDispatchOutbox`), refresh-token
     families (`session_families`, each person signs in once more);
   - `HELD` (new): its data must survive a move and the copy does not carry it yet. Every other harness and
     control-plane collection is held: durable runs and their journal, counters, payloads and queued work;
     idempotency receipts and admission quotas; fork records; sandbox reaps and snapshot index; the spend
     and cost ledgers; the audit chain and its checks; the data keys; sign-ins,
     memories, schedules, firings and counters, Inbox settings, finished-run notice claims, review candidates;
     workspaces and their keys; erasure and backfill jobs; and the control plane's heads, policy, history, ledgers,
     catalog, installations, grants and bindings. Each names the PostgreSQL tables its data lives in.
2. **Held means refused, never lossy.** Preflight is red (`held_collection`) while a held collection holds documents,
   with the reason and "stay on MongoDB until the copy carries it". A field of a mapped collection can be held the
   same way (`CollectionMapping.held`, check `held_field`): the coding-engine fields of `chatSessions`, `users` and
   `org`. A held path a document gains after preflight is removed from the
   verifier's source side and counted in the report (`held <path> (not copied)`), never passed silently.
3. **No exemption for a known collection.** Preflight no longer skips a collection because the application defines
   it: anything non-empty that is in none of the three lists is red (`unmapped_collection`).
4. **What has a column is copied.** The harness fields that are columns of main's tables are mapped: a session's
   `harnessVersion`, `harnessLedger`, `erasureRequestedAt`, `harnessSeen`; a message's `senderId`, `label` and the
   rest as the `harness` object (a root field with `spread`: its keys sit at the top of the document). The PostgreSQL
   conversation store kept neither `notFound` nor `limits` of a message (ANSWER-CONTRACT-FU-2, R32REPO-FU-1); it now
   keeps both, in normal operation as in the copy. `slack_conversations.orgId` is dropped with its reason: the row's
   organisation is that of the conversation, and the copy refuses a link whose `orgId` names another.
5. **The gap cannot come back unseen.** `tests/scripts/docstore-migrate/coverage.test.ts` runs in `npm test` with no
   database: the application's collections are its Mongoose models (schema files, plus models declared elsewhere,
   `harnessModel` included), the collections it names to the driver (a literal or a constant of the file), and the
   erasure inventory (`ORG_STORE_COVERAGE`, `UNMODELLED_COLLECTIONS`); each must be in exactly one list, and no list
   may name a collection the application does not store. Tables and columns are read from the checked-in surface
   snapshot (`backend/db/surface.snapshot.txt`, kept equal to the migrations by the db tests): every table of the
   document-store schemas, `harness` included, is reached by a mapping, named by a held or ignored collection, or
   listed with a reason (`coverage-lists.ts`), and every column of a mapped table is mapped or derived. The itest
   checks the same lists on a migrated PostgreSQL and runs in `scripts/verify.sh` and `scripts/qa_full.py` wherever
   the PostgreSQL test binaries are set.

6. **Carriers (R45-CARRY).** A store group whose PostgreSQL form is not the generic mapping's is carried by a carrier
   (`docstore-migrate/carry/`, interface `Carrier`): one unit that copies its collections and fields after the generic copy,
   applies the follower's events (writes and deletes), compares what each document calls for with what PostgreSQL holds, gives
   MongoDB back the same documents at a rollback (after the generic reverse, which rewrites a user, an org or a chat whole), and
   empties its tables at a reset. A collection or field a carrier carries is no longer held: `HELD` is what no carrier carries,
   so a group is un-held exactly when its carrier lands, and a fourth list, `CARRIED`, keeps the "exactly one list" rule.
   - **A key map** (`platform.docstore_keymap`, `platform_data_0008`, written and read by the import role only) records, in the
     unit that writes the rows, which row each document became when the table does not keep the MongoDB `_id`. The follower
     reads it to remove the row of a deleted document (an erasure during the follow does not survive the move), and a rollback
     gives every row its document's id back (a row made on PostgreSQL after the move gets a new id, or the one its store derives,
     and is recorded). The Slack thread links use it too (`CollectionMapping.keyed`): R45MIGR-FU-4.
   - **Compare by key, report by name.** A carrier's verify compares canonical text, so a field it does not place is a
     difference; a differing value is reported by its key and field names only, never its content (a wrapped key, a sealed
     manifest). A document a carrier cannot carry as it is (a BSON value the store does not keep, a missing key value) is refused
     with its id and path at preflight (`carry_refused`), never dropped or half-copied.
   - **The audit chain** is re-checked on both sides with `verifyAuditChain` (links, hashes, head, anchors); the two verdicts
     must be the same, so a valid chain stays valid and one already broken stays visibly broken.
   - **Pluggable, one edition seam.** An edition classifies and maps its collections in one object, `EDITION_DOCSTORE_COVERAGE`
     (src/edition.ts): `held`, `ignored` and `heldFields` classify (above), `carriers` map. The migrator checks each carrier's
     shape when it loads (`asCarrier`); a carrier may carry only held collections and fields, this build's or the edition's,
     none twice (`carrierProblems`), and an edition's held collection stays held until one of its carriers carries it.
   - **A row no org owns** (a sandbox reap, an erasure backfill plan, the control plane's deployment head: tables without
     `org_id`) is declared `deployment` and, when its table does not keep the `_id`, mapped in the key map's deployment-wide form
     (`platform.docstore_keymap_deployment`, `platform_data_0009`): the same entry without an organisation. The org-scoped map
     cannot hold it, because its `org_id` is checked as an org id and a made-up one would be an org the row policies know nothing of.
     (R46-CARRY.)
   - **Payload names are not rewritten** (R46-CARRY, amending the rewrite R45CARRY-FU-1 planned). A claim-check payload is named
     `<store>:<org>:<hash>` and its row is `<org>:<hash>` in both stores, so either document store reads a name of either form for
     its org (`payload-store/names.ts`, used by both stores and both session erasers). A rewrite could not reach every name: names
     sit inside sealed journal data and sealed payloads (opened only with the org's keys, which the copy never unwraps; the
     PostgreSQL eraser opens them to find content refs), and inside event data whose digest each audit record holds
     (`dataDigest`), so a renamed ref would no longer match the record a verifier cross-checks. The carrier copies the sealed bytes
     as they are, and its check resolves every name the carried documents show (claim-checks and `sealed:` content refs in the
     journal, runs, v1 events, queued runs and admission receipts) on both sides by that one rule, bytes checked against the hash in
     the name and the claim-check; the verdicts must be the same (a payload already missing on MongoDB stays visibly missing).
   - **The control plane's revisions** are carried exactly (both head tables, the deployment head included), and each org's policy
     and installation version chains are re-checked on both sides (current revision against its history, gaps, bodies); the two
     verdicts must be the same, as for the audit chain. An ordered-steps lease that recorded an intent (an unfinished write) is
     refused, never copied.
   - **The bridge window (R46-CARRY, R45CARRY-FU-5).** A rollback writes only the carried documents MongoDB does not already hold
     as PostgreSQL gives them, and removes those PostgreSQL no longer has. The reverse follower runs the same write-back at every
     sweep (`sweepMs`), so the rollback's freeze writes only what changed since the last sweep. `finalize --yes` ends the bridge
     window of a finished move: it removes the key map (both forms) and an online rollback is refused from then on (it would give
     every carried row a new id); it refuses while a job runs, before the flip, or when MongoDB is not retired.
   - **Carried now:** the coding-engine choices (`harness.personal_choices`), the data keys and workspaces (`data_keys`, `workspace_heads`, `workspace_manifests`,
     `workspace_keys`; keys never leave their wrapped form), the audit chain (`audit_records`, `audit_heads`, `audit_pending`,
     `audit_anchors`); since R46-CARRY the durable runs, journal and payloads (13 collections, carrier `durable-runs`), the ledgers
     (5, `ledgers`), people's data (8, `peoples-data`; sealed inputs and signing keys stay sealed), erasure jobs (6, `erasure`), and
     the control plane (10, `control-plane`). **Still held:** the five edition-written collections (R45CARRY-FU-4).

## As built

- Node: `backend/nodejs/apps/src/scripts/docstore-migrate/mapping/harness.ts` (`HARNESS_HELD`, `HARNESS_IGNORED`),
  `mapping/types.ts` (`HeldCollection`, `CollectionMapping.held`, `FieldMap.spread`), `mapping/index.ts` (`HELD`),
  `mapping/conversation.ts` and `mapping/identity.ts` (the mapped and held fields), `mapping/content.ts`
  (`slack_conversations.orgId`), `preflight.ts` (`heldFindings`, no exemption for a defined collection),
  `mongo-collections.ts` (`driverCollections`, `MIGRATOR_COLLECTIONS`), `decode.ts` and `census.ts` (spread),
  `source.ts` (held paths counted); `libs/repositories/conversation/pg-import.ts` and `pg-schema.ts`
  (`notFound`, `limits`), `libs/repositories/slack-thread/pg-import.ts`.
- Edition seam: `src/edition.ts` `EDITION_DOCSTORE_COVERAGE` (held and not-copied collections, held fields), joined in
  `mapping/index.ts` (`withEditionHeldFields`); an edition's held collection may name no table when it keeps the data in
  MongoDB only. Default: none.
- Gates: `scripts/verify.sh` ("node docstore-migrate itests"), `scripts/qa_full.py`.
- R45-CARRY: `carry/types.ts` (`Carrier`, `CarryMongo`), `carry/run.ts` (copy, follow events, verify, resync, restore, clear),
  `carry/doc-table.ts` (a declared row per document: key columns, typed columns, `doc`), `carry/keymap.ts`, `carry/choices.ts`,
  `carry/keys-workspaces.ts`, `carry/audit.ts`, `carry/index.ts` (`CARRIERS`, `EDITION_CARRIERS`, `asCarrier`, `carrierProblems`), `src/edition.ts` (`EDITION_DOCSTORE_COVERAGE.carriers`); `mapping/index.ts`
  (`OUTSIDE_GENERIC`, `CARRIED`, `HELD`, `splitOutside`, `fieldCarrier`); hooks in `bulk.ts`, `follow.ts` (events and resync),
  `verify.ts` (`VerifyReport.carried`), `reverse.ts`, `reverse-follow.ts` (inside the rollback's freeze), `reset.ts`,
  `preflight.ts` (`carriedFindings`, `platform_data` 6); `keyed-rows.ts`, `slack-threads.ts`, `mapping/content.ts`
  (`slack_conversations.orgId` as `org_id`, R45MIGR-FU-3); `backend/db/versions/platform_data/0008_carry_keymap.py` and `.sql`.
- R46-CARRY: `carry/durable-runs.ts` (`DURABLE_RUNS`, `payloadNameProblems`), `carry/ledgers.ts`, `carry/people.ts`,
  `carry/erasure.ts`, `carry/control-plane.ts` (`controlPlaneChainProblems`); `carry/doc-table.ts` (column kinds float, bool,
  bytes, date, datems, text lists; `orgOf`, `deployment`, `noRegion`, `derived`, `ordered`, `from`, `doc.idAs`; a rollback writes
  only what changed), `carry/keymap.ts` (the deployment-wide form), `reverse-follow.ts` (`writeBackCarried`, swept at
  `sweepMs`), `finalize.ts` and the `finalize` command (`cli.ts`), `preflight.ts` (`platform_data` 7);
  `libs/repositories/agent-harness/payload-store/names.ts` (`payloadRowId`); `backend/db/versions/platform_data/0009_carry_harness.py`
  and `.sql`.
- R46-CARRY tests: `carry-runs.itest.ts`, `carry-people.itest.ts`, `carry-control-plane.itest.ts` (PostgreSQL with MongoDB in
  memory, a scripted change stream through `follow()`, read back through the PostgreSQL stores), `carry-mongod.itest.ts` (the
  reverse follower's sweep, `finalize` and the refused online rollback, with a mongod), N `payload-names.test.ts`, the PS-9 case
  of the payload-store contract (`payload-store.pg.itest.ts`, `payload-store.mongo.itest.ts`), the R46-CARRY case of
  `coverage.test.ts`, `backend/db/tests/test_import_role.py` (the deployment-wide map, the import role's access, downgrade).
- R45-CARRY tests: N `carry-units.test.ts` and the R45-CARRY cases of `coverage.test.ts` (in `npm test`), `carry.itest.ts`
  (PostgreSQL with MongoDB in memory and a scripted change stream through `follow()`), `carry-mongod.itest.ts` (a whole move
  with a mongod, R45MIGR-FU-2), `content.itest.ts` (orgId), `backend/db/tests/test_import_role.py` (the key map's privileges and
  downgrade).
- Tests: N `tests/scripts/docstore-migrate/coverage.test.ts` (in `npm test`), `coverage-lists.ts`,
  `coverage.itest.ts`, `conversation.itest.ts` and `content.itest.ts` (the R45-MIGR cases),
  `tests/stores/contract/conversation.contract.ts` (`notFound`, `limits`).

## Deviations

- An install with harness or control-plane data cannot move to PostgreSQL with this release; preflight names each
  collection and why. Nothing is lost by trying.
- Carrying the held collections is R45MIGR-FU-1 (done for this build's collections since R46-CARRY: decision 6 lists what is
  carried); only the five edition-written collections stay held (R45CARRY-FU-4: their shape is the edition's). The payload-name
  rewrite R45CARRY-FU-1 planned was replaced by names both stores read (decision 6), because a rewrite could not reach the names
  inside sealed data and would break the audit records' data digests.
- Columns `chat_sessions.coding_engine`, `coding_engine_pin` and `repository` (conversation_0003) are written and read
  by nothing; they are listed as derived and stay NULL.
- The follow and rollback itests of the newly mapped fields need a mongod and have not run yet (R45MIGR-FU-2). The
  carriers' follow, delete and rollback logic runs on PostgreSQL with a scripted change stream (`carry.itest.ts`); the same move
  with a mongod (`carry-mongod.itest.ts`) joins R45MIGR-FU-2.
- The carriers have no change log: the reverse follower's sweep compares each carried group whole with MongoDB (it reads both
  sides, and writes only what changed), so a sweep and the rollback's freeze still read a large audit trail or journal in full
  (R46CARRY-FU-2). The whole move with a mongod (`carry-mongod.itest.ts`, the reverse sweep and `finalize` included) has not run
  yet: it needs a mongod (R45MIGR-FU-2).

## Owner amendments

None.

## Status

**Accepted** 2026-11-14 under the owner's direction of 2026-11-12 (first principles, long term, best UX).

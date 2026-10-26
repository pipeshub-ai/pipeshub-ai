# Repository seam

Controllers and services call repositories, not Mongoose models. This directory holds the shared pieces; each aggregate adds `<aggregate>/{types.ts, mongo.ts, index.ts}`.

- `uow.ts`: `UnitOfWork.run(fn)` and the opaque `Tx` handed to repository methods. Replaces `mongoose.startSession()` and the `rsAvailable` branches.
- `doc-store.ts`: `createUnitOfWork(rsAvailable)`, the one place the application's unit of work is built.
- `mongo.ts`: `MongoUnitOfWork(rsAvailable)` (transaction on a replica set, plain calls otherwise), `sessionOf(tx)` for Mongo repositories, `mapMongoError`.
- `errors.ts`: `UniqueConstraintError{key}`, `VersionConflictError`, and re-exports of the existing `ForeignKeyError`, `ConflictError`, `NotFoundError`. Repositories throw these; callers never test driver error codes.
- Rules: no Mongoose type crosses the seam.
- Ratchet: `tests/libs/repositories/mongo-seam-ratchet.test.ts` lists files that import `mongoose` or a `*.schema` module, and counts `.save()` / `new Model()`, outside `libs/repositories/**/mongo.ts`, `**/schema/**`, `libs/utils/mongo-filter.ts` and the boot migrations. The baseline may only shrink; re-record with `SEAM_RATCHET_UPDATE=1`.
- Contract tests: `tests/stores/contract/*.contract.ts` (store-neutral) run by `tests/stores/*.itest.ts` against a real server (`MONGO_IT_URI`, `REPLICA_SET_AVAILABLE`).

## Aggregates moved so far

- `conversation/`: chat and agent sessions, their messages and citations (`getConversationRepository()`). Methods are use cases, rows leave as plain objects with string ids. `listMessages` reads one session for one viewer; there is no multi-session message read. Every Mongo query the chat controllers ran lives in `conversation/mongo.ts`.
- `semantic-search/`: saved searches.
- `document/`: the metadata row behind every stored file (uploads, connector envelopes, assistant artifacts). Path-prefix lookups (`subtree`), connector scope (`pageByConnector`, `streamTaggedOutsidePrefixes`, `deleteByConnector`), virtual-record lookups and the keyed create (`createIdempotent`, which settles racing attempts and returns the first attempt's row) live in `document/mongo.ts`. `update` loads the row, applies a typed patch (and appended versions, optionally only while an upload lease is held) and saves it, so Mongoose writes only what changed.
- `notification/`: per-user notifications. The retention window and the deleted flag are part of every method; `list` returns `limit + 1` rows so the caller can see whether another page exists.
- `outbox/`: events decided with a change and delivered later. `claimNext` holds the claim rules (due, abandoned, not held back by an older event about the same entity); the model is in `outbox/schema/`.
- `mail-log/`: the audit record of each email sent.
- `plain.ts` (`toPlain`): turns a Mongoose document or lean row into the plain object a repository returns (ObjectId to string, nothing else changed). `ids.ts` (`isValidId`): id syntax check.
- Callers pass the org (and user) explicitly for now. The ambient org context is bound per request, but stream callbacks can run under another request's context on a reused connection, so a repository does not read it yet.
- Tests: `tests/stores/contract/*.contract.ts` run by `tests/stores/mongo-*.itest.ts`; controller unit tests run the real Mongo repository on the in-memory collections of `tests/modules/enterprise_search/controller/chat-test-harness.ts`.
- `oauth-app/`, `oauth-token/`, `identity-lookup/`: OAuth apps; access and refresh tokens (also personal and service tokens), authorization codes and device authorizations; and the lookups made before an org is known (`resolveToken`, `resolveRefresh`, `resolveClient`). Token, code and device methods are keyed by a hash, a code or a client id and take no org; a store that needs the org derives it from that key. Reading a live token by hash goes through `IdentityLookup`, which returns only the columns a vetted store function can supply.

## Remaining aggregates

- `org/`: the organisation record, its logo, and `createWithAdmin` (organisation, first administrator, "everyone" and "standard" groups and default sign-in config; pass the `tx` of a unit of work to make it atomic). The ids come first from `allocateIds()`, because the creation unit runs as the new organisation (`asAccount(ids.orgId, ids.adminUserId, …)`). The administrator's first password is stored in the same unit (`identity.person_first_password`, as that account). `liveSummary()` answers "how many live organisations, and which is the oldest" (the single-organisation checks).
- `project/`: project workspaces (`getLive`, `list` by scope, `update` with a typed patch, members, linked collection claim). Who may see or change a project stays in `ProjectService`.
- `connectors-config/`, `slack-thread/` (also owns the Slack bot's connection), `delegation/` (service-account allow-list and its event log).
- `org-metrics-job/`: cross-organisation reads for the periodic gauges; bound only in the metrics job.
- `slug/mongo.ts`: the per-name counter behind `generateUniqueSlug` (used by the schema hooks of organisations, users, groups and OAuth apps). The counter is not exposed; slugs come from the create methods.
- The connection service (`libs/services/mongo.service.ts`) is exempt from the ratchet, which is now empty: new Mongoose use outside the allowed places fails it.
- `ids.ts` (`isValidId`, `isNativeId`) and `mongo.ts` (`toDriverId`, for typed schema fields that still need a driver id) are the only id helpers non-repository code should use.

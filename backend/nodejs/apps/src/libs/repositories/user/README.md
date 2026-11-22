# Identity repositories

People, groups and sign-in state: `user/`, `group/`, `credential/`, `activity/`, `org-auth-config/`, `reset-link/`, `picture/` and `identity/` (the pre-organisation port). Each folder is `types.ts` (interface + DTOs), `mongo.ts` (today's queries, moved verbatim), `index.ts` (`getXRepository()` / `setXRepository(fake)`).

- Call the getter at call time, not at module load, so tests can swap a repository.
- DTOs are plain: ids are strings (`_id`, `orgId`, `userId`, group `users[]`), dates stay `Date`. A method that used to return a Mongoose document returns its `toObject()`; one that used `.lean()` returns the lean row. Responses therefore serialise exactly as before.
- Methods take the organisation id they act in. `IdentityPort` is the only place that looks across organisations or before one is known (`loginCandidates(email, orgId?)`, `memberships(email)`).
- Writes that can hit a unique key throw `UniqueConstraintError` (key names in `metadata.key`), never a driver error code.
- `credential/` is keyed by (user, organisation) because the Mongo store keeps one credential row per account. The same calls become the person-level identity functions when people and memberships are stored apart.
- `UserRepository.lockOrgAdminChanges` touches the organisation row inside a transaction to serialise last-admin changes; it moves to the organisation repository with the rest of `orgs`.
- Contract tests: `tests/stores/contract/identity.contract.ts`, run against a real server by `tests/stores/mongo-identity.itest.ts` (`MONGO_IT_URI`, `REPLICA_SET_AVAILABLE`).

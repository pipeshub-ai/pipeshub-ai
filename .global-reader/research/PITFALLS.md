# Edge Cases & Pitfalls

## Race Conditions

**Current State:** The codebase does NOT use MongoDB transactions for multi-document operations. Teams controller delegates to Python backend via HTTP calls.

**Key Risks:**
- **Concurrent user creation + team assignment:** Two requests could simultaneously try to add the same user to a team, creating duplicate membership entries
- **User deletion + team cleanup:** If a user is being deleted while being added to a team, membership state becomes undefined
- **Team deletion + active assignments:** Users could be added to a team that's being deleted simultaneously

**Existing Pattern:** Teams controller (teams.controller.ts:174-214) adds users without checking if they're already members
- No unique constraint on (teamId, userId) pairs
- No pre-check before adding users

**Prevention Strategy:**
- Implement idempotent operations with findOneAndUpdate patterns
- Use database-level unique indexes on permission edges
- Check membership before adding (get_edge query)

## Transaction Patterns

**Current State:**
- **Node.js backend:** NO transaction support observed
- **MongoDB:** No multi-document transactions in user_management module
- **Python/ArangoDB:** Supports transactions (TransactionStore class)

**Key Files:**
- `backend/nodejs/apps/src/libs/services/mongo.service.ts` - DocumentDB doesn't support transactions in namespaces
- `backend/python/app/connectors/core/base/data_store/arango_data_store.py` - Transaction-aware operations

**Pitfall:** Automatic team membership involves both MongoDB (users, orgs) AND ArangoDB (teams, permissions). Operations could split across two databases with no atomic guarantee.

**Prevention Strategy:**
- Accept eventual consistency
- Use compensating transactions for failures
- Log all operations for manual recovery if needed

## Error Handling - Non-Blocking Failures

**Existing Patterns:**

### Event Publishing (non-blocking)
```typescript
// EntityEventProducer catches errors and logs (entity_events.service.ts:114-121)
try {
  await this.eventService.publishEvent(...)
} catch (error) {
  this.logger.error('Failed to publish event', { error })
  // Does NOT throw - non-blocking
}
```

### Soft Deletes
Widely used (isDeleted flag, not hard deletes):
- Users schema: `isDeleted: { type: Boolean, default: false }`
- Orgs schema: `isDeleted: { type: Boolean, default: false }`
- UserGroups schema: `isDeleted: { type: Boolean, default: false }`

**Prevention Strategy for Global Reader:**
- Wrap team assignment in try-catch
- Log failures but don't propagate errors
- Never block login/registration for team assignment failures

## Idempotency

**Current State:** Minimal explicit idempotency handling.

### Existing Patterns

**1. Unique Constraints (users.schema.ts):**
```typescript
slug: { type: String, unique: true }
email: { type: String, required: true, lowercase: true, unique: true }
```

**2. Pre-check Pattern (userGroups.controller.ts:48-55):**
```typescript
const groupWithSameName = await UserGroups.findOne({ name, isDeleted: false });
if (groupWithSameName) {
  throw new BadRequestError('Group already exists');
}
```

**Missing for Teams:**
- No unique compound index on (teamId, userId)
- No idempotency key support
- No "createOrUpdate" / "getOrCreate" pattern for memberships

**Prevention Strategy:**
- Check existing membership before adding: `get_edge(userId, teamId)`
- Use upsert operations where possible
- Ensure multiple calls have same result (idempotent)

## Edge Cases to Handle

### Team Deletion
**Current Flow (teams.controller.ts:258-297):**
- Single HTTP DELETE to Python backend
- No cascading cleanup visible
- No removal of user memberships before team deletion
- No soft-delete flag for teams in Node.js side

**Dangers:**
1. Orphaned memberships
2. Stale permission edges
3. Partial deletions

**For Global Reader:**
- Check if team exists before adding user
- If team doesn't exist, recreate it (self-healing)
- Log warning but don't fail

### User Role Changes (Regular → Admin)
**Current State:** No handling for role transitions.

**Pitfall:** When user becomes admin, their Global Reader privilege should upgrade from READER to OWNER.

**Prevention Strategy:**
- Hook into admin group assignment
- When user added to admin group, check Global Reader membership
- Upgrade permission if exists, add with OWNER if not

### "Everyone" Group Pattern to Follow
```typescript
await UserGroups.updateOne(
  { orgId, type: 'everyone', isDeleted: false },
  { $addToSet: { users: newUser._id } },
);
```

This pattern:
- Uses `$addToSet` (idempotent - won't duplicate)
- Filters by type, not name (more reliable)
- Checks `isDeleted: false`

### Missing Org-Level Isolation
**Current Code (teams.controller.ts:34-35):**
```typescript
const orgId = req.user?.orgId;
const userId = req.user?.userId;
// But doesn't validate team.orgId === orgId
```

**For Global Reader:**
- Global Reader is system-wide (no orgId)
- OR create per-org Global Reader teams
- Decision needed based on PROJECT.md (system-wide chosen)

## Implementation Checklist

- [ ] Check membership exists before adding (idempotent)
- [ ] Wrap in try-catch (non-blocking)
- [ ] Log all operations (debugging)
- [ ] Handle team not existing (self-healing create)
- [ ] Check admin status before assigning privilege
- [ ] Never block login/registration on failure

## Key Files

| File | Relevant Pattern | Lines |
|------|------------------|-------|
| `user_management/controller/teams.controller.ts` | Team operations | 174-214, 258-297 |
| `user_management/routes/teams.routes.ts` | Validation schemas | - |
| `user_management/controller/users.controller.ts` | User patterns | - |
| `user_management/schema/userGroup.schema.ts` | Group model | 32 |
| `user_management/services/entity_events.service.ts` | Event publishing | 114-121 |
| `python/app/connectors/core/base/data_store/arango_data_store.py` | Transaction patterns | - |

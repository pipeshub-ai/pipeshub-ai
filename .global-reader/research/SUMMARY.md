# Research Summary: Automatic Global Reader Team

**Domain:** Team Membership Automation
**Researched:** 2026-02-04
**Overall confidence:** HIGH

## Executive Summary

This research analyzed the PipesHub codebase to understand how to implement automatic team membership for a "Global Reader" team. The existing infrastructure fully supports this feature — no new libraries or schema changes needed.

**Key Finding:** The codebase already has a pattern for automatic group membership (the "everyone" UserGroup). Teams use a different storage mechanism (ArangoDB PERMISSION edges) but the same hook points apply.

**Implementation Complexity:** LOW - Hook into existing user creation flows, reuse team membership APIs.

**Dependencies:** ZERO - All required services and patterns already exist.

## Key Findings

### Stack
- **Teams:** Stored in ArangoDB `teams` collection with UUID keys
- **Memberships:** Stored as PERMISSION edges in ArangoDB (`_from: users/{id}`, `_to: teams/{id}`)
- **Privileges:** READER, WRITER, OWNER defined in `PermissionType` enum
- **Admin Detection:** Via UserGroup membership where `type === 'admin'`

### Architecture - Hook Points
Three viable hook points identified:

1. **PRIMARY:** Post-JIT provisioning in `userAccount.controller.ts` (lines 1541-1546)
   - Catches all SSO first-time logins
   - User already has org context

2. **SECONDARY:** First login check in `userAccount.controller.ts` (lines 1698-1710)
   - Catches pre-existing users on first login
   - Good for backward compatibility

3. **TERTIARY:** Event-based via Kafka `NewUserEvent`
   - Decoupled, async
   - Won't block auth flow

### Features - Existing Patterns
- **Add user to team:** `POST /api/v1/entity/team/{team_id}/users` with `userRoles` array
- **Check membership:** `get_edge(userId, teamId)` in ArangoDB
- **Team lookup by name:** `GET /api/v1/entity/team/list` with search
- **"Everyone" group pattern:** `$addToSet` for idempotent membership

### Pitfalls to Avoid
1. **Race conditions:** No unique constraint on memberships - use check-before-add
2. **No transactions:** MongoDB/ArangoDB split - accept eventual consistency
3. **Blocking failures:** Must wrap in try-catch, never block login
4. **Team deletion:** Self-healing - recreate if missing

## Implications for Roadmap

### Phase 1: Global Reader Team Service
Create a service to manage the Global Reader team:
- `ensureGlobalReaderTeamExists()` - Idempotent team creation
- `addUserToGlobalReader(userId, isAdmin)` - Add with appropriate privilege
- `isUserInGlobalReader(userId)` - Check membership

### Phase 2: Hook Integration
Add hooks in user creation flows:
- JIT provisioning service (new users via SSO)
- User controller createUser (admin-created users)
- Authentication controller (retroactive for existing users)

### Phase 3: Testing & Edge Cases
- Test concurrent user creation
- Test admin privilege upgrade
- Test team recreation if deleted
- Verify non-blocking behavior

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Team Schema | HIGH | Verified in documents.py |
| Membership Pattern | HIGH | PERMISSION edges documented |
| Admin Detection | HIGH | UserGroup type='admin' pattern confirmed |
| Hook Points | HIGH | Multiple options identified with line numbers |
| Existing APIs | HIGH | Full CRUD available for teams |
| Idempotency | MEDIUM | Need to implement check-before-add |

## Gaps to Address

**None blocking.** All infrastructure exists.

**Implementation decisions needed:**
1. Where to create the Global Reader team (startup script vs on-demand)?
2. Who is the "creator" of the Global Reader team (system user)?
3. Should there be a special marker for system teams?

## Key Technical Details

### Team Creation Pattern
```typescript
// Create team in Python backend via POST /api/v1/entity/team
{
  "name": "Global Reader",
  "description": "System team - all users are automatically members",
  "userRoles": []  // Empty initially, users added separately
}
```

### Membership Addition Pattern
```typescript
// Add user to team via POST /api/v1/entity/team/{team_id}/users
{
  "userRoles": [{
    "userId": "<user_id>",
    "role": "READER"  // or "OWNER" for admins
  }]
}
```

### Admin Check Pattern
```typescript
const groups = await UserGroups.find({
  orgId,
  users: { $in: [userId] },
  isDeleted: false
});
const isAdmin = groups.some(group => group.type === 'admin');
```

### Idempotent Membership Pattern
```typescript
// Check if already member before adding
const existingMembership = await arangoService.get_edge(
  `users/${userId}`,
  `teams/${globalReaderTeamId}`,
  'PERMISSION'
);
if (!existingMembership) {
  // Add to team
}
```

## Ready for Requirements

Research is complete. All information needed for implementation is documented.

**Next step:** Define detailed requirements with the patterns identified.

**Risk level:** LOW (using existing patterns, no schema changes)

**Dependencies:** None (all services exist)

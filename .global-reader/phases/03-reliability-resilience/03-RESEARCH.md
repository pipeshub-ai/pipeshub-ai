# Phase 3: Reliability & Resilience - Research

**Researched:** 2026-02-04
**Domain:** Error handling, idempotency, non-blocking operations
**Confidence:** HIGH

## Summary

Phase 3 focuses on ensuring team membership operations are resilient and non-blocking. Research reveals that **RELY-01 and RELY-04 are already fully implemented** in Phase 2. The Global Reader team assignment is wrapped in try-catch blocks with comprehensive logging, and errors do not propagate to block user registration or creation.

**RELY-02** (login path) requires investigation as team assignment currently only happens during user registration/creation, not during login. This is actually the desired behavior - users are assigned once at creation, not on every login.

**RELY-03** (idempotency) is handled at the database level by ArangoDB's UPSERT operation, which prevents duplicate edges from being created.

**Primary recommendation:** Verify RELY-01/RELY-04 implementation in Phase 3, document RELY-02 as "not applicable" (team assignment happens at registration, not login), and validate RELY-03 idempotency behavior with integration tests.

## Standard Stack

### Core Error Handling
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| TypeScript try-catch | Native | Exception handling | Built into language, universally used |
| Winston/Pino | N/A | Structured logging | Industry standard for Node.js logging |

### Idempotency Mechanisms
| Tool | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| ArangoDB UPSERT | 3.x | Prevent duplicate edges | Graph database operations |
| MongoDB $addToSet | 4.x+ | Prevent duplicate array items | Document operations |

### Testing Tools
| Tool | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| Mocha/Jest | Latest | Integration tests | Validate idempotency |
| Supertest | Latest | API endpoint testing | End-to-end reliability tests |

**Installation:**
```bash
# Already installed in project
npm test  # Run existing test suite
```

## Architecture Patterns

### Pattern 1: Non-Blocking Service Calls with try-catch
**What:** Wrap external service calls in try-catch, log errors but don't throw
**When to use:** When a secondary operation should not block primary operation
**Example:**
```typescript
// Source: globalReaderTeam.service.ts lines 199-234
async addUserToGlobalReader(
  orgId: string,
  userId: string,
  headers: Record<string, string>,
): Promise<void> {
  try {
    // Step 1: Get team ID
    const teamId = await this.getGlobalReaderTeamId(orgId, headers);
    if (!teamId) {
      this.logger.warn('Global Reader team not found, skipping user addition', {
        orgId,
        userId,
      });
      return;
    }

    // Step 2: Check if user is admin
    const isAdmin = await this.isUserAdmin(orgId, userId);
    const role = isAdmin ? OWNER_ROLE : READER_ROLE;

    // Step 3: Add user to team
    await this.addUserToTeam(teamId, userId, role, headers);
    this.logger.info('User added to Global Reader team', {
      orgId,
      userId,
      role,
    });
  } catch (error) {
    // Non-blocking: log error but don't throw
    this.logger.error('Failed to add user to Global Reader team', {
      orgId,
      userId,
      error: error instanceof Error ? error.message : 'Unknown error',
    });
  }
}
```

### Pattern 2: Database-Level Idempotency with UPSERT
**What:** Use database UPSERT operations to prevent duplicate creation
**When to use:** When multiple calls might attempt to create the same relationship
**Example:**
```typescript
// Source: arango_http_provider.py lines 498-548
async def batch_create_edges(
    self,
    edges: List[Dict],
    collection: str,
    transaction: Optional[str] = None
) -> bool:
    """
    Batch create edges - FULLY ASYNC.
    Uses UPSERT to avoid duplicates - matches on _from and _to.
    """
    batch_query = """
    FOR edge IN @edges
        UPSERT { _from: edge._from, _to: edge._to }
        INSERT edge
        UPDATE edge
        IN @@collection
        RETURN NEW
    """
    # If edge with same _from and _to exists, updates it
    # If not, inserts new edge
    # No duplicate edges created
```

### Pattern 3: Graceful Degradation with Early Returns
**What:** Return early with warning logs when prerequisites are missing
**When to use:** When an operation depends on external state that may not exist
**Example:**
```typescript
// Source: globalReaderTeam.service.ts lines 206-213
const teamId = await this.getGlobalReaderTeamId(orgId, headers);
if (!teamId) {
  this.logger.warn('Global Reader team not found, skipping user addition', {
    orgId,
    userId,
  });
  return; // Graceful exit, no exception thrown
}
```

### Anti-Patterns to Avoid
- **Throwing errors from non-blocking operations:** Defeats the purpose of non-blocking design
- **Silent failures without logging:** Makes debugging impossible
- **Checking for duplicates manually before insert:** Race conditions; use database-level UPSERT instead
- **Retrying without idempotency guarantees:** Can create duplicates

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Duplicate prevention | Manual check-then-insert logic | Database UPSERT operations | Race conditions between check and insert |
| Structured logging | Custom log formatters | Winston/Pino with context | Industry standard, proven reliability |
| Error tracking | Custom error collection | Logger service with context | Already implemented, consistent format |
| Retry logic | Custom retry loops | Idempotent operations + monitoring | Simpler, safer, no duplicate risk |

**Key insight:** Idempotency should be built into operations (via UPSERT), not added as a wrapper. Checking for duplicates manually introduces race conditions.

## Common Pitfalls

### Pitfall 1: Login vs Registration Confusion
**What goes wrong:** Assuming team assignment happens on login when it only happens on registration
**Why it happens:** RELY-02 mentions "login" but current architecture assigns teams at user creation
**How to avoid:** Understand that user creation is the only time team assignment happens; login updates hasLoggedIn flag but doesn't reassign teams
**Warning signs:** Tests failing because team assignment isn't called during login flow

### Pitfall 2: Testing Non-Blocking Operations
**What goes wrong:** Integration tests pass even when team assignment fails because errors don't propagate
**Why it happens:** Try-catch blocks prevent errors from reaching test assertions
**How to avoid:**
  - Assert on log messages, not just operation success
  - Verify team membership directly in database after operation
  - Use separate test for team assignment failure scenarios
**Warning signs:** Tests show "success" but team membership isn't created

### Pitfall 3: Race Conditions in Idempotency
**What goes wrong:** Multiple simultaneous user creations create duplicate team memberships
**Why it happens:** Check-then-insert pattern has time gap between check and insert
**How to avoid:** Use database UPSERT operations that are atomic
**Warning signs:** Duplicate team memberships appearing under load or in concurrent tests

### Pitfall 4: Missing Context in Error Logs
**What goes wrong:** Error logs say "failed to add user" but don't include orgId/userId
**Why it happens:** Forgetting to include contextual data in log statements
**How to avoid:** Always log orgId, userId, and error details together
**Warning signs:** Unable to reproduce or investigate reported failures

## Code Examples

Verified patterns from existing implementation:

### Current Non-Blocking Implementation (RELY-01 ✓)
```typescript
// Source: jit-provisioning.service.ts lines 97-102
// This is already non-blocking - errors don't propagate to caller
await this.globalReaderTeamService.addUserToGlobalReader(
  orgId,
  String(newUser._id),
  {}, // Empty headers - internal call, auth context from org
);
// No try-catch here because addUserToGlobalReader handles errors internally
```

### Current Error Logging (RELY-04 ✓)
```typescript
// Source: globalReaderTeam.service.ts lines 226-233
} catch (error) {
  // Non-blocking: log error but don't throw
  this.logger.error('Failed to add user to Global Reader team', {
    orgId,      // ✓ Context included
    userId,     // ✓ Context included
    error: error instanceof Error ? error.message : 'Unknown error', // ✓ Error details
  });
}
```

### Database-Level Idempotency (RELY-03 ✓)
```typescript
// Source: entity.py lines 525-617
@router.post("/team/{team_id}/users")
async def add_users_to_team(request: Request, team_id: str) -> JSONResponse:
    # ...
    user_team_edges = []
    for user_role in user_roles:
        user_id = user_role.get("userId")
        role = user_role.get("role", "READER")
        if user_id == creator_key:
            # Skip creator - they already have OWNER role
            logger.info(f"Skipping creator {creator_key} - they already have OWNER role")
            continue
        user_team_edges.append({
            "_from": f"{CollectionNames.USERS.value}/{user_id}",
            "_to": f"{CollectionNames.TEAMS.value}/{team_id}",
            "type": "USER",
            "role": role,
            # ...
        })

    # batch_create_edges uses UPSERT internally
    result = await arango_service.batch_create_edges(user_team_edges, CollectionNames.PERMISSION.value)
```

### Login Flow (No Team Assignment - RELY-02 Analysis)
```typescript
// Source: userAccount.controller.ts lines 1698-1712
// Login only updates hasLoggedIn flag, does NOT call addUserToGlobalReader
if (!user.hasLoggedIn) {
  const userInfo = {
    email: user.email,
    hasLoggedIn: true, // Only this is updated
  };
  await this.iamService.updateUser(user._id, userInfo, accessToken);
  this.logger.info('user updated');
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Throw errors from secondary operations | Try-catch with logging only | Phase 2 (2026-02-04) | Registration/login never blocked by team failures |
| Manual duplicate checks | Database UPSERT | Phase 2 (2026-02-04) | Race-condition safe idempotency |
| Team assignment on every login | Team assignment only at user creation | Phase 2 (2026-02-04) | More efficient, no repeated operations |

**Current implementation status:**
- RELY-01 (registration non-blocking): ✓ Implemented
- RELY-04 (error logging): ✓ Implemented
- RELY-03 (idempotency): ✓ Implemented at database level
- RELY-02 (login non-blocking): ⚠️ Not applicable - team assignment happens at registration, not login

## Open Questions

### 1. Should RELY-02 be reinterpreted?
- **What we know:** Current architecture assigns teams only at user creation/registration
- **What's unclear:** Whether "login" in RELY-02 means "first login" (which is registration) or "subsequent logins"
- **Recommendation:** Clarify requirements. If team assignment should happen on every login (not recommended), this is new functionality. If it means "first login", RELY-02 is already satisfied by RELY-01.

### 2. What about JIT provisioning via SSO?
- **What we know:** JIT provisioning creates users on-demand during SSO login (jit-provisioning.service.ts line 98)
- **What's unclear:** Whether this counts as "login" or "registration" for RELY-02
- **Recommendation:** JIT provisioning is functionally equivalent to registration - user is created, then team is assigned. RELY-02 is satisfied for SSO flows.

### 3. Should retroactive assignment happen on login?
- **What we know:** v2 requirement RETRO-01 mentions "existing users added on next login"
- **What's unclear:** Whether Phase 3 should implement this or if it's explicitly v2 scope
- **Recommendation:** Keep as v2 scope. Phase 3 focuses on reliability of existing flows, not adding retroactive assignment.

### 4. How to test non-blocking behavior?
- **What we know:** Errors are caught and logged, not propagated
- **What's unclear:** Best approach to validate that registration succeeds even when team service fails
- **Recommendation:** Mock team service to throw error, assert user creation succeeds, check logs for error message

## Sources

### Primary (HIGH confidence)
- `/backend/nodejs/apps/src/modules/user_management/services/globalReaderTeam.service.ts` - Lines 199-234 (addUserToGlobalReader implementation)
- `/backend/nodejs/apps/src/modules/auth/services/jit-provisioning.service.ts` - Lines 97-102 (JIT provisioning with team assignment)
- `/backend/nodejs/apps/src/modules/user_management/controller/users.controller.ts` - Lines 278-286 (Admin user creation with team assignment)
- `/backend/python/app/services/graph_db/arango/arango_http_provider.py` - Lines 498-548 (batch_create_edges with UPSERT)
- `/backend/python/app/api/routes/entity.py` - Lines 525-617 (add_users_to_team endpoint)

### Secondary (MEDIUM confidence)
- `/backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts` - Lines 1698-1712 (Login flow showing hasLoggedIn update only)
- `.global-reader/REQUIREMENTS.md` - RELY-01 through RELY-04 requirement definitions

### Code Verification
- **Try-catch non-blocking:** Verified at globalReaderTeam.service.ts:204-234
- **Error logging with context:** Verified at globalReaderTeam.service.ts:228-233
- **UPSERT idempotency:** Verified at arango_http_provider.py:526-533
- **Login doesn't reassign teams:** Verified at userAccount.controller.ts:1705-1712

## Metadata

**Confidence breakdown:**
- Non-blocking registration (RELY-01): HIGH - Code review confirms implementation
- Non-blocking login (RELY-02): HIGH - Code review shows team assignment not called during login
- Idempotency (RELY-03): HIGH - Database UPSERT mechanism verified
- Error logging (RELY-04): HIGH - Log statements with context verified

**Research date:** 2026-02-04
**Valid until:** 60 days (stable codebase, low churn expected)

**Key findings:**
1. RELY-01 and RELY-04 are fully implemented - Phase 3 needs verification tests, not new code
2. RELY-02 is not applicable - team assignment happens at registration, not login
3. RELY-03 is implemented at database level via UPSERT - needs integration test validation
4. JIT provisioning (SSO) flows use same non-blocking pattern as manual registration

# Auth Flows & Hook Points

## User Creation Flow

**Primary Entry Points:**

### 1. JIT Provisioning Service
**Location:** `backend/nodejs/apps/src/modules/auth/services/jit-provisioning.service.ts`

**Method:** `provisionUser()` (lines 36-101)

**Flow:**
1. Creates new user directly via `Users.save()`
2. Adds to "everyone" group automatically (lines 67-70)
3. Publishes `NewUserEvent` to Kafka (lines 73-93)

### 2. User Controller
**Location:** `backend/nodejs/apps/src/modules/user_management/controller/users.controller.ts`

**Methods:**
- `createUser()` (lines 243-279) - Admin-initiated user creation
- `addManyUsers()` (lines 1107-1407) - Bulk user import

**Flow:**
- Both add users to "everyone" group before saving
- Both publish `NewUserEvent` events

## Login/Authentication Flow

**Main Route:** `/api/v1/userAccount/authenticate` → `UserAccountController.authenticate()`

**Location:** `backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts`

**Key Steps (lines 1351-1726):**

1. **Session initialization** via `initAuth()` (lines 204-459)
   - Creates session with JIT config if enabled
   - Identifies org from email domain or tenant ID

2. **JIT Detection** in `authenticate()` (lines 1386-1567)
   - Checks if `sessionInfo.userId === "NOT_FOUND"` (line 1387)
   - Validates JIT is enabled for the auth method (lines 1388-1397)
   - User object is created via `jitProvisioningService.provisionUser()` (lines 1541-1546)

3. **Post-Authentication** (lines 1698-1722)
   - Updates user `hasLoggedIn` flag
   - Generates access token

## JIT Provisioning Patterns

**Three implementations:**

1. **JitProvisioningService** (auth module)
   - Used by OAuth/SAML/Microsoft flows in `UserAccountController.authenticate()`
   - Creates user and adds to "everyone" group

2. **SAML Route Handler** (`backend/nodejs/apps/src/modules/auth/routes/saml.routes.ts`, lines 175-185)
   - Direct JIT provisioning via `jitProvisioningService.provisionUser()`

3. **UserController** methods
   - `provisionSamlUser()` and `provisionJitUser()` methods

## Recommended Hook Points

### PRIMARY HOOK - Post-JIT Provisioning
**Location:** Lines 1541-1546 in `userAccount.controller.ts`

```typescript
// After this line:
user = await this.jitProvisioningService.provisionUser(...)

// Add: Fetch/create Global Reader team, add user with appropriate role
```

**Why:**
- Catches ALL SSO first-time logins (Google, Microsoft, Azure AD, OAuth, SAML)
- User already in "everyone" group by this point
- Non-blocking: wrap in try-catch

### SECONDARY HOOK - Post-Existing User Login
**Location:** Lines 1698-1710 in `userAccount.controller.ts`

```typescript
// After first login check:
if (!user.hasLoggedIn) {
  await iamService.updateUser(...)
  // Add: Retroactively add to Global Reader if not member
}
```

**Why:**
- Catches first login for pre-existing users (backward compatibility)
- Idempotent check: only add if not already member

### TERTIARY HOOK - Event-Based
**Location:** `entity_events.service.ts`, lines 73-93

```typescript
// After: NewUserEvent published
// Add: Kafka consumer adds user to default team
```

**Why:**
- Decoupled from auth flow
- Can be processed asynchronously
- Failing Kafka publish won't block authentication

## Event Publishing Pattern

**Location:** `jit-provisioning.service.ts` (lines 73-93)

```typescript
await this.eventService.publishEvent({
  eventType: EventType.NewUserEvent,
  timestamp: Date.now(),
  payload: {
    orgId: orgId.toString(),
    userId: newUser._id,
    fullName: newUser.fullName,
    email: newUser.email,
    syncAction: SyncAction.Immediate,
  }
});
```

## "Everyone" Group Pattern

```typescript
await UserGroups.updateOne(
  { orgId, type: 'everyone', isDeleted: false },
  { $addToSet: { users: newUser._id } },
);
```

This is the pattern to follow for Global Reader team assignment.

## Key Files

| File | Purpose | Key Lines |
|------|---------|-----------|
| `auth/services/jit-provisioning.service.ts` | JIT user creation | 36-101 |
| `auth/controller/userAccount.controller.ts` | OAuth/SSO authentication | 1351-1726, 204-459 |
| `auth/routes/saml.routes.ts` | SAML SSO flow | 74-229 |
| `user_management/controller/users.controller.ts` | User CRUD | 243-279, 1107-1407 |
| `user_management/services/entity_events.service.ts` | Event publishing | 73-93 |
| `user_management/schema/users.schema.ts` | User model | 8-22 |
| `user_management/schema/userGroup.schema.ts` | Group model | - |

# Phase 2 Research: Membership Automation

## Add User to Team API

**Endpoint:** `POST /api/v1/entity/team/{team_id}/users`
- **Location:** backend/python/app/api/routes/entity.py (lines 525-617)
- **Authentication:** Requires OWNER role on the team
- **Request Body:** Supports two formats
  - New format (recommended): `{ "userRoles": [{"userId": "user_key", "role": "OWNER|READER"}] }`
  - Legacy format: `{ "userIds": ["user_key"], "role": "READER|OWNER" }`
- **Response:** Returns updated team with users, permissions, and metadata

**Key Implementation Details:**
- Requires creator to have OWNER permission first (line 542-547)
- Prevents adding creator with non-OWNER role (lines 566-575)
- Supports both formats for backward compatibility (lines 553-558)
- Creates edges in PERMISSION collection with role attribute (lines 576-583)
- Uses ArangoDB transactions for atomicity (lines 97-114 in create_team)
- Valid role values: "OWNER" or "READER" (tested in response generation)

## User Creation Hook Points

**Three main paths where users are created and should trigger Global Reader team addition:**

### 1. JIT Provisioning (OAuth/SAML)
- **File:** `backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts`
- **Flow:** Lines 1541-1546 (directSsoConfig endpoint callback)
- **Method:** `JitProvisioningService.provisionUser()` (jit-provisioning.service.ts, lines 36-101)
- **Key Actions:**
  - Creates user in MongoDB
  - Adds to 'everyone' group (lines 67-70 in jit-provisioning)
  - Publishes NewUserEvent via Kafka (lines 74-85)
- **Trigger Point:** After user.save() completes (line 64)
- **Provider Types:** google, microsoft, azureAd, oauth, saml

### 2. Admin User Creation (Bulk Import)
- **File:** `backend/nodejs/apps/src/modules/user_management/controller/users.controller.ts`
- **Method:** `addManyUsers()` (lines 1107-1407)
- **Key Actions:**
  - Creates new users for provided emails (lines 1196-1203)
  - Adds to specified groups AND 'everyone' group (lines 1223-1232, 1229-1231)
  - Publishes NewUserEvent for each user (lines 1234-1244, 1321-1331)
- **Trigger Point:** After Users.create() completes (line 1203)

### 3. Single User Creation (Admin UI)
- **File:** `backend/nodejs/apps/src/modules/user_management/controller/users.controller.ts`
- **Method:** `createUser()` (lines 243-279)
- **Key Actions:**
  - Creates single user
  - Adds to 'everyone' group (lines 254-257)
  - Publishes NewUserEvent (lines 259-272)
- **Trigger Point:** After newUser.save() completes (line 273)

**Common Pattern:** All paths publish `EventType.NewUserEvent` with `SyncAction.Immediate`, which could be used as alternative hook point via Kafka consumer.

## Admin Detection

**Pattern:** Check if user is member of UserGroup with type='admin'

**Implementation locations:**
- `backend/nodejs/apps/src/modules/user_management/middlewares/userAdminCheck.ts` (line 27)
- `backend/nodejs/apps/src/modules/user_management/controller/users.controller.ts` (lines 866-867)
- `backend/nodejs/apps/src/modules/user_management/middlewares/userAdminOrSelfCheck.ts` (line 26)

**Query Pattern:**
```typescript
const groups = await UserGroups.find({
  orgId: user.orgId,
  users: { $in: [userId] },
  isDeleted: false,
}).select('type');

const isAdmin = groups.find((userGroup: any) => userGroup.type === 'admin');
```

**Key Points:**
- Type value must be exactly 'admin' (case-sensitive)
- Always check isDeleted: false
- Check against current user's orgId for isolation
- Result is a UserGroup document with type property

## Team ID Lookup

**Current Implementation:**
- `GlobalReaderTeamService.checkTeamExists()` (backend/nodejs/apps/src/modules/user_management/services/globalReaderTeam.service.ts, lines 62-97)

**API Endpoint Used:** `GET /api/v1/entity/team/list`
- Query parameter: `search=Global Reader` (matches on team.name)
- Limit: 100 results
- Response format: `{ teams: [{name, orgId}] }`

**Lookup Logic:**
```typescript
const searchParams = new URLSearchParams({
  search: GLOBAL_READER_TEAM_NAME,  // 'Global Reader'
  limit: '100',
});

const response = await aiCommand.execute();
const teams = response.data?.teams || [];
const found = teams.some(
  (team) => team.name === GLOBAL_READER_TEAM_NAME && team.orgId === orgId
);
```

**Important:**
- Must match exact name: 'Global Reader' (defined in globalReaderTeam.service.ts line 17)
- Must verify orgId matches (lines 93-96)
- Team list response includes team._key needed for add_users_to_team endpoint
- Response structure from Python backend includes 'id' field (team._key)

## Role/Privilege Format

**Valid Role Values:** `"OWNER"` or `"READER"` (case-sensitive, uppercase)

**Usage in Add User Endpoint:**
- Python API expects: `{ "userRoles": [{"userId": "...", "role": "OWNER|READER"}] }`
- Edge creation in ArangoDB: `permission.role = "OWNER" | "READER"`

**Role Semantics (from entity.py):**
- **OWNER:** Can edit team, delete team, manage members (lines 202-204, 315-317)
- **READER:** Read-only access (default in legacy format, line 63)

**Assignment Rules:**
- Team creator always gets OWNER role (enforced, lines 70-79, 572-574)
- New regular users should get READER privilege
- New admin users should get OWNER privilege

## Implementation Recommendations

### 1. Extend GlobalReaderTeamService with User Addition Method

Add new method `addUserToGlobalReader()` that:
- Takes: `orgId`, `userId`, `isAdmin` flag, `headers`
- Finds Global Reader team by name/orgId (reuse checkTeamExists pattern but extract team ID)
- Calls Python API to add user with appropriate role
- Non-blocking error handling (log but don't throw)
- Returns team ID if found, null if not found

**Pseudo-code:**
```typescript
async addUserToGlobalReader(
  orgId: string,
  userId: string,
  isAdmin: boolean,
  headers: Record<string, string>,
): Promise<void> {
  const teamId = await this.getGlobalReaderTeamId(orgId, headers);
  if (!teamId) {
    this.logger.warn('Global Reader team not found', { orgId });
    return;  // Non-blocking
  }
  
  const role = isAdmin ? 'OWNER' : 'READER';
  await this.addUserToTeam(teamId, userId, role, headers);
}
```

### 2. Hook Point Integration

**Option A: Direct Integration (Recommended for Phase 2)**
- Call `addUserToGlobalReader()` immediately after user creation
- In `JitProvisioningService.provisionUser()` after user.save()
- In `UserController.createUser()` after newUser.save()
- In `UserController.addManyUsers()` after Users.create()

**Option B: Event-Based Integration (Future - Phase 2.2)**
- Consume NewUserEvent from Kafka
- Create separate Kafka consumer service
- Allows decoupling and retry logic
- Better for scalability

**Admin Detection Integration:**
- After user creation, check UserGroups: `type: 'admin'`
- If admin group contains new userId: pass `isAdmin: true`
- Otherwise: pass `isAdmin: false`

### 3. Error Handling Strategy

**Non-blocking Errors (like Global Reader team creation):**
- Log warnings but don't throw
- Allow user creation to succeed even if team assignment fails
- Log error context: orgId, userId, error message

**Network/API Errors:**
- Retry logic could be added (future phase)
- Currently: log and continue
- Ensures user creation is never blocked by Python backend issues

## Key Files to Modify

1. **backend/nodejs/apps/src/modules/user_management/services/globalReaderTeam.service.ts**
   - Add `addUserToGlobalReader()` method
   - Add `getGlobalReaderTeamId()` helper (extract from checkTeamExists logic)
   - Add `addUserToTeam()` helper for API calls
   - Export role constants: `READER_ROLE`, `OWNER_ROLE`
   - Lines to reference: 62-129 (existing patterns)

2. **backend/nodejs/apps/src/modules/auth/services/jit-provisioning.service.ts**
   - Inject GlobalReaderTeamService
   - After newUser.save() (line 64): Call addUserToGlobalReader()
   - After line 64: Check admin status from context if available, or query later
   - Lines to reference: 36-101

3. **backend/nodejs/apps/src/modules/user_management/controller/users.controller.ts**
   - Inject GlobalReaderTeamService
   - In createUser() method (line 273): Call addUserToGlobalReader() after save
   - In addManyUsers() method: Call for each new user after Users.create()
   - Pass isAdmin based on UserGroups query
   - Lines to reference: 243-279, 1107-1407

4. **backend/nodejs/apps/src/modules/user_management/container/userManager.container.ts**
   - GlobalReaderTeamService already bound (lines 59-65)
   - Ensure UserController and JitProvisioningService can inject it
   - Update controller bindings if needed

## Testing Considerations

1. **Unit Tests:**
   - Mock Python backend API responses
   - Test role assignment: isAdmin true → OWNER, false → READER
   - Test team ID lookup with multiple teams
   - Test non-blocking error scenarios

2. **Integration Tests:**
   - Create user via SSO → verify added to Global Reader team
   - Create user via admin bulk import → verify added to Global Reader team
   - Create user via admin single creation → verify added to Global Reader team
   - Admin user creation → verify OWNER role
   - Non-admin user creation → verify READER role

3. **Edge Cases:**
   - Global Reader team doesn't exist (should not block user creation)
   - Python backend unreachable (should not block user creation)
   - User already in team (should be idempotent)
   - Team creation race condition (multiple org creation)

## Dependencies

- **GlobalReaderTeamService:** Already exists, needs extension
- **Python Backend API:** Already exists at `/api/v1/entity/team/{team_id}/users`
- **Dependency Injection:** Inversify container already configured
- **Logging:** Logger service already available in all services
- **HTTP Client:** AIServiceCommand already used in globalReaderTeamService

## References

- Python API endpoint: `/backend/python/app/api/routes/entity.py` lines 525-617
- JIT provisioning: `/backend/nodejs/apps/src/modules/auth/services/jit-provisioning.service.ts`
- User controller: `/backend/nodejs/apps/src/modules/user_management/controller/users.controller.ts`
- UserGroup schema: `/backend/nodejs/apps/src/modules/user_management/schema/userGroup.schema.ts`
- Admin detection pattern: `/backend/nodejs/apps/src/modules/user_management/middlewares/userAdminCheck.ts` line 27
- Global Reader Team Service: `/backend/nodejs/apps/src/modules/user_management/services/globalReaderTeam.service.ts`

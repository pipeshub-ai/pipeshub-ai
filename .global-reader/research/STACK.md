# Team & Membership Models

## Team Schema

**Location:** `backend/python/app/schema/arango/documents.py` (lines 629-649)

**Storage:** ArangoDB collection named `teams`

**Key Fields:**
- `_key`: UUID identifier
- `name`: Team name (required, string, min 1 char)
- `description`: Optional description
- `orgId`: Organization ID (nullable)
- `createdBy`: User key of team creator
- `updatedByUserId`: Last updated by user
- `deletedByUserId`: Deleted by user (soft delete)
- `createdAtTimestamp`: Epoch timestamp in milliseconds
- `updatedAtTimestamp`: Epoch timestamp in milliseconds
- `deletedAtTimestamp`: Epoch timestamp in milliseconds
- `isDeleted`: Boolean flag (default: false)

## Membership Schema

**Storage Pattern:** PERMISSION edges in ArangoDB

**Edge Structure:**
- `_from`: User node reference (e.g., `users/{userId}`)
- `_to`: Team node reference (e.g., `teams/{teamId}`)
- `type`: "USER" (identifies entity type)
- `role`: Permission role (OWNER, WRITER, READER)
- `createdAtTimestamp`: When membership created
- `updatedAtTimestamp`: When membership last updated

**Collection:** `PERMISSION` collection in ArangoDB

**Hierarchy:** Creator automatically gets OWNER role, other members assigned specific roles

## Privilege Definitions

**Location:** `backend/python/app/models/permission.py` (lines 9-14)

**PermissionType Enum:**
- `READ` = "READER" - Read-only access
- `WRITE` = "WRITER" - Write/edit access
- `OWNER` = "OWNER" - Full control, can manage members, delete team
- `COMMENT` = "COMMENTER" - Comment-only access
- `OTHER` = "OTHERS" - Other permissions

**Permission Hierarchy:**
- OWNER: Can create, edit, delete, manage members
- WRITER: Can edit team and its content
- READER: Read-only access
- Team owner cannot be removed from team

## Admin Identification

**Mechanism:** User Group Membership

**Function Location:** `backend/nodejs/apps/src/modules/tokens_manager/controllers/connector.controllers.ts` (lines 100-115)

**Implementation:**
```typescript
const groups = await UserGroups.find({
  orgId,
  users: { $in: [userId] },
  isDeleted: false
})
const isAdmin = groups.find(group => group.type === 'admin')
```

**Admin Identification:** User is admin if they belong to a UserGroup with `type === 'admin'`

**User Group Types:** 'admin', 'standard', 'everyone', 'custom' (defined in `backend/nodejs/apps/src/modules/user_management/schema/userGroup.schema.ts` line 4)

## Existing Patterns

- **Default Behavior:** No system or default teams found
- **Team Creation Pattern:** Creator automatically becomes OWNER
- **"Everyone" Group:** Users are auto-added to "everyone" UserGroup on creation - similar pattern to follow
- **Membership Modification:**
  - Add users with specified roles (READER, WRITER, OWNER)
  - Remove users (except owner)
  - Update individual user roles (batch operations)

## Key Files

| File | Purpose | Lines |
|------|---------|-------|
| `backend/python/app/schema/arango/documents.py` | Team schema | 629-649 |
| `backend/python/app/models/permission.py` | Permission enum | 1-70 |
| `backend/python/app/api/routes/entity.py` | Team API endpoints | 28-1050+ |
| `backend/nodejs/apps/src/modules/user_management/controller/teams.controller.ts` | Team controller | 1-555 |
| `backend/nodejs/apps/src/modules/user_management/routes/teams.routes.ts` | Route definitions | 1-305 |
| `backend/nodejs/apps/src/modules/tokens_manager/controllers/connector.controllers.ts` | Admin check | 100-115 |
| `backend/nodejs/apps/src/modules/user_management/middlewares/userAdminCheck.ts` | Admin middleware | - |
| `backend/nodejs/apps/src/modules/user_management/schema/userGroup.schema.ts` | User group schema | - |

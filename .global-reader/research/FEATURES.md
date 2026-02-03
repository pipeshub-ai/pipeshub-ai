# Membership Operations

## Adding Users to Teams

**Node.js Backend:** `TeamsController.addUsersToTeam()` (lines 174-214 in teams.controller.ts)

**Python Backend:** `add_users_to_team()` (lines 525-617 in entity.py)

**API Endpoint:** `POST /api/v1/entity/team/{team_id}/users`

### Parameters

**New Format (Recommended):**
```json
{
  "userRoles": [
    {
      "userId": "<user_id>",
      "role": "<READER|WRITER|OWNER>"
    }
  ]
}
```

**Legacy Format (Backward Compatible):**
```json
{
  "userIds": ["<user_id1>", "<user_id2>"],
  "role": "<READER|WRITER|OWNER>"
}
```

### Process Flow
1. Request validation ensures user has OWNER role on the team (lines 541-547)
2. User IDs are converted to ArangoDB permission edges (lines 563-583)
3. Edges are created in batches using `batch_create_edges()` (line 598)
4. Creator is automatically skipped to preserve their OWNER role (lines 572-575)
5. Returns updated team with all members and their roles

## Setting Privileges

**Node.js Backend:** `TeamsController.updateTeamUsersPermissions()` (lines 449-489)

**Python Backend:** `update_user_permissions()` (lines 704-815)

**API Endpoint:** `PUT /api/v1/entity/team/{team_id}/users/permissions`

### Parameters
```json
{
  "userRoles": [
    {
      "userId": "<user_id>",
      "role": "<READER|WRITER|OWNER>"
    }
  ]
}
```

### Process Flow
1. Verify caller has OWNER role (lines 720-730)
2. Prevent changing team owner's role (lines 746-749)
3. Batch update all user roles using AQL query (lines 765-782)
4. Update `updatedAtTimestamp` for audit trail
5. Return updated team with new permissions

### Database Storage
- Roles stored in PERMISSION collection edges
- Edge format: `_from: "users/{userId}"`, `_to: "teams/{teamId}"`, `role: "OWNER|READER|WRITER"`
- Timestamps tracked for creation and updates

## Checking Membership

**Method:** Uses ArangoDB `get_edge()` method in arango service

### Query Pattern
```python
permission = await arango_service.get_edge(
    f"{CollectionNames.USERS.value}/{user['_key']}",
    f"{CollectionNames.TEAMS.value}/{team_id}",
    CollectionNames.PERMISSION.value
)
```

### Returns
- Permission edge document if user exists in team
- `None` if user is not a member

## Team Lookup

### By Team ID
`GET /api/v1/entity/team/{team_id}` - Returns team details with member info

### By User
- `GET /api/v1/entity/user/teams` - Lists all teams current user is member of
- `GET /api/v1/entity/user/teams/created` - Lists all teams created by current user

### List All Teams
`GET /api/v1/entity/team/list` - Lists all teams in user's organization
- Supports pagination (page, limit) and search by name

### Search Teams
`GET /api/v1/entity/team/search` - Fuzzy search by name or description

## Team Creation

**Node.js Backend:** `TeamsController.createTeam()` (lines 22-66)

**Python Backend:** `create_team()` (lines 28-133)

**API Endpoint:** `POST /api/v1/entity/team`

### Parameters
```json
{
  "name": "<team_name>",
  "description": "<optional_description>",
  "userRoles": [
    {
      "userId": "<user_id>",
      "role": "<READER|WRITER|OWNER>"
    }
  ]
}
```

### Process Flow
1. Verify user exists and has orgId (lines 39-45)
2. Generate unique UUID for team key (line 47)
3. Create team node in TEAMS collection with metadata (lines 49-57)
4. Create PERMISSION edges for all initial users (lines 67-95)
   - Creator automatically gets OWNER role
   - Other users get specified roles
5. Use database transaction to ensure atomicity (lines 98-114)
6. Return team with all members and their roles

### Required Fields
- `name`: String, required, minimum 1 character
- `description`: String, optional
- `userRoles` or `userIds`: Array of users with roles

## Key Files

| File | Purpose | Lines |
|------|---------|-------|
| `backend/nodejs/apps/src/modules/user_management/controller/teams.controller.ts` | Team operations | 1-555 |
| `backend/nodejs/apps/src/modules/user_management/routes/teams.routes.ts` | Route definitions | 1-305 |
| `backend/python/app/api/routes/entity.py` | Entity API endpoints | 1-1609 |
| `backend/python/app/config/constants/arangodb.py` | Database collections | 105-142 |
| `backend/python/app/models/permission.py` | Permission definitions | 1-70 |
| `backend/python/app/services/graph_db/arango/arango.py` | ArangoDB service | 1-400+ |

# Phase 1 Research: Team Foundation

## Application Bootstrap

**Where the app starts:** `backend/nodejs/apps/src/index.ts`

**Key lifecycle points:**
- Lines 50-60: IIFE that calls `preInitMigration()` → `initialize()` → `start()` → `runMigration()`
- `initialize()` (app.ts, lines 93-242): Sets up all containers and middleware
- `runMigration()` (app.ts, lines 490-505): Runs migrations after server starts

**Hook point for global team creation:**
1. Add to `runMigration()` after line 495 (after AI models migration)
2. Create new method called after line 54 in index.ts (between start() and runMigration())

**Key constraint:** Need access to `UserManagerContainer` to get services and AppConfig.

## Service Patterns

**DI Framework:** Inversify (`@injectable`, `@inject` decorators)

**Service structure example** (auth.service.ts):
```typescript
@injectable()
export class AuthService {
  constructor(
    @inject('AppConfig') private authConfig: AppConfig,
    @inject('Logger') private logger: Logger,
  ) {}

  async someMethod(): Promise<Result> {
    // Implementation
  }
}
```

**Container binding pattern** (userManager.container.ts, lines 90-92):
```typescript
container.bind<TeamsController>('TeamsController').toDynamicValue(() => {
  return new TeamsController(appConfig, container.get('Logger'));
});
```

## HTTP Client to Python Backend

**Client class:** `AIServiceCommand` (ai.service.command.ts)

**Usage pattern from TeamsController.createTeam()** (teams.controller.ts, lines 22-66):
```typescript
const aiCommandOptions: AICommandOptions = {
  uri: `${this.config.connectorBackend}/api/v1/entity/team`,
  method: HttpMethod.POST,
  headers: {
    ...(req.headers as Record<string, string>),
    'Content-Type': 'application/json',
  },
  body: req.body,
};
const aiCommand = new AIServiceCommand(aiCommandOptions);
const aiResponse = await aiCommand.execute();
```

**Key details:**
- `connectorBackend` URL from AppConfig (tokens_manager/config/config.ts, line 19)
- Spreads request headers (passes through auth/session tokens)
- Response format: `{ statusCode, data, msg }`
- Includes retry logic with exponential backoff (3 retries, 300ms initial)

## System Context

**How orgId is obtained:**

From authenticated request (teams.controller.ts, line 34):
```typescript
const orgId = req.user?.orgId;  // From JWT/auth middleware
```

**System-managed entity pattern** (org.controller.ts, lines 206-225):

When creating org, "everyone" UserGroup is automatically created:
```typescript
const allUsersGroup = new UserGroups({
  type: 'everyone',  // System type
  name: 'everyone',
  orgId: org._id,
  users: [adminUser._id],
});
```

**For Global Reader team:**
- Need orgId from current authenticated user OR
- Query for orgs and create team for each org
- Follow same pattern: mark as system-managed

## Team Lookup API

**Check if team exists:** `GET /api/v1/entity/team/list` with search parameter

**Endpoint:** `GET ${connectorBackend}/api/v1/entity/team/list?search=Global%20Reader`

**Query parameters:**
- `search` (optional): Team name search string
- `page` (optional): Page number, default 1
- `limit` (optional): Items per page, default 10, max 100

**Response structure:**
```json
{
  "status": "success",
  "message": "Teams fetched successfully",
  "teams": [
    {
      "id": "team_key",
      "name": "Global Reader",
      "description": "...",
      "createdBy": "user_key",
      "orgId": "org_id",
      "memberCount": 5
    }
  ],
  "pagination": { "page": 1, "limit": 10, "total": 1 }
}
```

**Idempotency check:** `response.teams.find(t => t.name === "Global Reader")`

## Implementation Recommendations

### Service Structure

**Create:** `backend/nodejs/apps/src/modules/user_management/services/globalReaderTeam.service.ts`

**Pattern:**
```typescript
@injectable()
export class GlobalReaderTeamService {
  constructor(
    @inject('AppConfig') private config: AppConfig,
    @inject('Logger') private logger: Logger,
  ) {}

  async ensureGlobalReaderTeamExists(orgId: string, headers: Record<string, string>): Promise<void> {
    // 1. Search for existing team by name "Global Reader"
    // 2. If exists and orgId matches, return early (idempotent)
    // 3. If not exists, create via POST /api/v1/entity/team
    // 4. Log success/failure
  }
}
```

### Startup Integration

**Recommended approach:** Hook into org creation flow rather than app startup

Since teams require orgId and user context, the cleanest integration is:
1. When org is created (org.controller.ts), also create Global Reader team
2. For existing orgs, add migration or lazy creation on first user login

**Alternative:** Create `ensureSystemTeams()` called from `runMigration()` that iterates all orgs.

### Headers & User Context

**Challenge:** Creating system-level team without authenticated user request

**Solutions:**
1. Hook into org creation (has admin context)
2. Hook into first admin login per org
3. Migration script with service account

### Error Handling

- Check `statusCode !== 200` after execute()
- Log team creation success/failure with orgId
- Don't fail app startup if team creation fails - log warning and continue

## Key Files

| File | Purpose | Key Lines |
|------|---------|-----------|
| `backend/nodejs/apps/src/index.ts` | Entry point, lifecycle | 50-60 |
| `backend/nodejs/apps/src/app.ts` | Application class | 93-242, 490-505 |
| `backend/nodejs/apps/src/modules/user_management/container/userManager.container.ts` | DI container | 20-42, 80-92 |
| `backend/nodejs/apps/src/modules/user_management/controller/teams.controller.ts` | Team operations | 22-66, 109-172 |
| `backend/nodejs/apps/src/libs/commands/ai_service/ai.service.command.ts` | HTTP client | 1-140 |
| `backend/python/app/api/routes/entity.py` | Python team API | 28-133, 135-277 |
| `backend/nodejs/apps/src/modules/user_management/controller/org.controller.ts` | Org creation pattern | 206-225 |

## Critical Implementation Details

### Idempotency Check
- **When:** On every attempt to create Global Reader team
- **How:** Call `GET /api/v1/entity/team/list?search=Global%20Reader&limit=100`
- **Match:** Find team with exact name "Global Reader" for the org

### Best Hook Point
- **Recommended:** Org creation flow in `org.controller.ts`
- **Why:** Already has admin context, orgId, and headers
- **Pattern:** Same as "everyone" UserGroup creation

### Error Handling
- Python backend uses ArangoDB transactions (entity.py line 99-114)
- Node.js doesn't need transaction - single HTTP call
- Retry logic built into AIServiceCommand (3 attempts)
- Never fail parent operation if team creation fails

# Architecture: Microsoft SSO Tenant ID Matching

**Domain:** Multi-tenant Microsoft SSO authentication with JIT provisioning
**Researched:** 2026-01-31
**Confidence:** HIGH

## Current Architecture

### Authentication Flow Components

```
┌─────────────────┐
│  User enters    │
│  email          │
└────────┬────────┘
         │
         v
┌─────────────────────────────────────────┐
│ userAccount.controller.ts               │
│ initAuth() - lines 204-437              │
│                                         │
│ 1. Extract domain from email           │
│ 2. Find Org by domain field            │
│ 3. Find OrgAuthConfig by orgId          │
│ 4. Create session with auth methods     │
└────────┬────────────────────────────────┘
         │
         v
┌─────────────────────────────────────────┐
│ Frontend: User clicks SSO button        │
└────────┬────────────────────────────────┘
         │
         v
┌─────────────────────────────────────────┐
│ userAccount.controller.ts               │
│ authenticate() - lines 1329-1663        │
│                                         │
│ If userId === "NOT_FOUND":              │
│   - Validate Microsoft credentials      │
│   - Extract user details from token    │
│   - Call JIT provisioning               │
└────────┬────────────────────────────────┘
         │
         v
┌─────────────────────────────────────────┐
│ jit-provisioning.service.ts             │
│ provisionUser() - lines 36-101          │
│                                         │
│ 1. Check for deleted user               │
│ 2. Create new User record               │
│ 3. Add to everyone group                │
│ 4. Publish user creation event          │
└─────────────────────────────────────────┘
```

### Data Storage Architecture

**Organization identification:**
```
Org schema (org.schema.ts)
├── _id: ObjectId
├── domain: String (single domain only)
├── registeredName: String
└── contactEmail: String

OrgAuthConfig schema (orgAuthConfiguration.schema.ts)
├── _id: ObjectId
├── orgId: ObjectId (references Org)
├── domain: String (optional, currently unused)
└── authSteps: IAuthStep[]
    └── allowedMethods: IAuthMethod[]
        └── type: 'microsoft' | 'azureAd' | ...
```

**Microsoft configuration storage:**
```
Configuration Manager (key-value store)
Path: /services/auth/microsoft
Scope: Per-organization (via JWT with orgId)

Stored structure (encrypted):
{
  clientId: string,
  tenantId: string,
  authority: string,
  enableJit: boolean
}
```

### Current Lookup Flow

**Step 1: Initial auth (initAuth)**
```typescript
// userAccount.controller.ts:217-222
const domain = this.getDomainFromEmail(email);  // Extract domain
let org = domain ? await Org.findOne({
  domain,       // Match on single domain field
  isDeleted: false,
}) : null;
```

**Step 2: Configuration fetch (per-org scoped)**
```typescript
// userAccount.controller.ts:244-276
// Create user context with orgId
const newUser = { orgId: orgAuthConfig.orgId, email };

// Fetch Microsoft config using org-scoped JWT
const configManagerResponse = await this.configurationManagerService.getConfig(
  this.config.cmBackend,
  MICROSOFT_AUTH_CONFIG_PATH,  // '/services/auth/microsoft'
  newUser,  // Contains orgId
  this.config.scopedJwtSecret,
);

// Response contains: { clientId, tenantId, authority, enableJit }
```

**Step 3: Token validation**
```typescript
// azureAdTokenValidation.ts:13-56
export const validateAzureAdUser = async (
  credentials: Record<string, any>,
  tenantId: string,  // From configuration
): Promise<any | null> => {
  // Validate token against Microsoft's JWKS endpoint
  // Ensures token is from correct tenant
}
```

## Problem Analysis

### Current Limitation

The architecture has an **architectural mismatch**:

1. **Org lookup uses email domain** (initAuth line 219)
   - `Org.findOne({ domain: "aktor.ai" })`
   - Single domain per org

2. **Auth validation uses tenant ID** (authenticate line 1422)
   - Microsoft config stored per-org with tenantId
   - Token validation checks tenant ID

3. **Gap:** No way to look up org by tenant ID when email domain doesn't match

**Example failure case:**
- Org has `domain: "aktor.ai"`
- Microsoft config has `tenantId: "abc-123"`
- User email: `user@biosar.gr` (different domain, same tenant)
- Result: Domain lookup fails → No org found → JIT provisioning doesn't attempt

### Configuration Scoping

**Key insight:** Microsoft configuration is **org-scoped**, not global.

```typescript
// cm.service.ts:26-43
async getConfig(
  cmBackendUrl: string,
  configUrlPath: string,
  user: Record<string, any>,  // Contains userId and orgId
  scopedJwtSecret: string,
): Promise<ConfigManagerResponse> {
  // JWT generated with orgId embedded
  const token = fetchConfigJwtGenerator(user._id, user.orgId, scopedJwtSecret);

  // Request to /services/auth/microsoft
  // Key-value store uses orgId from JWT to scope lookup
}
```

**Storage structure:**
```
Key-Value Store (Redis/etcd)
├── /services/auth/microsoft:{orgId1} → { clientId, tenantId, ... }
├── /services/auth/microsoft:{orgId2} → { clientId, tenantId, ... }
└── /services/auth/microsoft:{orgId3} → { clientId, tenantId, ... }
```

This means:
- **We cannot query "find org by tenantId" at the config layer**
- Config is retrieved AFTER org is identified
- Org identification must happen at MongoDB layer (Org/OrgAuthConfig)

## Recommended Architecture

### Component Changes

**Required modifications:**

| Component | Current Role | New Role | Change Type |
|-----------|-------------|----------|-------------|
| `userAccount.controller.ts` | Lookup org by email domain | Lookup org by tenantId from token | MODIFY |
| `OrgAuthConfig` schema | Store authSteps only | Add `microsoftTenantId` field | EXTEND |
| `jit-provisioning.service.ts` | Provision user for known org | No change needed | UNCHANGED |
| `azureAdTokenValidation.ts` | Validate token against tenantId | Extract tenantId from token | EXTEND |
| Configuration Manager | Store Microsoft config | No change needed | UNCHANGED |

### New Data Flow

**Revised lookup sequence:**

```
1. User enters email
   └─> initAuth extracts domain (for backward compatibility)

2. Frontend shows Microsoft SSO button
   └─> User authenticates with Microsoft

3. Microsoft returns token
   └─> authenticate() receives credentials with idToken

4. Extract tenant ID from token
   └─> azureAdTokenValidation decodes token
   └─> Get tid claim without full validation

5. Look up org by tenant ID
   └─> OrgAuthConfig.findOne({ microsoftTenantId: tid })
   └─> Get orgId from matched config

6. Fetch Microsoft config using orgId
   └─> configurationManagerService.getConfig(newUser)
   └─> Full token validation

7. JIT provision user
   └─> jitProvisioningService.provisionUser(email, details, orgId)
```

### Data Model Changes

**OrgAuthConfig schema extension:**

```typescript
// orgAuthConfiguration.schema.ts
interface IOrgAuthConfig extends Document {
  orgId: Types.ObjectId;
  domain?: string;  // Keep for backward compatibility
  microsoftTenantId?: string;  // NEW: Microsoft Entra ID tenant ID
  authSteps: IAuthStep[];
  isDeleted?: boolean;
  createdAt?: Date;
  updatedAt?: Date;
}

const OrgAuthConfigSchema = new Schema<IOrgAuthConfig>(
  {
    orgId: { type: Schema.Types.ObjectId, required: true },
    domain: { type: String },
    microsoftTenantId: {
      type: String,
      index: true,  // Enable efficient lookup
      sparse: true,  // Only index non-null values
    },
    authSteps: { /* ... */ },
    isDeleted: { type: Boolean, default: false },
  },
  { timestamps: true }
);

// Compound index for efficient lookups
OrgAuthConfigSchema.index({ microsoftTenantId: 1, isDeleted: 1 });
```

**Why OrgAuthConfig, not Org?**

1. **Separation of concerns:** Auth configuration is separate from org identity
2. **Multiple auth methods:** Org might have Google SSO (no tenantId) + Microsoft SSO (has tenantId)
3. **Existing pattern:** Configuration Manager already stores provider configs separately
4. **Migration safety:** Doesn't modify core Org schema

### Token Extraction Strategy

**Two-phase validation:**

```typescript
// Phase 1: Extract tenant ID (before org lookup)
async extractTenantIdFromToken(idToken: string): Promise<string | null> {
  const decoded = jwt.decode(idToken, { complete: true });
  if (!decoded || !decoded.payload) return null;

  const payload = decoded.payload as JwtPayload;
  return payload.tid || payload.tenant_id || null;
}

// Phase 2: Full validation (after org lookup, before JIT)
async validateAzureAdUser(credentials, tenantId): Promise<any> {
  // Existing validation logic
  // Fetch JWKS, verify signature, check claims
}
```

**Security:** Phase 1 is safe because:
- Only extracts claim, doesn't trust it yet
- Full validation happens in Phase 2 with JWKS verification
- If tenantId is forged, validation fails before JIT

## Implementation Order

**Suggested sequence for milestone phases:**

### Phase 1: Schema Migration
**Goal:** Add microsoftTenantId field without breaking existing flow

**Changes:**
1. Add `microsoftTenantId?: string` to OrgAuthConfig schema
2. Add database index for efficient lookup
3. Run migration to backfill existing orgs with tenantId from config

**Why first:**
- Non-breaking change
- Enables subsequent phases
- Can be tested independently

**Validation:**
- Existing auth flows continue working
- New field populated for orgs with Microsoft SSO configured

---

### Phase 2: Token Extraction
**Goal:** Extract tenant ID from Microsoft token before org lookup

**Changes:**
1. Add `extractTenantIdFromToken()` helper to azureAdTokenValidation.ts
2. Modify authenticate() to extract tenantId before validation
3. Add logging to track extraction success/failure

**Why second:**
- Minimal risk (only adds extraction, doesn't change lookup)
- Provides visibility into tenant IDs in logs
- Can verify extraction works before changing lookup logic

**Validation:**
- Log shows tenant IDs extracted from tokens
- Existing validation still passes

---

### Phase 3: Org Lookup by Tenant ID
**Goal:** Match org by tenant ID instead of email domain for Microsoft SSO

**Changes:**
1. Modify initAuth() to store "microsoft-pending" state in session for unknown users
2. Modify authenticate() to look up org by tenant ID when userId === "NOT_FOUND"
3. Update JIT provisioning to use matched orgId

**Why third:**
- Depends on schema (Phase 1) and extraction (Phase 2)
- Core behavior change, needs careful testing
- Backward compatibility: falls back to domain if tenantId not found

**Validation:**
- Multi-domain users can authenticate
- Single-domain users still work
- Logs show tenant ID lookup success

---

### Phase 4: Backfill & Cleanup
**Goal:** Populate tenantId for existing orgs, remove fallback code

**Changes:**
1. Script to sync microsoftTenantId from Configuration Manager to OrgAuthConfig
2. Remove domain-based fallback logic (if desired)
3. Add monitoring for tenant ID match failures

**Why last:**
- Ensures all existing orgs have tenantId populated
- Can remove temporary fallback code
- Production-ready state

**Validation:**
- All Microsoft-enabled orgs have tenantId
- Zero failed lookups in logs

## Architecture Patterns

### Pattern 1: Two-Phase Validation

**What:** Split token processing into extraction (untrusted) and validation (trusted).

**When:** Need data from token to determine which org to validate against.

**Implementation:**
```typescript
// Phase 1: Extract (no trust)
const tenantId = extractTenantIdFromToken(idToken);

// Phase 2: Lookup org
const orgAuthConfig = await OrgAuthConfig.findOne({
  microsoftTenantId: tenantId
});

// Phase 3: Validate (trust after verification)
const validatedToken = await validateAzureAdUser(credentials, tenantId);
```

**Security consideration:** Tenant ID is user-supplied until Phase 3. Never use it for authorization until after JWKS validation.

---

### Pattern 2: Org Lookup Fallback Chain

**What:** Try multiple lookup strategies in order of preference.

**When:** Migrating from domain-based to tenant-based matching.

**Implementation:**
```typescript
async findOrgForMicrosoftAuth(email: string, tenantId?: string) {
  // Strategy 1: Tenant ID (preferred for multi-domain)
  if (tenantId) {
    const config = await OrgAuthConfig.findOne({
      microsoftTenantId: tenantId,
      isDeleted: false,
    });
    if (config) return Org.findById(config.orgId);
  }

  // Strategy 2: Email domain (backward compatibility)
  const domain = getDomainFromEmail(email);
  if (domain) {
    return Org.findOne({ domain, isDeleted: false });
  }

  return null;
}
```

**Why useful:** Graceful migration without breaking existing users.

---

### Pattern 3: Session State for Deferred Lookup

**What:** Store "unknown user, pending SSO" state in session.

**When:** Need to defer org lookup until after SSO provider authentication.

**Current implementation (initAuth):**
```typescript
// When user not found
const session = await this.sessionService.createSession({
  userId: "NOT_FOUND",  // Special marker
  email: email,
  orgId: orgAuthConfig ? orgAuthConfig.orgId.toString() : "",
  authConfig: orgAuthConfig && jitEnabledMethods.length > 0
    ? orgAuthConfig.authSteps
    : defaultAuthSteps,
  currentStep: 0,
  jitConfig: jitEnabledMethods.length > 0 ? jitConfig : undefined,
});
```

**Enhancement for tenant ID:**
```typescript
const session = await this.sessionService.createSession({
  userId: "NOT_FOUND",
  email: email,
  orgId: "",  // Unknown until SSO
  pendingTenantIdLookup: true,  // NEW: flag for tenant-based lookup
  authConfig: [{
    order: 1,
    allowedMethods: [{ type: 'microsoft' }]
  }],
  currentStep: 0,
  jitConfig: { microsoft: true },
});
```

**Why needed:** Email domain lookup may fail, but tenant ID lookup happens later (after Microsoft auth).

## Anti-Patterns to Avoid

### Anti-Pattern 1: Storing Tenant ID in Org Schema

**What:** Adding `microsoftTenantId` field to Org schema instead of OrgAuthConfig.

**Why bad:**
- Mixes identity (Org) with authentication method (Microsoft SSO)
- Org might have multiple auth methods (Google, SAML, Microsoft)
- Hard to extend for other providers (e.g., Azure AD vs Microsoft 365 SSO)
- Migration risk for core schema

**Instead:** Use OrgAuthConfig, which already manages auth provider configs.

---

### Anti-Pattern 2: Global Tenant ID Registry

**What:** Creating separate TenantMapping collection: `{ tenantId -> orgId }`.

**Why bad:**
- Duplicates data already in Configuration Manager
- Extra table to maintain
- Sync issues between config and mapping
- Violates DRY principle

**Instead:** Add tenantId to OrgAuthConfig, which already maps auth configs to orgs.

---

### Anti-Pattern 3: Full Token Validation Before Org Lookup

**What:** Calling `validateAzureAdUser()` before knowing which org to validate against.

**Why bad:**
- Need tenantId from config to validate token
- Need token to get tenantId for lookup
- Circular dependency
- Can't validate signature without knowing which JWKS endpoint

**Instead:** Two-phase validation (extract, lookup, validate).

---

### Anti-Pattern 4: Removing Domain-Based Lookup Immediately

**What:** Removing existing domain-based lookup in Phase 1.

**Why bad:**
- Breaks existing single-domain orgs
- No rollback path if tenant ID lookup fails
- Migration risk

**Instead:** Keep fallback chain during migration, remove after backfill.

## Edge Cases & Considerations

### Edge Case 1: Org Has Both Domain and Tenant ID

**Scenario:** Org previously had `domain: "aktor.ai"`, now adds `microsoftTenantId`.

**Solution:** Tenant ID lookup takes precedence for Microsoft SSO. Domain-based lookup still works for password/OTP.

```typescript
// For Microsoft SSO
if (method === 'microsoft' && tenantId) {
  // Use tenant ID lookup
}

// For password/OTP
if (method === 'password') {
  // Use domain lookup (existing flow)
}
```

---

### Edge Case 2: Multiple Orgs Share Same Tenant ID

**Scenario:** Misconfiguration or tenant migration causes duplicate tenantIds.

**Solution:** Database unique constraint + validation at config save time.

```typescript
// OrgAuthConfig schema
OrgAuthConfigSchema.index(
  { microsoftTenantId: 1 },
  {
    unique: true,
    sparse: true,  // Allows null/undefined
    partialFilterExpression: {
      isDeleted: false,
      microsoftTenantId: { $exists: true }
    }
  }
);
```

**Error handling:**
```typescript
try {
  await orgAuthConfig.save();
} catch (error) {
  if (error.code === 11000) {  // Duplicate key
    throw new BadRequestError(
      'This Microsoft tenant ID is already registered to another organization'
    );
  }
}
```

---

### Edge Case 3: User Switches Between Orgs

**Scenario:** User has accounts in Org A and Org B, both use same email provider but different tenants.

**Solution:** Each SSO session is scoped to the tenant. User must initiate auth from the correct org's login page.

**Flow:**
1. User goes to `app.example.com` (Org A's portal)
2. Enters email → Org A's config loaded
3. Microsoft auth → Returns tenant A's token
4. Validation passes only if tenant A matches config

**No change needed:** Existing session scoping already handles this.

---

### Edge Case 4: Token Has No Tenant ID Claim

**Scenario:** Personal Microsoft accounts (consumer accounts) don't have `tid` claim.

**Solution:** Fallback to domain-based lookup.

```typescript
const tenantId = extractTenantIdFromToken(idToken);

if (!tenantId) {
  logger.warn('No tenant ID in token, falling back to domain lookup');
  // Use domain-based lookup
}
```

**Note:** JIT provisioning with personal accounts should be disabled (check enableJit flag).

## Scalability Considerations

**At current scale (< 10K orgs):**
- MongoDB index on `microsoftTenantId` is sufficient
- No caching needed
- Direct database lookup on every auth

**At 100K orgs:**
- Consider Redis cache for tenantId -> orgId mapping
- Cache TTL: 1 hour (configs change infrequently)
- Invalidate cache on config update

**At 1M+ orgs:**
- Pre-load tenant mappings into memory (startup cache)
- Use bloom filter to avoid DB lookups for non-existent tenantIds
- Monitor slow query logs for index effectiveness

**Current recommendation:** Start with direct DB lookup. Revisit when auth latency > 100ms.

## Testing Strategy

### Unit Tests

**Test 1: Extract tenant ID from valid token**
```typescript
it('should extract tenant ID from Microsoft token', () => {
  const token = createMockMicrosoftToken({ tid: 'abc-123' });
  const tenantId = extractTenantIdFromToken(token);
  expect(tenantId).toBe('abc-123');
});
```

**Test 2: Handle missing tenant ID**
```typescript
it('should return null when tenant ID missing', () => {
  const token = createMockMicrosoftToken({ /* no tid */ });
  const tenantId = extractTenantIdFromToken(token);
  expect(tenantId).toBeNull();
});
```

**Test 3: Lookup org by tenant ID**
```typescript
it('should find org by tenant ID', async () => {
  await OrgAuthConfig.create({
    orgId: testOrg._id,
    microsoftTenantId: 'abc-123',
    authSteps: [/* ... */],
  });

  const config = await OrgAuthConfig.findOne({
    microsoftTenantId: 'abc-123',
  });

  expect(config.orgId.toString()).toBe(testOrg._id.toString());
});
```

### Integration Tests

**Test 1: Multi-domain user JIT provisioning**
```typescript
it('should provision user from secondary domain', async () => {
  // Setup: Org with domain "aktor.ai" and tenantId "abc-123"
  // Test: User with email "user@biosar.gr" authenticates
  // Expected: User provisioned under aktor.ai org
});
```

**Test 2: Fallback to domain for non-Microsoft auth**
```typescript
it('should use domain lookup for password auth', async () => {
  // Setup: User with email from domain "aktor.ai"
  // Test: User uses password auth (not Microsoft SSO)
  // Expected: Domain-based lookup still works
});
```

**Test 3: Tenant ID mismatch rejection**
```typescript
it('should reject token from wrong tenant', async () => {
  // Setup: Org configured with tenantId "abc-123"
  // Test: User provides token with tenantId "xyz-789"
  // Expected: Validation fails, JIT does not provision
});
```

## Migration Path

**Step 1: Schema deployment**
```bash
# Deploy new schema with microsoftTenantId field
npm run migrate:schema

# Verify index created
db.orgAuthConfig.getIndexes()
```

**Step 2: Backfill existing orgs**
```typescript
// Script: backfill-microsoft-tenant-ids.ts
async function backfillTenantIds() {
  const orgs = await Org.find({ isDeleted: false });

  for (const org of orgs) {
    const config = await OrgAuthConfig.findOne({ orgId: org._id });
    if (!config) continue;

    // Check if Microsoft auth is enabled
    const hasMicrosoft = config.authSteps.some(step =>
      step.allowedMethods.some(m =>
        m.type === 'microsoft' || m.type === 'azureAd'
      )
    );

    if (!hasMicrosoft) continue;

    // Fetch Microsoft config from Configuration Manager
    const user = { _id: 'system', orgId: org._id };
    try {
      const response = await configurationManagerService.getConfig(
        cmBackendUrl,
        MICROSOFT_AUTH_CONFIG_PATH,
        user,
        scopedJwtSecret,
      );

      const { tenantId } = response.data;
      if (tenantId && !config.microsoftTenantId) {
        config.microsoftTenantId = tenantId;
        await config.save();
        console.log(`Backfilled org ${org._id}: tenantId ${tenantId}`);
      }
    } catch (error) {
      console.error(`Failed to backfill org ${org._id}:`, error);
    }
  }
}
```

**Step 3: Deploy code changes**
```bash
# Phase 2: Token extraction
git deploy phase-2-token-extraction

# Phase 3: Tenant ID lookup
git deploy phase-3-tenant-lookup

# Monitor logs for lookup failures
kubectl logs -f auth-service | grep "tenant.*lookup"
```

**Step 4: Validate**
```bash
# Test multi-domain users
curl -X POST /api/v1/auth/init \
  -d '{"email": "user@biosar.gr"}'

# Expected: Session created with Microsoft SSO option

curl -X POST /api/v1/auth/authenticate \
  -H "x-session-token: <token>" \
  -d '{"method": "microsoft", "credentials": {...}}'

# Expected: User provisioned under correct org
```

## Monitoring & Observability

**Key metrics to track:**

1. **Tenant ID extraction success rate**
   - Counter: `microsoft_auth.tenant_id_extracted`
   - Counter: `microsoft_auth.tenant_id_missing`

2. **Org lookup method**
   - Counter: `org_lookup.tenant_id_success`
   - Counter: `org_lookup.domain_fallback`
   - Counter: `org_lookup.failed`

3. **JIT provisioning by lookup method**
   - Counter: `jit_provisioning.tenant_id_match`
   - Counter: `jit_provisioning.domain_match`

**Log examples:**
```typescript
this.logger.info('Tenant ID extracted from token', {
  tenantId,
  email,
  hasOrgConfig: !!orgAuthConfig
});

this.logger.warn('Tenant ID lookup failed, falling back to domain', {
  tenantId,
  domain,
  email,
});
```

## Sources

**HIGH confidence sources:**
- Existing codebase analysis (userAccount.controller.ts, jit-provisioning.service.ts)
- OrgAuthConfig schema definition
- Configuration Manager implementation
- Microsoft token structure (Azure AD documentation)

**Medium confidence:**
- Migration strategy based on similar schema migrations in project history
- Performance estimates based on current MongoDB index performance

**No external sources needed:** Architecture research based entirely on existing codebase patterns.

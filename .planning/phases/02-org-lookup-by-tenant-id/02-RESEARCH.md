# Phase 2: Org Lookup by Tenant ID - Research

**Researched:** 2026-01-31
**Domain:** Authentication flows, JWT handling, database query patterns, backward compatibility
**Confidence:** HIGH

## Summary

This phase modifies the JIT provisioning flow to identify organizations by Microsoft Entra ID tenant ID instead of email domain. The research reveals that successful implementation requires careful security considerations around JWT decoding without verification, proper fallback query patterns, and robust error handling.

The standard approach involves extracting the tenant ID from the unverified token early in the flow (to identify which organization's configuration to load), then performing full signature validation once the correct tenant configuration is known. This chicken-and-egg problem is solved by the pattern already implemented in Phase 1: decode without verification for lookup purposes, then validate fully before provisioning.

MongoDB fallback query patterns should try tenant ID first (most specific match), then fall back to domain-based lookup (for backward compatibility). Proper null checking and structured logging are critical for debugging multi-domain scenarios.

**Primary recommendation:** Use extractTenantIdFromToken() for initial org lookup, implement tenant-first/domain-fallback query pattern with explicit null checks, and add structured logging with tenant ID, domain, and matched org for audit trail.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| jsonwebtoken | ^9.0.3 | JWT decoding and validation | Industry standard for Node.js JWT handling, supports both decode() and verify() |
| mongoose | ^8.9.2 | MongoDB ODM | Project's existing ORM, provides type-safe queries with null handling |
| winston | ^3.17.0 | Structured logging | Project's existing logger, supports structured context for audit trails |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| jwk-to-pem | ^2.0.7 | Convert JWK to PEM format | Required for Microsoft Entra ID signature validation (already in use) |
| axios | ^1.12.0 | HTTP client for JWKS fetching | Fetch OpenID configuration and signing keys (already in use) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| jsonwebtoken | jose | jose is more modern and TypeScript-native, but jsonwebtoken is already in use project-wide |
| Manual fallback logic | mongoose-findone-or-error | Plugin adds error throwing, but explicit null checks provide clearer control flow |

**Installation:**
```bash
# All required libraries already installed in project
# No new dependencies needed for Phase 2
```

## Architecture Patterns

### Recommended Flow Structure
```
authenticate() method:
├── 1. Extract tenant ID (unverified) from token
├── 2. Query OrgAuthConfig by microsoftTenantId
├── 3. Fallback to domain-based query if null
├── 4. Validate token signature with found org's tenantId
├── 5. Provision user under matched org
└── 6. Log matched org and method used
```

### Pattern 1: Two-Phase JWT Processing (Security-Critical)
**What:** Decode JWT without verification for routing, then verify with proper configuration
**When to use:** When the validation parameters (tenant ID) come from database lookup based on token contents
**Security note:** The unverified tenant ID is ONLY used for database lookup, never for authorization decisions

**Example:**
```typescript
// Phase 1: Extract tenant ID for org lookup (NO VERIFICATION)
const tenantId = extractTenantIdFromToken(credentials.idToken);

// Phase 2: Find org configuration
const orgAuthConfig = await OrgAuthConfig.findOne({
  microsoftTenantId: tenantId,
  isDeleted: false
});

// Phase 3: NOW validate with the org's expected tenant ID
const { tenantId: expectedTenantId } = configManagerResponse.data;
const decodedToken = await validateAzureAdUser(credentials, expectedTenantId);
// decodedToken is NOW trusted
```

**Why this works:** Even if an attacker modifies the tenant ID in the token, they can't forge the signature. Full validation ensures the token was actually issued by the expected tenant.

### Pattern 2: Fallback Query with Null Checks
**What:** Try primary lookup method, explicitly check for null, fall back to secondary method
**When to use:** During migration periods or when supporting multiple authentication patterns

**Example:**
```typescript
// Source: MongoDB best practices + existing codebase patterns
const domain = this.getDomainFromEmail(email);
let orgAuthConfig: InstanceType<typeof OrgAuthConfig> | null = null;

// Try tenant ID lookup first (preferred)
if (tenantId) {
  orgAuthConfig = await OrgAuthConfig.findOne({
    microsoftTenantId: tenantId,
    isDeleted: false,
  });

  this.logger.debug('Tenant ID lookup', {
    tenantId,
    found: !!orgAuthConfig
  });
}

// Fallback to domain-based lookup
if (!orgAuthConfig && domain) {
  const org = await Org.findOne({
    domain,
    isDeleted: false,
  });

  if (org) {
    orgAuthConfig = await OrgAuthConfig.findOne({
      orgId: org._id,
      isDeleted: false,
    });

    this.logger.debug('Domain fallback lookup', {
      domain,
      orgId: org._id,
      found: !!orgAuthConfig
    });
  }
}

// Explicit null check before proceeding
if (!orgAuthConfig) {
  this.logger.warn('No org found for Microsoft SSO', {
    tenantId,
    domain,
    email,
  });
  // Handle JIT provisioning failure appropriately
}
```

### Pattern 3: Structured Authentication Logging
**What:** Log key identifiers (tenant ID, domain, email, orgId) at each decision point
**When to use:** All authentication flows, especially during migration or debugging

**Example:**
```typescript
// Source: Audit logging best practices
this.logger.info('Microsoft SSO org lookup', {
  tenantId: tenantId || null,
  domain,
  email: sessionInfo.email,
  method: 'tenant-id-first',
  orgFound: !!orgAuthConfig,
  orgId: orgAuthConfig?.orgId?.toString(),
});

// After successful provisioning
this.logger.info('JIT provisioning completed', {
  email: sessionInfo.email,
  method: 'microsoft',
  orgId: user.orgId,
  userId: user._id,
  matchedBy: tenantId ? 'microsoftTenantId' : 'domain',
});
```

### Anti-Patterns to Avoid
- **Using unverified JWT claims for authorization:** Never use extractTenantIdFromToken() result to authorize actions, only for org lookup before full validation
- **Forgetting null checks after findOne():** mongoose findOne() returns null when no document matches, must check before accessing properties
- **Silent fallback failures:** If domain fallback also fails, log the failure case with all attempted parameters
- **Modifying existing domain lookups first:** Implement alongside domain-based lookup, don't replace it until tenant ID field is populated for all orgs

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JWT signature validation | Custom crypto verification | jsonwebtoken.verify() with JWKS | Microsoft Entra ID uses RS256 with rotating keys, manual implementation will break |
| Extracting email domain | String manipulation with indexOf('@') | Existing getDomainFromEmail() utility | Already handles edge cases (empty strings, multiple @, trimming) |
| Retry logic for JWKS fetching | Manual setTimeout loops | axios-retry (already configured) | Handles transient failures, exponential backoff, network issues |
| Logging context correlation | Manual log message assembly | winston with structured fields | Enables querying logs by tenantId/orgId/email across authentication flow |

**Key insight:** Authentication flows have subtle edge cases (personal accounts, guest users, B2B scenarios) that appear only in production. Use battle-tested libraries and existing project patterns rather than custom logic.

## Common Pitfalls

### Pitfall 1: Missing Tenant ID in Token
**What goes wrong:** Personal Microsoft accounts or misconfigured applications may send tokens without tid claim, causing null pointer errors
**Why it happens:** extractTenantIdFromToken() returns null when tid claim is missing or token is malformed
**How to avoid:** Always null-check the tenant ID and have domain fallback ready
**Warning signs:** UnhandledPromiseRejection when accessing tenantId.toString() or similar

```typescript
// Bad
const tenantId = extractTenantIdFromToken(idToken);
const orgAuthConfig = await OrgAuthConfig.findOne({
  microsoftTenantId: tenantId.toString() // May crash if null
});

// Good
const tenantId = extractTenantIdFromToken(idToken);
if (tenantId) {
  orgAuthConfig = await OrgAuthConfig.findOne({
    microsoftTenantId: tenantId,
    isDeleted: false,
  });
}
```

### Pitfall 2: Trusting Unverified Token Claims
**What goes wrong:** Using tenant ID from unverified token for authorization decisions allows token forgery attacks
**Why it happens:** Developer confuses "we extracted the tenant ID" with "the tenant ID is verified"
**How to avoid:** Remember that extractTenantIdFromToken() is explicitly NOT VERIFIED per its JSDoc security note. Only use for org lookup, then validate fully.
**Warning signs:** Security audits flag use of jwt.decode() results in authorization logic

### Pitfall 3: Race Condition in Fallback Logic
**What goes wrong:** Both tenant ID and domain queries run in parallel, unpredictable which result is used
**Why it happens:** Misunderstanding of MongoDB async behavior or using Promise.race() incorrectly
**How to avoid:** Sequential queries with explicit null check between them (try tenant ID first, then domain)
**Warning signs:** Intermittent failures where sometimes the wrong org is matched

### Pitfall 4: Incomplete Backward Compatibility
**What goes wrong:** Orgs without microsoftTenantId populated can't authenticate after deployment
**Why it happens:** Assuming all orgs have tenant ID field populated immediately
**How to avoid:** Implement domain fallback that mirrors existing logic exactly; tenant ID lookup is additive, not replacement
**Warning signs:** Existing Microsoft SSO users suddenly can't log in after deployment

### Pitfall 5: Missing Audit Logs for Match Method
**What goes wrong:** When debugging multi-domain issues, can't tell if org was matched by tenant ID or domain fallback
**Why it happens:** Logging orgId but not logging which lookup method succeeded
**How to avoid:** Include `matchedBy: 'microsoftTenantId' | 'domain'` in all success logs
**Warning signs:** Support tickets where you can't reproduce the issue or explain why user was matched to specific org

## Code Examples

Verified patterns from official sources:

### Extracting Tenant ID (Phase 1 Complete)
```typescript
// Source: backend/nodejs/apps/src/modules/auth/utils/azureAdTokenValidation.ts
// Already implemented in Phase 1
import { extractTenantIdFromToken } from '../utils/azureAdTokenValidation';

const tenantId = extractTenantIdFromToken(credentials.idToken);
// tenantId is string | null
// UNTRUSTED until validateAzureAdUser() completes
```

### Full Validation After Org Lookup
```typescript
// Source: backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts:1414-1425
// Existing pattern, shows how validation happens AFTER config fetch
const configManagerResponse = await this.configurationManagerService.getConfig(
  this.config.cmBackend,
  MICROSOFT_AUTH_CONFIG_PATH,
  newUser,
  this.config.scopedJwtSecret,
);
const { tenantId } = configManagerResponse.data;
const decodedToken = await validateAzureAdUser(credentials, tenantId);
// decodedToken is NOW fully validated and trusted
```

### Mongoose Null Check Pattern
```typescript
// Source: TypeScript + Mongoose best practices
// Explicit type annotation with null union
let orgAuthConfig: InstanceType<typeof OrgAuthConfig> | null = null;

orgAuthConfig = await OrgAuthConfig.findOne({
  microsoftTenantId: tenantId,
  isDeleted: false,
});

if (!orgAuthConfig) {
  // Handle not found case
  // Do NOT access orgAuthConfig.orgId here (will throw)
}

// Safe to use orgAuthConfig after null check
const orgId = orgAuthConfig.orgId;
```

### Structured Logger Context
```typescript
// Source: backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts
// Existing pattern from controller
this.logger.info('Event description', {
  key1: value1,
  key2: value2,
  // Winston will serialize this as structured JSON
});

// For Phase 2, add:
this.logger.info('Microsoft SSO org matched', {
  tenantId: tenantId || 'null',
  domain,
  email: sessionInfo.email,
  orgId: orgAuthConfig.orgId.toString(),
  matchedBy: tenantId ? 'microsoftTenantId' : 'domain',
});
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Match org by email domain only | Match by tenant ID first, domain fallback | Phase 1 schema (2026-01) | Enables multi-domain orgs to use single SSO config |
| Domain stored in Org model | Domain optional, tenant ID primary in OrgAuthConfig | Phase 1 schema | Decouples org identity from auth routing |
| Full validation before org lookup | Extract tenant ID unverified, validate after config load | Established pattern | Solves chicken-and-egg: need tenant to find config, need config to validate tenant |

**Deprecated/outdated:**
- **Domain-only org matching:** Still supported for backward compatibility, but tenant ID is preferred for Microsoft SSO
- **Assuming tid is always present:** Personal Microsoft accounts use fixed tenant ID `9188040d-6c67-4c5b-b112-36a304b66dad`, some edge cases may have no tid

## Open Questions

1. **Should personal Microsoft accounts be supported?**
   - What we know: Personal accounts have tid = `9188040d-6c67-4c5b-b112-36a304b66dad`
   - What's unclear: Business requirement - are personal Microsoft accounts allowed for JIT provisioning?
   - Recommendation: Assume work/school accounts only unless requirements specify otherwise. Log warning if personal account tenant ID detected.

2. **Should fallback to domain be logged as warning or info?**
   - What we know: Domain fallback means tenant ID lookup failed (could be normal during migration or error condition)
   - What's unclear: Is this a transient migration state or ongoing support for legacy configs?
   - Recommendation: Use `info` level during migration period, consider changing to `warn` after all orgs have tenant IDs populated

3. **Should we fetch org AND orgAuthConfig in single aggregation?**
   - What we know: Current code does two separate queries when using domain fallback (Org.findOne, then OrgAuthConfig.findOne)
   - What's unclear: Performance impact, whether aggregation adds complexity vs. performance benefit
   - Recommendation: Keep separate queries for clarity; MongoDB indexes (from Phase 1) make lookups efficient. Optimize later if profiling shows bottleneck.

## Sources

### Primary (HIGH confidence)
- Microsoft Identity Platform Documentation: [ID Token Claims Reference](https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference) - tid claim specification and usage
- Existing codebase: backend/nodejs/apps/src/modules/auth/utils/azureAdTokenValidation.ts - extractTenantIdFromToken() implementation and security notes
- Existing codebase: backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts - JIT provisioning flow and validation patterns
- Existing codebase: backend/nodejs/apps/src/modules/auth/schema/orgAuthConfiguration.schema.ts - OrgAuthConfig model with microsoftTenantId field and indexes

### Secondary (MEDIUM confidence)
- [How to Avoid JWT Security Mistakes in Node.js](https://www.nodejs-security.com/blog/how-avoid-jwt-security-mistakes-nodejs) - JWT decode vs verify security considerations
- [Backward Compatible Database Changes — PlanetScale](https://planetscale.com/blog/backward-compatible-databases-changes) - Expand-migrate-contract pattern for schema changes
- [Mongoose in TypeScript](https://thecodebarbarian.com/working-with-mongoose-in-typescript.html) - findOne() null handling in TypeScript
- [Audit Logging Best Practices | Splunk](https://www.splunk.com/en_us/blog/learn/audit-logs.html) - Structured logging for authentication events

### Tertiary (LOW confidence)
- [JIT Provisioning Error Handling](https://github.com/wso2/product-is/issues/21429) - Community discussion on breaking auth flow when JIT provisioning fails (specific implementation varies)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - All libraries already in project, versions confirmed in package.json
- Architecture: HIGH - Patterns verified in existing codebase, Microsoft docs authoritative for tid claim
- Pitfalls: HIGH - Based on JWT security best practices, Mongoose documentation, and codebase analysis

**Research date:** 2026-01-31
**Valid until:** 2026-03-31 (60 days - stable domain, but Microsoft identity platform updates quarterly)

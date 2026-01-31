# Feature Landscape: Multi-Domain Microsoft SSO

**Domain:** Enterprise SSO with JIT Provisioning (Tenant ID Matching)
**Researched:** 2026-01-31
**Confidence:** HIGH (based on codebase analysis)

## Executive Summary

This research focuses on the **narrow scope** of adding tenant ID-based organization matching to the existing Microsoft SSO JIT provisioning flow. The current implementation already has 90% of required features - this milestone adds the missing org lookup mechanism.

**Current State Analysis:**
- ✓ Microsoft SSO authentication with MSAL
- ✓ Token validation with tenant ID extraction (`tid` claim)
- ✓ JIT user provisioning service
- ✓ Tenant ID stored in auth config (ETCD/Redis)
- ✓ `enableJit` flag support
- ✗ Org lookup by tenant ID (currently uses email domain only)

## Table Stakes Features

Features required for tenant ID matching to work correctly. Missing any = broken functionality.

| Feature | Why Expected | Complexity | Implementation Status |
|---------|--------------|------------|----------------------|
| **Extract tenant ID from token** | Microsoft tokens include `tid` claim; needed for org matching | Low | ✓ **EXISTS** - token decoded in `azureAdTokenValidation.ts` |
| **Look up org by tenant ID** | Core requirement - match user to org when domain doesn't match | Medium | ✗ **MISSING** - currently looks up by email domain only |
| **Fallback to existing domain lookup** | Backward compatibility for orgs not using tenant ID matching | Low | ✓ **EXISTS** - domain lookup in `initAuth` (lines 217-222) |
| **Validate tenant ID matches config** | Security - prevent users from wrong tenant authenticating | Low | ✓ **EXISTS** - `validateAzureAdUser` checks tenant ID (line 1214, 1422, 1435) |
| **Handle "user not found" state** | JIT flow already handles this; needs to work with tenant ID lookup | Low | ✓ **EXISTS** - `userId === "NOT_FOUND"` flow (lines 1365-1504) |
| **Query OrgAuthConfig by tenant ID** | Database lookup to find org when domain fails | Medium | ✗ **MISSING** - no index or query for this |

**Critical Path:**
1. User enters email → `initAuth` called
2. Email domain lookup fails → Check for tenant ID in token
3. Query auth config store for matching tenant ID
4. Return org + JIT-enabled methods if found
5. User authenticates → JIT provisions under matched org

## Differentiating Features

Features that improve UX or security but aren't strictly required for basic functionality.

| Feature | Value Proposition | Complexity | Priority |
|---------|-------------------|------------|----------|
| **Multi-tenant support per org** | Allow org to trust multiple Microsoft tenants | Medium | OUT OF SCOPE (per PROJECT.md) |
| **Tenant ID verification in UI** | Admin can verify correct tenant before saving config | Low | Nice to have |
| **Audit log for tenant ID matches** | Track which tenant ID was used for provisioning | Low | Post-MVP |
| **Domain allowlist** | Restrict which email domains can JIT provision even with valid tenant ID | Medium | Post-MVP |
| **Graceful degradation message** | Better error when tenant ID doesn't match any org | Low | Nice to have |
| **Admin dashboard showing domain count** | Visibility into how many domains are SSO-enabled | Low | Post-MVP |

**Recommendation:** Focus solely on table stakes. The existing JIT flow is solid; we only need the lookup mechanism.

## Anti-Features

Features to explicitly NOT build for this focused milestone.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **UI for managing domain lists** | Tenant ID approach eliminates need; building this wastes time | Store tenant ID only; Microsoft Entra ID manages domains |
| **DNS domain verification** | Microsoft already verifies domain ownership in Entra ID | Trust Microsoft's verification |
| **Email domain-based restrictions** | Conflicts with tenant ID matching philosophy; adds complexity | Use tenant ID as source of truth |
| **Google SSO changes** | Out of scope; only Microsoft requested | Leave Google SSO untouched |
| **Custom tenant ID field in Org schema** | Auth config already stores it; don't duplicate | Query via auth config's `tenantId` field |
| **"Preferred domain" selection** | No business need; tenant ID is sufficient identifier | Use tenant ID exclusively |
| **Domain migration tools** | Over-engineering for single-time setup | Manual config update if needed |

## Feature Dependencies

```
Microsoft SSO Token
    ↓
Tenant ID Extraction (exists)
    ↓
Org Lookup by Tenant ID (MISSING) ←──── requires ────┐
    ↓                                                  │
OrgAuthConfig with tenantId field (exists)            │
    ↓                                                  │
JIT Provisioning (exists)                             │
                                                       │
Backward Compatibility: Domain Lookup (exists) ───────┘
```

**Critical Dependency:** The org lookup is the **only** missing piece. All surrounding infrastructure exists.

## Scope-Specific Features

### For Tenant ID Matching (This Milestone)

**Must Have:**
1. Query `OrgAuthConfig` by `microsoft.tenantId` or `azureAd.tenantId`
2. Extract tenant ID from decoded token during `initAuth`
3. Try tenant ID lookup when domain lookup returns no results
4. Preserve existing domain-based lookup for backward compatibility

**Must NOT Have:**
- Changes to token validation (already works)
- Changes to JIT provisioning service (already works)
- Changes to Google SSO (out of scope)
- New database schemas (use existing auth config)

### Implementation Notes

**Storage Layer:**
- Tenant ID stored in: `configPaths.auth.microsoft` or `configPaths.auth.azureAD`
- Format: `{ clientId, tenantId, authority, enableJit }`
- Storage: ETCD or Redis (via `KeyValueStoreService`)
- Key structure: `/services/auth/microsoft` (global path, not org-scoped)

**Lookup Strategy:**
Current implementation stores auth config globally but retrieves it using user context (orgId). For tenant ID matching, we need:
1. Enumerate all stored auth configs
2. Filter by `tenantId` match
3. Extract `orgId` from matching config's key or metadata

**Complexity Note:** The current storage model is org-scoped (retrieve by orgId), but tenant ID matching requires reverse lookup (find orgId by tenantId). This may require:
- Index of tenantId → orgId mappings, OR
- Iteration through all org configs (acceptable for low org count)

## MVP Recommendation

**For this milestone, implement ONLY:**

1. **Tenant ID Extraction** (5 lines)
   - Read `tid` from decoded Microsoft token in `initAuth`
   - Complexity: Trivial - token already decoded

2. **Reverse Lookup Function** (30-50 lines)
   - Function: `findOrgByTenantId(tenantId: string): Promise<Org | null>`
   - Query auth config store for matching tenant ID
   - Return org if found, null otherwise
   - Complexity: Medium - needs to handle ETCD/Redis iteration

3. **Updated initAuth Flow** (10 lines)
   - After domain lookup fails, attempt tenant ID lookup
   - If found, proceed with existing JIT flow
   - Complexity: Low - conditional logic insertion

4. **Backward Compatibility Test** (validation)
   - Ensure existing domain-based orgs still work
   - Complexity: Low - regression testing

**Deferred to Post-MVP:**
- Error message improvements
- Audit logging
- Admin UI indicators
- Domain allowlisting

## Complexity Assessment

| Component | Complexity | Reason |
|-----------|------------|--------|
| Tenant ID extraction | **Trivial** | Token already decoded; just read `tid` claim |
| Org lookup logic | **Medium** | Need to iterate/query auth configs by tenantId |
| Integration into initAuth | **Low** | Insert conditional after domain lookup fails |
| Backward compatibility | **Low** | Existing flow preserved as fallback |
| Testing | **Medium** | Need to test both lookup paths |

**Overall Complexity:** Medium (driven primarily by storage model mismatch)

## Edge Cases to Handle

1. **Multiple orgs with same tenant ID** (configuration error)
   - Mitigation: Return first match, log warning
   - Better: Validate uniqueness on config save (future enhancement)

2. **Tenant ID in token but not in any config**
   - Behavior: Fall through to "user not found" error
   - No special handling needed

3. **Org has both domain and tenant ID configured**
   - Behavior: Domain lookup succeeds first; tenant ID never checked
   - This is correct - domain lookup is faster

4. **User from unexpected domain in known tenant**
   - Behavior: Tenant ID matches → JIT provisions successfully
   - This is the desired behavior (fixes the 48-domain problem)

5. **Backward compatibility: Org with no tenant ID configured**
   - Behavior: Tenant ID lookup returns null; falls back to domain lookup
   - Existing orgs unaffected

## Feature Flags

Not applicable for this milestone. Feature is gated by:
- Presence of `tenantId` in auth config (org-level opt-in)
- `enableJit: true` in auth config (already exists)

## Security Considerations

**Existing Security (Preserved):**
- Token signature validation via JWKS (in `validateAzureAdUser`)
- Tenant ID match validation (token's `tid` must match config's `tenantId`)
- User email match validation (token email must match session email)

**New Security Implications:**
- Tenant ID becomes trusted identifier for org matching
- Risk: If attacker can spoof tenant ID in config, they can redirect JIT provisioning
- Mitigation: Config encrypted at rest, admin-only write access (already implemented)

**No New Security Risks:** Tenant ID matching uses the same validated token data as current domain matching.

## Assumptions Validated

Based on codebase analysis:

1. ✓ Tenant ID is available in Microsoft tokens
   - Confirmed: `validateAzureAdUser` receives and validates `tenantId` parameter
   - Token structure includes `tid` claim (standard Microsoft ID token)

2. ✓ Tenant ID is already stored in auth config
   - Confirmed: `setMicrosoftAuthConfig` stores `{ clientId, tenantId, authority, enableJit }`
   - Path: `/services/auth/microsoft` in ETCD/Redis

3. ✓ JIT provisioning supports Microsoft SSO
   - Confirmed: `provisionUser` accepts `provider: 'microsoft' | 'azureAd'`
   - `extractMicrosoftUserDetails` extracts user info from token

4. ✓ Current lookup is domain-based only
   - Confirmed: `initAuth` line 217-222 uses `getDomainFromEmail` → `Org.findOne({ domain })`
   - No tenant ID lookup path exists

## Research Confidence

| Area | Confidence | Source |
|------|------------|--------|
| Current implementation | **HIGH** | Direct codebase analysis |
| Microsoft token structure | **HIGH** | MSAL documentation + existing token validation code |
| Storage model | **HIGH** | Analyzed `cm_controller.ts` and `paths.ts` |
| JIT provisioning flow | **HIGH** | Analyzed `jit-provisioning.service.ts` and `userAccount.controller.ts` |
| Tenant ID availability | **HIGH** | Already used in `validateAzureAdUser` |
| Edge cases | **MEDIUM** | Inferred from codebase patterns |

## Open Questions

1. **Storage lookup performance:** If there are 1000+ orgs, iterating all auth configs for tenant ID match could be slow
   - **Resolution needed:** Benchmark or implement tenant ID index if org count is high
   - **For MVP:** Acceptable if org count < 100

2. **Microsoft vs AzureAd distinction:** Codebase has both `microsoft` and `azureAd` auth types
   - **Observation:** Both use same `tenantId` and `validateAzureAdUser` function
   - **Resolution:** Support tenant ID lookup for both auth types

3. **Config storage scoping:** Current pattern retrieves config by orgId; tenant ID matching needs reverse lookup
   - **Impact:** May need to scan all configs or maintain separate index
   - **Decision needed:** Scan vs index tradeoff

## Summary for Roadmap

**This is a small, focused milestone:**
- 95% of infrastructure exists
- Only need org lookup mechanism
- Low risk - backward compatible by design
- Estimated effort: 1-2 days development + testing

**Recommended Phase Structure:**
1. Implement `findOrgByTenantId` helper function
2. Update `initAuth` to try tenant ID lookup after domain lookup fails
3. Test both legacy (domain) and new (tenant ID) flows
4. Deploy with feature validation

**No deep research needed for subsequent phases** - this is a standalone enhancement to existing auth flow.

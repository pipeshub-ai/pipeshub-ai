# Research Summary: Multi-Tenant Microsoft SSO

**Domain:** Enterprise Authentication - Microsoft Entra ID SSO
**Researched:** 2026-01-31
**Overall confidence:** HIGH

## Executive Summary

This research focused on the stack dimension for enabling multi-tenant Microsoft SSO authentication. The goal is to match users to organizations by Microsoft Entra ID tenant ID instead of email domain, enabling organizations with 48+ email domains under one Entra ID tenant to use SSO successfully.

**Key Finding:** The existing codebase already contains all necessary libraries and infrastructure. The solution is a logic change, not a stack upgrade. Microsoft Entra ID tokens include a `tid` (tenant ID) claim that uniquely identifies the tenant. Extracting this claim and using it for organization matching is the standard 2025 approach for multi-tenant SSO.

**Implementation Complexity:** LOW - This requires extracting one claim from an already-validated token and changing the organization lookup query.

**Dependencies:** ZERO - No new npm packages or infrastructure components needed.

## Key Findings

**Stack:** Existing libraries (@azure/msal-node 3.8.4, jsonwebtoken 9.0.3, jwk-to-pem 2.0.7) are current and sufficient. Token validation is already correctly implemented using tenant-specific JWKS endpoints.

**Token Claims:** Microsoft Entra ID ID tokens contain a `tid` claim (GUID format) that uniquely identifies the tenant. This claim is guaranteed present and is the authoritative source for tenant identification per Microsoft's official documentation.

**Architecture:** Current token validation flow is correct and follows Microsoft best practices. The only change needed is to extract `decodedToken.tid` after validation and use it for org lookup instead of email domain.

**Critical Pitfall Avoided:** Using email domain for multi-domain orgs is an anti-pattern. Microsoft explicitly provides `tid` for this use case. The tenant ID is cryptographically verified as part of the token signature, making it secure and reliable.

## Implications for Roadmap

Based on research, suggested implementation approach:

1. **Phase 1: Extract Tenant ID (Safe, Zero Risk)**
   - Add one line after token validation: `const tenantId = decodedToken.tid`
   - Log it for verification
   - No behavioral changes yet
   - **Effort:** 5 minutes

2. **Phase 2: Implement Tenant-Based Lookup with Fallback**
   - Query OrgAuthConfig by Microsoft tenant ID
   - Fall back to domain-based lookup if no match
   - Maintains backward compatibility
   - **Effort:** 1-2 hours (query logic + error handling)

3. **Phase 3: Test Multi-Domain Scenarios**
   - Verify users from all domains (aktor.ai, aktor.gr, biosar.gr, intrakat.com) can sign in
   - Confirm all land in same organization
   - Validate personal Microsoft accounts are rejected (optional security hardening)
   - **Effort:** 1-2 hours (test cases + manual testing)

4. **Phase 4: Optional Cleanup**
   - Remove domain-based fallback once validated
   - Add performance optimizations (JWKS caching, tenant ID indexing)
   - **Effort:** 1 hour (if needed)

**Total implementation time:** 3-5 hours (excluding testing time)

**Phase ordering rationale:**
- Extract-first approach minimizes risk (Phase 1 is observability-only)
- Fallback pattern in Phase 2 ensures no breaking changes
- Testing can happen incrementally with real tokens
- Cleanup is optional and can be deferred

**Research flags for phases:**
- Phase 1: No additional research needed (implementation is straightforward)
- Phase 2: No additional research needed (query patterns are standard)
- Phase 3: No additional research needed (testing is verification, not discovery)
- Phase 4: No additional research needed (optimizations are well-documented)

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Existing libraries verified current and sufficient via package.json and Microsoft docs |
| Token Claims | HIGH | `tid` claim documented in official Microsoft Entra ID token reference (verified 2026-01-31) |
| Implementation Approach | HIGH | Pattern matches Microsoft's recommended multi-tenant approach |
| Security | HIGH | Token validation already correct; extracting `tid` after validation is safe |
| Backward Compatibility | HIGH | Fallback to domain-based lookup ensures no breaking changes |

## Gaps to Address

**None for core implementation.** The research is complete and actionable.

**Optional enhancements identified:**
1. Personal account rejection (security hardening)
2. JWKS caching (performance optimization)
3. Tenant ID schema denormalization (performance optimization if many orgs)

These are not blockers and can be addressed post-MVP if needed.

## Key Technical Details

### Microsoft Token Structure
```json
{
  "tid": "aaaabbbb-0000-cccc-1111-dddd2222eeee",  // Tenant ID - use this
  "iss": "https://login.microsoftonline.com/{tid}/v2.0",  // Issuer contains tid
  "oid": "user-object-id-guid",  // User ID within tenant
  "email": "user@aktor.gr",  // Mutable, not reliable for org matching
  "preferred_username": "user@aktor.gr",
  "name": "User Name",
  "given_name": "User",
  "family_name": "Name"
}
```

### Current Implementation Assessment

**Already correct:**
- Token signature validation using tenant-specific JWKS
- Algorithm validation (RS256)
- JWK to PEM conversion
- Token expiry handling
- User detail extraction (name, email)

**Needs adjustment:**
- Organization lookup logic (domain → tenant ID)
- JIT provisioning org matching (domain → tenant ID)

### Existing Code Locations

| File | Lines | Current Behavior | Required Change |
|------|-------|------------------|----------------|
| `azureAdTokenValidation.ts` | 13-56 | Validates token, returns decoded | None - working correctly |
| `userAccount.controller.ts` | 217-222 | Matches org by email domain | Add tenant ID matching |
| `userAccount.controller.ts` | 1422, 1435 | Validates token, extracts user details | Add `tid` extraction |
| `jit-provisioning.service.ts` | 126-141 | Extracts user details from token | None - working correctly |

## Security Validation

**Token Trust Boundary:** The current implementation correctly validates the token signature BEFORE extracting claims. This is critical because:
1. Token comes from untrusted source (user's browser)
2. Signature validation proves token was issued by Microsoft
3. Only after validation can `tid` be trusted

**Tenant ID Spoofing Prevention:**
```typescript
// ✓ Current code (correct order)
const decodedToken = await validateAzureAdUser(credentials, tenantId);  // Validates signature
const tokenTenantId = decodedToken.tid;  // Safe - signature verified

// ✗ Wrong pattern (vulnerable to spoofing)
const decoded = jwt.decode(idToken);  // No signature check
const tokenTenantId = decoded.tid;  // Attacker could forge this
```

The existing implementation follows the correct pattern, so tenant ID extraction will be secure.

## Official Sources

All findings based on Microsoft official documentation:

1. **Microsoft Entra ID Token Claims Reference**
   - URL: https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference
   - Retrieved: 2026-01-31
   - Content: Verified `tid` claim structure, GUID format, guaranteed presence

2. **Multi-Tenant Application Patterns**
   - URL: https://learn.microsoft.com/en-us/entra/identity-platform/howto-convert-app-to-be-multi-tenant
   - Retrieved: 2026-01-31
   - Content: Verified tenant identification via `tid` claim, `/common` vs tenant-specific endpoints

3. **Access Token and ID Token Validation**
   - URL: https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens
   - Retrieved: 2026-01-31
   - Content: Verified validation patterns, issuer matching, tenant isolation requirements

## Ready for Roadmap

Research is complete. All information needed for roadmap creation is documented in STACK.md.

**Next step:** Create implementation roadmap with phases outlined above.

**Estimated total effort:** 3-5 hours of development + 2-3 hours of testing = 1 day total

**Risk level:** LOW (logic change in existing validated flow, with backward-compatible fallback)

**Dependencies:** None (no external systems, no new libraries, no schema migrations)

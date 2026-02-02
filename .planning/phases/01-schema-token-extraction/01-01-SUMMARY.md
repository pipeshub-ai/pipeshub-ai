---
phase: 01-schema-token-extraction
plan: 01
subsystem: auth
tags: [microsoft, azure-ad, jwt, mongoose, tenant-id]

# Dependency graph
requires:
  - phase: research
    provides: ARCHITECTURE.md with auth schema analysis
provides:
  - OrgAuthConfig schema with microsoftTenantId field and sparse index
  - extractTenantIdFromToken() helper for tid extraction from JWT
  - Compound index on (microsoftTenantId, isDeleted) for efficient lookups
affects: [02-auth-flow-integration, 03-migration-testing]

# Tech tracking
tech-stack:
  added: []
  patterns: [sparse-indexing-for-optional-fields, untrusted-extraction-before-validation]

key-files:
  created: []
  modified:
    - backend/nodejs/apps/src/modules/auth/schema/orgAuthConfiguration.schema.ts
    - backend/nodejs/apps/src/modules/auth/utils/azureAdTokenValidation.ts

key-decisions:
  - "Use sparse index for microsoftTenantId to avoid indexing null values"
  - "Extract tenant ID without validation (untrusted) before org lookup, then validate fully"
  - "Place compound index on (microsoftTenantId, isDeleted) for efficient filtered queries"

patterns-established:
  - "Sparse indexes for optional SSO tenant identifiers"
  - "Extract-then-validate pattern for tenant-based org lookup"

# Metrics
duration: 4min
completed: 2026-01-31
---

# Phase 01 Plan 01: Schema & Token Extraction Summary

**OrgAuthConfig schema with microsoftTenantId field, sparse indexing, and JWT tenant ID extraction helper**

## Performance

- **Duration:** 4 minutes
- **Started:** 2026-01-31T03:34:36Z
- **Completed:** 2026-01-31T03:38:55Z
- **Tasks:** 3 (2 implementation, 1 verification)
- **Files modified:** 2

## Accomplishments
- OrgAuthConfig schema extended with microsoftTenantId optional field
- Sparse index on microsoftTenantId for efficient queries without bloating index with nulls
- Compound index on (microsoftTenantId, isDeleted) for filtered tenant lookups
- extractTenantIdFromToken() helper extracts tid claim from Microsoft JWT
- Security documentation clarifying untrusted extraction before full validation

## Task Commits

Each task was committed atomically:

1. **Task 1: Add microsoftTenantId field to OrgAuthConfig schema** - `b92bacee` (feat)
2. **Task 2: Add extractTenantIdFromToken helper function** - `47b61fac` (feat)
3. **Task 3: Verify existing auth still works** - No commit (verification only)

## Files Created/Modified
- `backend/nodejs/apps/src/modules/auth/schema/orgAuthConfiguration.schema.ts` - Added microsoftTenantId field to IOrgAuthConfig interface, added field to schema with sparse index, added compound index
- `backend/nodejs/apps/src/modules/auth/utils/azureAdTokenValidation.ts` - Added extractTenantIdFromToken() function to extract tid claim from JWT payload

## Decisions Made

**1. Use sparse index for microsoftTenantId**
- Rationale: Field is optional (only for Microsoft SSO orgs). Sparse index only indexes documents where field exists, saving space and improving performance for non-Microsoft orgs.

**2. Compound index on (microsoftTenantId, isDeleted)**
- Rationale: Queries will always filter by both fields. Compound index optimizes the query pattern `{ microsoftTenantId: tid, isDeleted: false }`.

**3. Extract tenant ID without signature validation**
- Rationale: Need tenant ID to lookup org before knowing which tenant to validate against. Extract (untrusted) → lookup org → validate with org's tenant. JSDoc documents security consideration.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

**TypeScript compilation warnings**
- Issue: Third-party dependency type conflicts (mocha, inversify) present in project
- Resolution: Confirmed no errors related to our code changes. Existing dependency issues are unrelated to this phase.
- Impact: None - our schema and helper function compile successfully.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

**Ready for Phase 02 (Auth Flow Integration):**
- microsoftTenantId field available in OrgAuthConfig schema
- extractTenantIdFromToken() helper ready to extract tid from incoming tokens
- MongoDB indexes in place for efficient tenant-based org lookups

**No blockers or concerns.**

**Note for Phase 02:**
- Use extractTenantIdFromToken() in auth flow BEFORE calling validateAzureAdUser()
- Query pattern: `OrgAuthConfig.findOne({ microsoftTenantId: tid, isDeleted: false })`
- The tid from extractTenantIdFromToken() is untrusted until full validation completes

---
*Phase: 01-schema-token-extraction*
*Completed: 2026-01-31*

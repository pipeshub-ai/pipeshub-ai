# Roadmap: Multi-Domain Microsoft SSO

**Project:** Multi-Domain Microsoft SSO
**Created:** 2026-01-31
**Mode:** YOLO | Quick depth | Parallel execution

## Milestone 1: Multi-Domain SSO Support

**Goal:** Enable JIT provisioning for all 48+ company domains via Microsoft tenant ID matching

**Success Criteria:**
- Users from any company domain (aktor.ai, aktor.gr, biosar.gr, intrakat.com, etc.) can SSO
- All users provision under the same organization
- Existing single-domain SSO continues to work
- No new dependencies added

---

### Phase 1: Schema & Token Extraction ✓
**Goal:** Add infrastructure for tenant ID storage and extraction
**Status:** Complete
**Completed:** 2026-01-31

**Deliverables:**
1. Add `microsoftTenantId` field to OrgAuthConfig schema ✓
2. Add database index for efficient lookup ✓
3. Add `extractTenantIdFromToken()` helper function ✓

**Entry Criteria:** Research complete ✓
**Exit Criteria:** Schema deployed, extraction helper tested ✓

**Key Files:**
- `backend/nodejs/apps/src/modules/auth/schema/orgAuthConfiguration.schema.ts`
- `backend/nodejs/apps/src/modules/auth/utils/azureAdTokenValidation.ts`

**Plans:** 1 plan
Plans:
- [x] 01-01-PLAN.md — Add microsoftTenantId schema field and extractTenantIdFromToken helper

---

### Phase 2: Org Lookup by Tenant ID ✓
**Goal:** Match organizations by Microsoft tenant ID instead of email domain
**Status:** Complete
**Completed:** 2026-02-01

**Deliverables:**
1. Modify JIT provisioning flow to extract tenant ID from validated token ✓
2. Add org lookup by `microsoftTenantId` in OrgAuthConfig ✓
3. Implement fallback to domain-based lookup for backward compatibility ✓
4. Add logging for tenant ID matching ✓

**Entry Criteria:** Phase 1 complete ✓
**Exit Criteria:** Multi-domain users can SSO successfully ✓

**Key Files:**
- `backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts`

**Plans:** 1 plan
Plans:
- [x] 02-01-PLAN.md — Add tenant ID org lookup in JIT Microsoft flow

---

### Phase 3: Validation & Integration ✓
**Goal:** Verify multi-domain SSO works end-to-end
**Status:** Complete
**Completed:** 2026-02-02

**Deliverables:**
1. Test with users from multiple domains (aktor.ai, aktor.gr, biosar.gr) ✓
2. Verify all users provision under same organization ✓
3. Confirm existing users still work ✓
4. Update org's Microsoft config with tenant ID (if not already present) ✓
5. **Bonus:** Auto-sync tenantId from Microsoft config UI ✓
6. **Bonus:** Tenant ID fallback in initAuth for unknown domains ✓

**Entry Criteria:** Phase 2 complete ✓
**Exit Criteria:** All acceptance criteria pass ✓

**Key Files:**
- `backend/nodejs/apps/src/modules/auth/utils/azureAdTokenValidation.spec.ts`
- `backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.spec.ts`
- `backend/nodejs/apps/src/modules/configuration_manager/controller/cm_controller.ts`

**Plans:** 2 plans
Plans:
- [x] 03-01-PLAN.md — Write integration tests for tenant ID org lookup
- [x] 03-02-PLAN.md — Manual SSO verification and tenant ID config update

---

## Phase Dependency Graph

```
Phase 1 (Schema & Extraction) ✓
    │
    └──> Phase 2 (Org Lookup) ✓
              │
              └──> Phase 3 (Validation) ✓

MILESTONE COMPLETE 🎉
```

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| Tenant ID spoofing | High | Extract tid AFTER token validation (already done) |
| Breaking existing SSO | Medium | Domain-based fallback during rollout |
| Performance regression | Low | Add database index on microsoftTenantId |

## Estimated Effort

| Phase | Effort |
|-------|--------|
| Phase 1 | 1-2 hours |
| Phase 2 | 2-3 hours |
| Phase 3 | 1-2 hours |
| **Total** | **4-7 hours** |

---
*Generated from research at .planning/research/*

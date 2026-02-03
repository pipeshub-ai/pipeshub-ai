# Roadmap: Automatic Global Reader Team

**Created:** 2026-02-04
**Mode:** yolo | **Depth:** quick
**Total Phases:** 3 | **Total Plans:** 6

## Overview

This roadmap implements automatic team membership for a system-managed "Global Reader" team. All users are automatically added upon registration, enabling admins to grant organization-wide read access via a single team.

**Implementation Strategy:** Brownfield integration using existing hook points identified in research:
- Primary hook: Post-JIT provisioning in `userAccount.controller.ts`
- Existing patterns: PERMISSION edges in ArangoDB, UserGroup admin detection

## Phase 1: Team Foundation

**Goal:** Establish the Global Reader team as a system-managed entity.

**Requirements Covered:**
- TEAM-01: Global Reader team exists in the system (created if not present)
- TEAM-02: Team has name "Global Reader" and appropriate description

**Success Criteria:**
1. Global Reader team exists after application startup
2. Team can be queried by name via existing team APIs
3. Team creation is idempotent (safe to run multiple times)

**Plans:**
1. **PLAN-1.1: Global Reader Service** - Create `globalReaderTeam.service.ts` with `ensureGlobalReaderTeamExists()` method
2. **PLAN-1.2: Startup Integration** - Call team creation on application bootstrap

**Estimated Effort:** Small (1-2 implementation tasks)

---

## Phase 2: Membership Automation

**Goal:** Automatically add users to Global Reader team with appropriate privileges.

**Requirements Covered:**
- MEMB-01: New users are automatically added to Global Reader team on registration
- MEMB-02: New users receive Reader privilege by default
- MEMB-03: New admin users receive Owner privilege instead of Reader
- MEMB-04: Admin status is determined by UserGroup membership (type='admin')

**Success Criteria:**
1. New user created via any path (SSO, admin, self-registration) appears in Global Reader team
2. Non-admin users have READER privilege on the team
3. Admin users have OWNER privilege on the team
4. Admin detection uses existing UserGroup type='admin' pattern

**Plans:**
1. **PLAN-2.1: Add User Method** - Add `addUserToGlobalReader(userId, isAdmin)` to service
2. **PLAN-2.2: Hook Integration** - Integrate with JIT provisioning and user creation flows
3. **PLAN-2.3: Admin Detection** - Implement admin check using UserGroup membership

**Estimated Effort:** Medium (3 implementation tasks)

---

## Phase 3: Reliability & Resilience

**Goal:** Ensure team membership operations are non-blocking and idempotent.

**Requirements Covered:**
- RELY-01: Team assignment failures do not block user registration
- RELY-02: Team assignment failures do not block user login
- RELY-03: Multiple registrations/logins do not create duplicate memberships
- RELY-04: Failed assignments are logged for debugging

**Success Criteria:**
1. User registration succeeds even if team service is unavailable
2. User login succeeds even if team assignment fails
3. Running the same user through registration twice does not create duplicate memberships
4. Team assignment errors appear in application logs with user context

**Plans:**
1. **PLAN-3.1: Non-Blocking Wrapper** - Wrap all team operations in try-catch, log failures, never throw

**Estimated Effort:** Small (1 implementation task)

---

## Phase Summary

| Phase | Requirements | Plans | Effort |
|-------|-------------|-------|--------|
| 1: Team Foundation | TEAM-01, TEAM-02 | 2 | Small |
| 2: Membership Automation | MEMB-01, MEMB-02, MEMB-03, MEMB-04 | 3 | Medium |
| 3: Reliability & Resilience | RELY-01, RELY-02, RELY-03, RELY-04 | 1 | Small |

**Total:** 10 requirements mapped, 6 plans, estimated small-medium overall effort.

## Requirement Coverage Verification

| Requirement | Phase | Status |
|-------------|-------|--------|
| TEAM-01 | Phase 1 | Mapped |
| TEAM-02 | Phase 1 | Mapped |
| MEMB-01 | Phase 2 | Mapped |
| MEMB-02 | Phase 2 | Mapped |
| MEMB-03 | Phase 2 | Mapped |
| MEMB-04 | Phase 2 | Mapped |
| RELY-01 | Phase 3 | Mapped |
| RELY-02 | Phase 3 | Mapped |
| RELY-03 | Phase 3 | Mapped |
| RELY-04 | Phase 3 | Mapped |

**Coverage:** 10/10 v1 requirements (100%)

---
*Roadmap created: 2026-02-04*

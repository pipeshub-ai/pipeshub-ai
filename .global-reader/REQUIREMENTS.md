# Requirements: Automatic Global Reader Team

**Defined:** 2026-02-04
**Core Value:** Users are automatically part of a universal team, so admins can grant organization-wide access without manual membership management.

## v1 Requirements

Requirements for initial release.

### Team Setup

- [x] **TEAM-01**: Global Reader team exists in the system (created if not present)
- [x] **TEAM-02**: Team has name "Global Reader" and appropriate description

### Membership Automation

- [ ] **MEMB-01**: New users are automatically added to Global Reader team on registration
- [ ] **MEMB-02**: New users receive Reader privilege by default
- [ ] **MEMB-03**: New admin users receive Owner privilege instead of Reader
- [ ] **MEMB-04**: Admin status is determined by UserGroup membership (type='admin')

### Reliability

- [ ] **RELY-01**: Team assignment failures do not block user registration
- [ ] **RELY-02**: Team assignment failures do not block user login
- [ ] **RELY-03**: Multiple registrations/logins do not create duplicate memberships
- [ ] **RELY-04**: Failed assignments are logged for debugging

## v2 Requirements

Deferred to future release.

### Retroactive Membership

- **RETRO-01**: Existing users (created before feature) are added to Global Reader on next login
- **RETRO-02**: Existing admin users receive Owner privilege on retroactive assignment

### Role Transitions

- **ROLE-01**: When user is promoted to admin, upgrade Global Reader privilege from Reader to Owner
- **ROLE-02**: Role upgrade is idempotent (multiple promotions don't duplicate)

### Self-Healing

- **HEAL-01**: If Global Reader team is deleted, recreate it automatically
- **HEAL-02**: After recreation, existing membership state is preserved or restored

## Out of Scope

| Feature | Reason |
|---------|--------|
| Per-organization Global Reader teams | System-wide team is sufficient; simpler implementation |
| Multiple default teams | Single Global Reader covers the use case |
| Manual removal from Global Reader | Membership is automatic and tied to system membership |
| Writer privilege for Global Reader | Team is for read access; write access managed separately |
| UI for managing Global Reader | System team managed automatically, not user-configurable |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| TEAM-01 | Phase 1: Team Foundation | Complete |
| TEAM-02 | Phase 1: Team Foundation | Complete |
| MEMB-01 | Phase 2: Membership Automation | Pending |
| MEMB-02 | Phase 2: Membership Automation | Pending |
| MEMB-03 | Phase 2: Membership Automation | Pending |
| MEMB-04 | Phase 2: Membership Automation | Pending |
| RELY-01 | Phase 3: Reliability & Resilience | Pending |
| RELY-02 | Phase 3: Reliability & Resilience | Pending |
| RELY-03 | Phase 3: Reliability & Resilience | Pending |
| RELY-04 | Phase 3: Reliability & Resilience | Pending |

**Coverage:**
- v1 requirements: 10 total
- Mapped to phases: 10
- Unmapped: 0

---
*Requirements defined: 2026-02-04*
*Last updated: 2026-02-04 after Phase 1 completion*

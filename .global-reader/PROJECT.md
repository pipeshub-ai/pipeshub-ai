# Automatic Global Reader Team

## What This Is

A built-in "Global Reader" team that all users are automatically added to upon registration or first login. This enables admins to grant organization-wide read access to knowledge bases by adding a single team, eliminating the need to manually maintain an "everyone" team.

## Core Value

Users are automatically part of a universal team, so admins can grant organization-wide access without manual membership management.

## Requirements

### Validated

- ✓ Team creation and management — existing
- ✓ Team membership with privileges (Reader/Writer/Owner) — existing
- ✓ Knowledge base access control via teams — existing
- ✓ User registration flow — existing
- ✓ User authentication flow — existing
- ✓ Admin role identification — existing

### Active

- [ ] Create "Global Reader" team on application startup (idempotent)
- [ ] Auto-add new users to Global Reader with Reader privilege
- [ ] Auto-add new admin users to Global Reader with Owner privilege
- [ ] Retroactively add existing users on login if not already members
- [ ] Upgrade existing admin members from Reader to Owner if needed
- [ ] Ensure failures don't block login/registration

### Out of Scope

- Per-organization default teams — system-wide team is sufficient for current needs
- Multiple default teams — single Global Reader covers the use case
- Manual removal from Global Reader — membership is automatic and tied to system membership
- Writer privilege for Global Reader — team is for read access; write access managed separately

## Context

**Problem being solved:**
Admins want certain knowledge bases readable by everyone in the organization. Today they must:
1. Create a team manually
2. Add every existing user
3. Remember to add every new user

This is tedious and error-prone. New users don't get access until someone remembers to add them.

**Solution approach:**
A system-managed team where membership is automatic. The team exists by default, users are added automatically, and admins just add this team to knowledge bases that should be universally readable.

**Existing patterns to leverage:**
- Team/membership models already support Reader/Owner privileges
- User registration and authentication flows exist as hook points
- Admin role is already identifiable in the system

## Constraints

- **Non-blocking**: Team assignment failures must not prevent user login or registration
- **Idempotent**: Multiple logins must not create duplicate memberships
- **Backward compatible**: Existing users get added on next login, not via migration
- **Existing privilege model**: Use existing Reader/Owner privilege definitions

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| System-wide team (not per-org) | Simpler implementation, matches "everyone" semantics | — Pending |
| Add on login (not migration) | Safer rollout, no batch data changes | — Pending |
| Non-blocking failures | User experience > team membership | — Pending |
| Reuse existing privilege model | Consistency, less code | — Pending |

---
*Last updated: 2026-02-04 after initialization*

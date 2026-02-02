# Multi-Domain Microsoft SSO

## What This Is

A fix to enable Microsoft SSO JIT provisioning for organizations with multiple email domains. Instead of matching users to orgs by email domain (which only supports one domain), match by Microsoft Entra ID tenant ID. This allows all 48+ company domains (aktor.ai, aktor.gr, biosar.gr, intrakat.com, etc.) to SSO into the same organization.

## Core Value

Users from any company email domain can sign in via Microsoft SSO and be automatically provisioned, as long as they belong to the organization's Microsoft Entra ID tenant.

## Requirements

### Validated

- ✓ Microsoft SSO authentication flow — existing
- ✓ JIT user provisioning service — existing
- ✓ Microsoft tenant ID stored in auth config — existing
- ✓ Token validation extracts tenant ID — existing

### Active

- [ ] Match org by Microsoft tenant ID during JIT provisioning (not email domain)
- [ ] Look up org via OrgAuthConfig's Microsoft tenantId when user not found
- [ ] Maintain backward compatibility for existing users

### Out of Scope

- Google SSO changes — only Microsoft requested
- UI for managing domain lists — tenant ID approach eliminates need
- DNS domain verification — Microsoft Entra ID already verifies domain ownership
- Multiple tenant support per org — single tenant per org is sufficient

## Context

**Current behavior (broken for multi-domain):**
1. User enters email (e.g., `user@aktor.gr`)
2. App extracts domain `aktor.gr` from email
3. App looks for org with `domain: "aktor.gr"`
4. No match (org has `domain: "aktor.ai"`)
5. JIT provisioning fails → falls back to password auth

**Target behavior:**
1. User enters email (e.g., `user@aktor.gr`)
2. User clicks "Sign in with Microsoft"
3. Microsoft authenticates, returns token with `tid` (tenant ID)
4. App finds org whose Microsoft auth config has matching `tenantId`
5. JIT provisions user under that org

**Key files:**
- `backend/nodejs/apps/src/modules/auth/controller/userAccount.controller.ts` — initAuth method, lines 217-222
- `backend/nodejs/apps/src/modules/auth/services/jit-provisioning.service.ts` — user provisioning
- `backend/nodejs/apps/src/modules/auth/utils/azureAdTokenValidation.ts` — token validation

**Reference PR:** https://github.com/pipeshub-ai/pipeshub-ai/pull/1347 (JIT Account Provisioning)

## Constraints

- **Scope**: Microsoft SSO only — Google SSO unchanged
- **Compatibility**: Must not break existing single-domain orgs
- **Auth flow**: Email-first flow must be preserved (user enters email, then sees SSO button)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Match by tenant ID, not domain | Tenant ID uniquely identifies Entra ID tenant; all 48 domains share one tenant | — Pending |
| No domain list storage | Tenant ID eliminates need to store/manage domain lists | — Pending |
| Microsoft only | User specified; Google SSO works differently | — Pending |

---
*Last updated: 2026-01-31 after initialization*

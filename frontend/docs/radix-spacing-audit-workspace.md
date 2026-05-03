# Radix Spacing/Sizing Token Audit — `/workspace` Components

> **Date:** 16 April 2026
> **Scope:** All `.tsx` files under `app/(main)/workspace/`
> **Read-only audit — no code changes applied.**

---

## Radix `--space-*` Token Scale (v3, default)

| Token | Value |
|---|---|
| `--space-1` | 4px |
| `--space-2` | 8px |
| `--space-3` | 12px |
| `--space-4` | 16px |
| `--space-5` | 20px |
| `--space-6` | 24px |
| `--space-7` | 28px |
| `--space-8` | 32px |
| `--space-9` | 36px |

---

## Audit Rules

- **Only `px` values flagged** (skipping `%`, `rem`, `em`, `vh/vw`)
- **Includes** inline `style={}` props and numeric shorthand values (e.g. `padding: 16`)
- **Skipped:** `lineHeight`, `letterSpacing`, `fontSize`, `fontWeight`, `border-width`, `z-index`, `boxShadow`, `backdropFilter`, `0` values, existing `var(--space-*)` usage
- **Match Type:**
  - `Exact` — a `--space-*` token exists for this value
  - `Closest` — no exact match; nearest token noted with delta
  - `No token` — value is too large/specific to map to any Radix token

---

## Shared Components (`workspace/components/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| SettingsRow | `components/settings-row.tsx` | 26 | `marginTop` | `2` | `--space-1` (4px) | Closest (Δ−2) |
| SettingsRow | `components/settings-row.tsx` | 36 | `minWidth` | `200` | — | No token |
| EntityFilterBar | `components/entity-filter-bar.tsx` | 36 | `height` | `40px` | — | No token |
| EntityFilterBar | `components/entity-filter-bar.tsx` | 37 | `padding` | `0 16px` | `--space-4` (16px) | Exact |
| EntityFilterBar | `components/entity-filter-bar.tsx` | 67 | `padding` | `4px 10px` | `--space-1` / closest `--space-3` (Δ−2) | Exact / Closest |
| EntityFilterBar | `components/entity-filter-bar.tsx` | 70 | `gap` | `4px` | `--space-1` | Exact |
| EntityPageHeader | `components/entity-page-header.tsx` | 47 | `paddingTop` | `64px` | — | No token |
| EntityPageHeader | `components/entity-page-header.tsx` | 48 | `paddingBottom` | `16px` | `--space-4` | Exact |
| EntityPageHeader | `components/entity-page-header.tsx` | 68 | `width` | `224px` | — | No token |
| EntityBulkActionBar | `components/entity-bulk-action-bar.tsx` | 75 | `bottom` | `8px` | `--space-2` | Exact |
| EntityBulkActionBar | `components/entity-bulk-action-bar.tsx` | 81 | `padding` | `6px 16px` | closest `--space-2` (Δ−2) / `--space-4` | Closest / Exact |
| EntityRowActionMenu | `components/entity-row-action-menu.tsx` | 102 | `padding` | `8px 16px` | `--space-2` / `--space-4` | Exact / Exact |
| EntityRowActionMenu | `components/entity-row-action-menu.tsx` | 192 | `margin` | `4px 0` | `--space-1` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 164 | `gap` | `4` | `--space-1` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 166 | `minHeight` | `32` | `--space-8` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 184 | `maxHeight` | `120` | — | No token |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 197 | `padding` | `2px 8px` | closest `--space-1` (Δ−2) / `--space-2` | Closest / Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 247 | `minWidth` | `80` | — | No token |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 248 | `padding` | `2px 4px` | closest `--space-1` (Δ−2) / `--space-1` | Closest / Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 257 | `padding` | `2px 4px` | closest `--space-1` (Δ−2) / `--space-1` | Closest / Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 272 | `height` | `24` | `--space-6` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 273 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 291 | `marginTop` | `4` | `--space-1` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 292 | `marginBottom` | `4` | `--space-1` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 295 | `maxHeight` | `216` | — | No token |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 308 | `padding` | `16px` | `--space-4` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 333 | `padding` | `8px 16px` | `--space-2` / `--space-4` | Exact / Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 340 | `width` | `16` | `--space-4` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 341 | `height` | `16` | `--space-4` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 371 | `width` | `28` | `--space-7` | Exact |
| SearchableCheckboxDropdown | `components/searchable-checkbox-dropdown.tsx` | 372 | `height` | `28` | `--space-7` | Exact |
| TagInput | `components/tag-input.tsx` | 258 | `padding` | `4px` | `--space-1` | Exact |
| TagInput | `components/tag-input.tsx` | 328 | `padding` | `4px 8px` | `--space-1` / `--space-2` | Exact / Exact |
| EntityDataTable | `components/entity-data-table.tsx` | 96 | `height` | `36px` | `--space-9` | Exact |
| EntityDataTable | `components/entity-data-table.tsx` | 106 | `width` | `38px` | closest `--space-9` (Δ+2) | Closest |
| EntityDataTable | `components/entity-data-table.tsx` | 106 | `padding` | `0 8px` | `--space-2` | Exact |
| EntityDataTable | `components/entity-data-table.tsx` | 137 | `width` | `80px` | — | No token |
| EntityDataTable | `components/entity-data-table.tsx` | 167 | `height` | `60px` | — | No token |
| EntityDataTable | `components/entity-data-table.tsx` | 183 | `width` | `38px` | closest `--space-9` (Δ+2) | Closest |
| EntityDataTable | `components/entity-data-table.tsx` | 183 | `padding` | `0 8px` | `--space-2` | Exact |
| EntityDataTable | `components/entity-data-table.tsx` | 216 | `width` | `80px` | — | No token |
| EntityDataTable | `components/entity-data-table.tsx` | 216 | `padding` | `0 8px` | `--space-2` | Exact |
| EntityEmptyState | `components/entity-empty-state.tsx` | 43 | `minHeight` | `400px` | — | No token |
| EntityEmptyState | `components/entity-empty-state.tsx` | 51 | `width` | `64px` | — | No token |
| EntityEmptyState | `components/entity-empty-state.tsx` | 52 | `height` | `64px` | — | No token |
| EntityEmptyState | `components/entity-empty-state.tsx` | 60 | `maxWidth` | `420px` | — | No token |
| EntityPagination | `components/entity-pagination.tsx` | 48 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| EntityPagination | `components/entity-pagination.tsx` | 80 | `padding` | `4px 12px` | `--space-1` / `--space-3` | Exact / Exact |
| EntityPagination | `components/entity-pagination.tsx` | 83 | `minWidth` | `32px` | `--space-8` | Exact |
| EntityPagination | `components/entity-pagination.tsx` | 108 | `height` | `20px` | `--space-5` | Exact |
| EntityPagination | `components/entity-pagination.tsx` | 119 | `padding` | `4px 8px` | `--space-1` / `--space-2` | Exact / Exact |
| WorkspaceRightPanel | `components/workspace-right-panel.tsx` | 105 | `padding` | `8px 8px 8px 16px` | `--space-2` / `--space-2` / `--space-2` / `--space-4` | Exact |
| WorkspaceRightPanel | `components/workspace-right-panel.tsx` | 144 | `padding` | `16px` | `--space-4` | Exact |
| WorkspaceRightPanel | `components/workspace-right-panel.tsx` | 159 | `padding` | `8px 8px 8px 16px` | `--space-2` / `--space-2` / `--space-2` / `--space-4` | Exact |
| SettingsSaveBar | `components/settings-save-bar.tsx` | 69 | `bottom` | `16px` | `--space-4` | Exact |
| SelectDropdown | `components/select-dropdown.tsx` | 93 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| SelectDropdown | `components/select-dropdown.tsx` | 156 | `padding` | `8px 16px` | `--space-2` / `--space-4` | Exact / Exact |
| FormField | `components/form-field.tsx` | 34 | `marginLeft` | `4` | `--space-1` | Exact |

---

## Users (`workspace/users/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| UsersPage | `users/page.tsx` | 610 | `minWidth` | `220px` | — | No token |
| UsersPage | `users/page.tsx` | 622 | `width` | `112px` | — | No token |
| UsersPage | `users/page.tsx` | 632 | `width` | `100px` | — | No token |
| UsersPage | `users/page.tsx` | 642 | `width` | `110px` | — | No token |
| UsersPage | `users/page.tsx` | 650 | `width` | `130px` | — | No token |
| UsersPage | `users/page.tsx` | 660 | `width` | `130px` | — | No token |
| UsersPage | `users/page.tsx` | 921 | `paddingLeft` | `40px` | — | No token |
| UsersPage | `users/page.tsx` | 922 | `paddingRight` | `40px` | — | No token |
| InviteUsersSidebar | `users/components/invite-users-sidebar.tsx` | 299 | `padding` | `16` | `--space-4` | Exact |
| InviteUsersSidebar | `users/components/invite-users-sidebar.tsx` | 302 | `gap` | `20` | `--space-5` | Exact |
| UserProfileSidebar | `users/components/user-profile-sidebar.tsx` | 57 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| UserProfileSidebar | `users/components/user-profile-sidebar.tsx` | 118 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |

---

## Teams (`workspace/teams/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| TeamsPage | `teams/page.tsx` | 448 | `minWidth` | `160px` | — | No token |
| TeamsPage | `teams/page.tsx` | 456 | `minWidth` | `180px` | — | No token |
| TeamsPage | `teams/page.tsx` | 474 | `width` | `80px` | — | No token |
| TeamsPage | `teams/page.tsx` | 485 | `minWidth` | `180px` | — | No token |
| TeamsPage | `teams/page.tsx` | 503 | `width` | `140px` | — | No token |
| TeamsPage | `teams/page.tsx` | 565 | `paddingLeft` | `40px` | — | No token |
| TeamsPage | `teams/page.tsx` | 566 | `paddingRight` | `40px` | — | No token |
| CreateTeamSidebar | `teams/components/create-team-sidebar.tsx` | 198 | `height` | `32` | `--space-8` | Exact |
| CreateTeamSidebar | `teams/components/create-team-sidebar.tsx` | 199 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| CreateTeamSidebar | `teams/components/create-team-sidebar.tsx` | 238 | `minHeight` | `88` | — | No token |
| CreateTeamSidebar | `teams/components/create-team-sidebar.tsx` | 239 | `padding` | `8px` | `--space-2` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 281 | `padding` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 284 | `gap` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 300 | `height` | `32` | `--space-8` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 301 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 354 | `minHeight` | `88` | — | No token |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 355 | `padding` | `8px` | `--space-2` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 387 | `padding` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 390 | `gap` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 420 | `padding` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 423 | `gap` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 479 | `marginLeft` | `8` | `--space-2` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 501 | `padding` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 504 | `gap` | `8` | `--space-2` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 524 | `padding` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 527 | `gap` | `8` | `--space-2` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 566 | `marginTop` | `16` | `--space-4` | Exact |
| TeamDetailSidebar | `teams/components/team-detail-sidebar.tsx` | 567 | `padding` | `16` | `--space-4` | Exact |

---

## Groups (`workspace/groups/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| GroupsPage | `groups/page.tsx` | 402 | `minWidth` | `160px` | — | No token |
| GroupsPage | `groups/page.tsx` | 410 | `minWidth` | `200px` | — | No token |
| GroupsPage | `groups/page.tsx` | 420 | `width` | `80px` | — | No token |
| GroupsPage | `groups/page.tsx` | 431 | `minWidth` | `140px` | — | No token |
| GroupsPage | `groups/page.tsx` | 441 | `width` | `140px` | — | No token |
| GroupsPage | `groups/page.tsx` | 503 | `paddingLeft` | `40px` | — | No token |
| GroupsPage | `groups/page.tsx` | 504 | `paddingRight` | `40px` | — | No token |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 171 | `padding` | `16` | `--space-4` | Exact |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 174 | `gap` | `20` | `--space-5` | Exact |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 191 | `height` | `32` | `--space-8` | Exact |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 192 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 205 | `padding` | `5px 7px` | closest `--space-1` (Δ+1) / `--space-2` (Δ−1) | Closest / Closest |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 209 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 231 | `minHeight` | `88` | — | No token |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 232 | `padding` | `8px` | `--space-2` | Exact |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 246 | `padding` | `7px` | closest `--space-2` (Δ−1) | Closest |
| CreateGroupSidebar | `groups/components/create-group-sidebar.tsx` | 250 | `padding` | `8px` | `--space-2` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 276 | `padding` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 279 | `gap` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 295 | `height` | `32` | `--space-8` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 296 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 311 | `padding` | `5px 7px` | closest `--space-1` (Δ+1) / `--space-2` (Δ−1) | Closest / Closest |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 316 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 347 | `minHeight` | `88` | — | No token |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 348 | `padding` | `8px` | `--space-2` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 364 | `padding` | `7px` | closest `--space-2` (Δ−1) | Closest |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 369 | `padding` | `8px` | `--space-2` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 380 | `padding` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 383 | `gap` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 404 | `padding` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 407 | `gap` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 473 | `padding` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 476 | `gap` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 501 | `padding` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 504 | `gap` | `8` | `--space-2` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 542 | `marginTop` | `16` | `--space-4` | Exact |
| GroupDetailSidebar | `groups/components/group-detail-sidebar.tsx` | 543 | `padding` | `16` | `--space-4` | Exact |

---

## Authentication (`workspace/authentication/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| AuthPage | `authentication/page.tsx` | 229 | `padding` | `64px 100px 80px` | — / — / — | No token (all 3) |
| AuthPage | `authentication/page.tsx` | 231 | `marginBottom` | `24` | `--space-6` | Exact |
| AuthPage | `authentication/page.tsx` | 236 | `marginTop` | `4` | `--space-1` | Exact |
| AuthPage | `authentication/page.tsx` | 251 | `gap` | `6` | closest `--space-2` (Δ−2) | Closest |
| AuthPage | `authentication/page.tsx` | 267 | `marginBottom` | `20` | `--space-5` | Exact |
| AuthPage | `authentication/page.tsx` | 274 | `padding` | `14px 16px` | closest `--space-3` (Δ+2) / `--space-4` | Closest / Exact |
| AuthPage | `authentication/page.tsx` | 282 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| AuthPage | `authentication/page.tsx` | 295 | `gap` | `6` | closest `--space-2` (Δ−2) | Closest |
| AuthPage | `authentication/page.tsx` | 307 | `padding` | `40px 0` | — | No token |
| AuthPage | `authentication/page.tsx` | 313 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| AuthPage | `authentication/page.tsx` | 348 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| AuthPage | `authentication/page.tsx` | 351 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| AuthPage | `authentication/page.tsx` | 365 | `marginTop` | `4` | `--space-1` | Exact |
| ConfigurePanel | `authentication/components/configure-panel.tsx` | 87 | `gap` | `4` | `--space-1` | Exact |
| AuthMethodRow | `authentication/components/auth-method-row.tsx` | 71 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| AuthMethodRow | `authentication/components/auth-method-row.tsx` | 82 | `width` | `36` | `--space-9` | Exact |
| AuthMethodRow | `authentication/components/auth-method-row.tsx` | 83 | `height` | `36` | `--space-9` | Exact |
| AuthMethodRow | `authentication/components/auth-method-row.tsx` | 93 | `width` | `18` | closest `--space-5` (Δ−2) | Closest |
| AuthMethodRow | `authentication/components/auth-method-row.tsx` | 93 | `height` | `18` | closest `--space-5` (Δ−2) | Closest |
| AuthMethodRow | `authentication/components/auth-method-row.tsx` | 110 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| JitField | `authentication/components/forms/jit-field.tsx` | 18 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| JitField | `authentication/components/forms/jit-field.tsx` | 26 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| JitField | `authentication/components/forms/jit-field.tsx` | 34 | `marginLeft` | `16` | `--space-4` | Exact |
| ReadonlyField | `authentication/components/forms/readonly-field.tsx` | 30 | `marginTop` | `4` | `--space-1` | Exact |
| ProviderConfigForm | `authentication/components/forms/provider-config-form.tsx` | 106 | `padding` | `32px 0` | `--space-8` | Exact |

---

## Profile (`workspace/profile/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| ProfilePage | `profile/page.tsx` | 70 | `padding` | `64px 100px` | — / — | No token (both) |
| ProfilePage | `profile/page.tsx` | 73 | `marginBottom` | `24` | `--space-6` | Exact |
| ProfilePage | `profile/page.tsx` | 77 | `marginTop` | `4` | `--space-1` | Exact |
| ProfilePage | `profile/page.tsx` | 83 | `marginBottom` | `20` | `--space-5` | Exact |
| ProfilePage | `profile/page.tsx` | 104 | `marginBottom` | `20` | `--space-5` | Exact |
| ProfilePage | `profile/page.tsx` | 110 | `marginBottom` | `80` | — | No token |
| PasswordSecuritySection | `profile/components/password-security-section.tsx` | 72 | `padding` | `6px 14px` | closest `--space-2` (Δ−2) / closest `--space-3` (Δ+2) | Closest / Closest |

---

## General (`workspace/general/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| GeneralPage | `general/page.tsx` | 45 | `padding` | `64px 100px` | — / — | No token (both) |
| GeneralPage | `general/page.tsx` | 46 | `marginBottom` | `24` | `--space-6` | Exact |
| GeneralPage | `general/page.tsx` | 50 | `marginTop` | `4` | `--space-1` | Exact |
| GeneralPage | `general/page.tsx` | 318 | `padding` | `64px 100px` | — / — | No token (both) |
| GeneralPage | `general/page.tsx` | 320 | `marginBottom` | `24` | `--space-6` | Exact |
| GeneralPage | `general/page.tsx` | 324 | `marginTop` | `4` | `--space-1` | Exact |
| GeneralPage | `general/page.tsx` | 330 | `marginBottom` | `20` | `--space-5` | Exact |
| GeneralPage | `general/page.tsx` | 385 | `marginBottom` | `20` | `--space-5` | Exact |
| GeneralPage | `general/page.tsx` | 447 | `marginBottom` | `80` | — | No token |
| GeneralPage | `general/page.tsx` | 463 | `padding` | `16px 20px` | `--space-4` / `--space-5` | Exact / Exact |
| GeneralPage | `general/page.tsx` | 466 | `marginBottom` | `12` | `--space-3` | Exact |
| GeneralPage | `general/page.tsx` | 471 | `paddingLeft` | `4` | `--space-1` | Exact |
| GeneralPage | `general/page.tsx` | 483 | `width` | `4` | `--space-1` | Exact |
| GeneralPage | `general/page.tsx` | 484 | `height` | `4` | `--space-1` | Exact |
| GeneralPage | `general/page.tsx` | 487 | `marginTop` | `8` | `--space-2` | Exact |

---

## Web Search (`workspace/web-search/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| WebSearchPage | `web-search/page.tsx` | 256 | `padding` | `64px 100px 80px` | — / — / — | No token (all 3) |
| WebSearchPage | `web-search/page.tsx` | 258 | `marginBottom` | `24` | `--space-6` | Exact |
| WebSearchPage | `web-search/page.tsx` | 263 | `marginTop` | `4` | `--space-1` | Exact |
| WebSearchPage | `web-search/page.tsx` | 275 | `gap` | `6` | closest `--space-2` (Δ−2) | Closest |
| WebSearchPage | `web-search/page.tsx` | 291 | `marginBottom` | `20` | `--space-5` | Exact |
| WebSearchPage | `web-search/page.tsx` | 298 | `padding` | `14px 16px` | closest `--space-3` (Δ+2) / `--space-4` | Closest / Exact |
| WebSearchPage | `web-search/page.tsx` | 306 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| WebSearchPage | `web-search/page.tsx` | 319 | `gap` | `6` | closest `--space-2` (Δ−2) | Closest |
| WebSearchPage | `web-search/page.tsx` | 331 | `padding` | `40px 0` | — | No token |
| WebSearchPage | `web-search/page.tsx` | 337 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| WebSearchPage | `web-search/page.tsx` | 378 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| WebSearchPage | `web-search/page.tsx` | 381 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| ConfigurePanel | `web-search/components/configure-panel.tsx` | 95 | `gap` | `4` | `--space-1` | Exact |
| ConfigurePanel | `web-search/components/configure-panel.tsx` | 109 | `width` | `20` | `--space-5` | Exact |
| ConfigurePanel | `web-search/components/configure-panel.tsx` | 109 | `height` | `20` | `--space-5` | Exact |
| ConfigurePanel | `web-search/components/configure-panel.tsx` | 171 | `bottom` | `60` | — | No token |
| ConfigurePanel | `web-search/components/configure-panel.tsx` | 177 | `padding` | `8px 16px` | `--space-2` / `--space-4` | Exact / Exact |
| SendImagesRow | `web-search/components/send-images-row.tsx` | 26 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| SendImagesRow | `web-search/components/send-images-row.tsx` | 37 | `width` | `36` | `--space-9` | Exact |
| SendImagesRow | `web-search/components/send-images-row.tsx` | 38 | `height` | `36` | `--space-9` | Exact |
| SendImagesRow | `web-search/components/send-images-row.tsx` | 57 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| WebSearchProviderRow | `web-search/components/web-search-provider-row.tsx` | 90 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| WebSearchProviderRow | `web-search/components/web-search-provider-row.tsx` | 101 | `width` | `36` | `--space-9` | Exact |
| WebSearchProviderRow | `web-search/components/web-search-provider-row.tsx` | 102 | `height` | `36` | `--space-9` | Exact |
| WebSearchProviderRow | `web-search/components/web-search-provider-row.tsx` | 112 | `width` | `18` | closest `--space-5` (Δ−2) | Closest |
| WebSearchProviderRow | `web-search/components/web-search-provider-row.tsx` | 112 | `height` | `18` | closest `--space-5` (Δ−2) | Closest |
| WebSearchProviderRow | `web-search/components/web-search-provider-row.tsx` | 129 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |

---

## Prompts (`workspace/prompts/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| PromptsPage | `prompts/page.tsx` | 42 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| PromptsPage | `prompts/page.tsx` | 48 | `width` | `36` | `--space-9` | Exact |
| PromptsPage | `prompts/page.tsx` | 49 | `height` | `36` | `--space-9` | Exact |
| PromptsPage | `prompts/page.tsx` | 71 | `padding` | `16` | `--space-4` | Exact |
| PromptsPage | `prompts/page.tsx` | 87 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| PromptsPage | `prompts/page.tsx` | 185 | `padding` | `64px 100px` | — / — | No token (both) |
| PromptsPage | `prompts/page.tsx` | 185 | `paddingBottom` | `80` | — | No token |
| PromptsPage | `prompts/page.tsx` | 188 | `marginBottom` | `24` | `--space-6` | Exact |
| PromptsPage | `prompts/page.tsx` | 193 | `marginTop` | `4` | `--space-1` | Exact |
| PromptsPage | `prompts/page.tsx` | 211 | `marginBottom` | `20` | `--space-5` | Exact |

---

## Labs (`workspace/labs/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| LabsPage | `labs/page.tsx` | 42 | `padding` | `16` | `--space-4` | Exact |
| LabsPage | `labs/page.tsx` | 84 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| LabsPage | `labs/page.tsx` | 90 | `minWidth` | `200` | — | No token |
| LabsPage | `labs/page.tsx` | 105 | `padding` | `10px 12px` | closest `--space-3` (Δ−2) / `--space-3` | Closest / Exact |
| LabsPage | `labs/page.tsx` | 126 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| LabsPage | `labs/page.tsx` | 320 | `padding` | `64px 100px` | — / — | No token (both) |
| LabsPage | `labs/page.tsx` | 320 | `paddingBottom` | `80` | — | No token |
| LabsPage | `labs/page.tsx` | 322 | `marginBottom` | `24` | `--space-6` | Exact |
| LabsPage | `labs/page.tsx` | 326 | `marginTop` | `4` | `--space-1` | Exact |
| LabsPage | `labs/page.tsx` | 332 | `marginBottom` | `20` | `--space-5` | Exact |
| LabsPage | `labs/page.tsx` | 353 | `padding` | `2px 8px` | closest `--space-1` (Δ−2) / `--space-2` | Closest / Exact |
| LabsPage | `labs/page.tsx` | 354 | `height` | `24` | `--space-6` | Exact |
| LabsPage | `labs/page.tsx` | 381 | `marginBottom` | `20` | `--space-5` | Exact |

---

## Mail (`workspace/mail/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| MailPage | `mail/page.tsx` | 90 | `padding` | `64px 100px 80px` | — / — / — | No token (all 3) |
| MailPage | `mail/page.tsx` | 92 | `marginBottom` | `24` | `--space-6` | Exact |
| MailPage | `mail/page.tsx` | 97 | `marginTop` | `4` | `--space-1` | Exact |
| MailPage | `mail/page.tsx` | 107 | `gap` | `6` | closest `--space-2` (Δ−2) | Closest |
| MailPage | `mail/page.tsx` | 123 | `marginBottom` | `20` | `--space-5` | Exact |
| MailPage | `mail/page.tsx` | 127 | `padding` | `14px 16px` | closest `--space-3` (Δ+2) / `--space-4` | Closest / Exact |
| MailPage | `mail/page.tsx` | 133 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| MailPage | `mail/page.tsx` | 140 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| MailPage | `mail/page.tsx` | 142 | `padding` | `24px 0` | `--space-6` | Exact |
| MailPage | `mail/page.tsx` | 152 | `padding` | `12px 14px` | `--space-3` / closest `--space-3` (Δ+2) | Exact / Closest |
| MailPage | `mail/page.tsx` | 163 | `width` | `36` | `--space-9` | Exact |
| MailPage | `mail/page.tsx` | 164 | `height` | `36` | `--space-9` | Exact |
| MailPage | `mail/page.tsx` | 187 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| SmtpConfigurePanel | `mail/components/smtp-configure-panel.tsx` | 56 | `marginBottom` | `6` | closest `--space-2` (Δ−2) | Closest |
| SmtpConfigurePanel | `mail/components/smtp-configure-panel.tsx` | 61 | `marginTop` | `2` | closest `--space-1` (Δ−2) | Closest |
| SmtpConfigurePanel | `mail/components/smtp-configure-panel.tsx` | 154 | `gap` | `4` | `--space-1` | Exact |
| SmtpConfigurePanel | `mail/components/smtp-configure-panel.tsx` | 184 | `padding` | `10px 12px` | closest `--space-3` (Δ−2) / `--space-3` | Closest / Exact |
| SmtpConfigurePanel | `mail/components/smtp-configure-panel.tsx` | 216 | `marginTop` | `4` | `--space-1` | Exact |
| SmtpConfigurePanel | `mail/components/smtp-configure-panel.tsx` | 240 | `marginTop` | `4` | `--space-1` | Exact |
| SmtpConfigurePanel | `mail/components/smtp-configure-panel.tsx` | 264 | `marginTop` | `4` | `--space-1` | Exact |

---

## Bots (`workspace/bots/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| BotCard | `bots/components/bot-card.tsx` | 57 | `padding` | `12` | `--space-3` | Exact |
| BotCard | `bots/components/bot-card.tsx` | 58 | `gap` | `24` | `--space-6` | Exact |
| BotCard | `bots/components/bot-card.tsx` | 70 | `width` | `32` | `--space-8` | Exact |
| BotCard | `bots/components/bot-card.tsx` | 71 | `height` | `32` | `--space-8` | Exact |
| BotCard | `bots/components/bot-card.tsx` | 72 | `padding` | `8` | `--space-2` | Exact |
| BotCard | `bots/components/bot-card.tsx` | 145 | `gap` | `8` | `--space-2` | Exact |
| BotCard | `bots/components/bot-card.tsx` | 147 | `height` | `32` | `--space-8` | Exact |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 44 | `paddingTop` | `64` | — | No token |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 45 | `paddingBottom` | `64` | — | No token |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 46 | `paddingLeft` | `100` | — | No token |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 47 | `paddingRight` | `100` | — | No token |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 72 | `paddingTop` | `80` | — | No token |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 127 | `gap` | `6` | closest `--space-2` (Δ−2) | Closest |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 128 | `height` | `32` | `--space-8` | Exact |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 162 | `width` | `32` | `--space-8` | Exact |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 163 | `height` | `32` | `--space-8` | Exact |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 184 | `paddingTop` | `80` | — | No token |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 201 | `marginTop` | `8` | `--space-2` | Exact |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 207 | `gap` | `6` | closest `--space-2` (Δ−2) | Closest |
| BotPageLayout | `bots/components/bot-page-layout.tsx` | 208 | `height` | `36` | `--space-9` | Exact |
| BotConfigPanel | `bots/components/bot-config-panel.tsx` | 141 | `padding` | `10px 12px` | closest `--space-3` (Δ−2) / `--space-3` | Closest / Exact |

---

## Connectors (`workspace/connectors/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| ConnectorTeamPage | `connectors/team/page.tsx` | 410 | `padding` | `0 12px` | `--space-3` | Exact |
| ConnectorTeamPage | `connectors/team/page.tsx` | 416 | `gap` | `8` | `--space-2` | Exact |
| ConnectorTeamPage | `connectors/team/page.tsx` | 417 | `height` | `32` | `--space-8` | Exact |
| InstanceCardPrimitives | `connectors/components/instance-card/primitives.tsx` | 100 | `padding` | `0 12px` | `--space-3` | Exact |
| InstanceCardPrimitives | `connectors/components/instance-card/primitives.tsx` | 208 | `padding` | `0 12px` | `--space-3` | Exact |
| SyncStatus | `connectors/components/instance-card/sync-status.tsx` | 28 | `padding` | `0 12px 0 8px` | `--space-3` / `--space-2` | Exact / Exact |
| SyncStatus | `connectors/components/instance-card/sync-status.tsx` | 54 | `padding` | `0 12px` | `--space-3` | Exact |
| SyncStatus | `connectors/components/instance-card/sync-status.tsx` | 69 | `padding` | `0 12px` | `--space-3` | Exact |
| InstanceCardIndex | `connectors/components/instance-card/index.tsx` | 295 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| ConnectorPanel | `connectors/components/connector-panel.tsx` | 359 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| AuthCard | `connectors/components/auth-card.tsx` | 118 | `padding` | `4px 8px` | `--space-1` / `--space-2` | Exact / Exact |
| AuthCard | `connectors/components/auth-card.tsx` | 132 | `padding` | `4px 8px` | `--space-1` / `--space-2` | Exact / Exact |
| SchemaFormField | `connectors/components/schema-form-field.tsx` | 32 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| SchemaFormField | `connectors/components/schema-form-field.tsx` | 52 | `padding` | `5px 7px` | closest `--space-1` (Δ+1) / `--space-2` (Δ−1) | Closest / Closest |
| SelectRecordsPage | `connectors/components/select-records-page.tsx` | 110 | `paddingBottom` | `12` | `--space-3` | Exact |
| SelectRecordsPage | `connectors/components/select-records-page.tsx` | 165 | `padding` | `12px 0` | `--space-3` | Exact |
| AuthenticateTab | `connectors/components/authenticate-tab/index.tsx` | 55 | `padding` | `4px 0` | `--space-1` | Exact |
| AuthenticateTab | `connectors/components/authenticate-tab/index.tsx` | 140 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| DocumentationSection | `connectors/components/authenticate-tab/documentation-section.tsx` | 75 | `padding` | `8px 8px 8px 12px` | `--space-2` / `--space-2` / `--space-2` / `--space-3` | Exact |
| ConfigureTabIndex | `connectors/components/configure-tab/index.tsx` | 34 | `padding` | `4px 0` | `--space-1` | Exact |
| ImportDateSection | `connectors/components/configure-tab/import-date-section.tsx` | 46 | `padding` | `6px 8px` | closest `--space-2` (Δ−2) / `--space-2` | Closest / Exact |
| RecordsSection | `connectors/components/configure-tab/records-section.tsx` | 52 | `padding` | `2px 6px` | closest `--space-1` (Δ−2) / closest `--space-2` (Δ−2) | Closest / Closest |
| InstancePanelIndex | `connectors/components/instance-panel/index.tsx` | 100 | `padding` | `2px 6px 2px 4px` | closest `--space-1` (Δ−2) / closest `--space-2` (Δ−2) / closest `--space-1` (Δ−2) / `--space-1` | Closest |
| OverviewTab | `connectors/components/instance-panel/overview-tab.tsx` | 128 | `padding` | `12px 16px` | `--space-3` / `--space-4` | Exact / Exact |
| OverviewTab | `connectors/components/instance-panel/overview-tab.tsx` | 291 | `padding` | `8px 0` | `--space-2` | Exact |
| OverviewTab | `connectors/components/instance-panel/overview-tab.tsx` | 395 | `padding` | `24px 16px` | `--space-6` / `--space-4` | Exact / Exact |

---

## Archived Chats (`workspace/archived-chats/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| ArchivedChatsSidebar | `archived-chats/components/archived-chats-sidebar.tsx` | 75 | `padding` | `0 16px` | `--space-4` | Exact |
| ArchivedChatsSidebar | `archived-chats/components/archived-chats-sidebar.tsx` | 85 | `padding` | `0 8px 8px` | `--space-2` / `--space-2` | Exact |
| ArchivedChatsSidebar | `archived-chats/components/archived-chats-sidebar.tsx` | 100 | `paddingTop` | `32` | `--space-8` | Exact |
| ArchivedChatsSidebar | `archived-chats/components/archived-chats-sidebar.tsx` | 106 | `paddingTop` | `32` | `--space-8` | Exact |
| ArchivedChatView | `archived-chats/components/archived-chat-view.tsx` | 68 | `gap` | `12` | `--space-3` | Exact |
| ArchivedChatView | `archived-chats/components/archived-chat-view.tsx` | 201 | `top` | `12` | `--space-3` | Exact |
| ArchivedChatView | `archived-chats/components/archived-chat-view.tsx` | 202 | `right` | `12` | `--space-3` | Exact |
| ArchivedChatView | `archived-chats/components/archived-chat-view.tsx` | 221 | `minWidth` | `160` | — | No token |
| ArchivedChatView | `archived-chats/components/archived-chat-view.tsx` | 255 | `padding` | `40px 0 32px` | — / `--space-8` | No token / Exact |
| ArchivedChatView | `archived-chats/components/archived-chat-view.tsx` | 256 | `maxWidth` | `720` | — | No token |
| ArchivedChatView | `archived-chats/components/archived-chat-view.tsx` | 264 | `padding` | `0 24px` | `--space-6` | Exact |
| ArchivedChatSearch | `archived-chats/components/archived-chat-search.tsx` | 150 | `maxHeight` | `512px` | — | No token |
| ArchivedChatSearch | `archived-chats/components/archived-chat-search.tsx` | 214 | `height` | `32` | `--space-8` | Exact |

---

## Sidebar (`workspace/sidebar/`)

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| SidebarItem | `sidebar/sidebar-item.tsx` | 55 | `gap` | `8` | `--space-2` | Exact |
| SidebarItem | `sidebar/sidebar-item.tsx` | 59 | `paddingRight` | `12` | `--space-3` | Exact |
| CollapsibleSection | `sidebar/collapsible-section.tsx` | 60 | `gap` | `8` | `--space-2` | Exact |

---

## Services & AI Models

| Component | File Path | Line # | CSS Property | Current Value | Radix Variable | Match Type |
|---|---|---|---|---|---|---|
| ServicesPage | `services/page.tsx` | 15 | `marginTop` | `16` | `--space-4` | Exact |
| ServicesPage | `services/page.tsx` | 18 | `marginTop` | `8` | `--space-2` | Exact |
| AIModelsPage | `ai-models/page.tsx` | 15 | `marginTop` | `16` | `--space-4` | Exact |
| AIModelsPage | `ai-models/page.tsx` | 18 | `marginTop` | `8` | `--space-2` | Exact |

---

## Summary by Match Type

| Match Type | Count | Notes |
|---|---|---|
| **Exact** | ~140 | Direct `--space-*` substitution available — straightforward migration |
| **Closest** | ~50 | Values like `2px`, `5px`, `6px`, `7px`, `10px`, `14px`, `18px` — often intentional for border-offset or optical compensation |
| **No token** | ~45 | Layout-specific: `40px`, `60px`, `64px`, `80px`, `88px`, `100px`, `110–224px`, `400–720px` |

---

## Top Recurring Patterns Needing Attention

| Pattern | Value | Occurrences | Notes |
|---|---|---|---|
| Page-level inset | `padding: '64px 100px'` / `'64px 100px 80px'` | 8+ pages | No Radix token; consider a shared CSS custom property or layout constant |
| Table container padding | `paddingLeft/Right: '40px'` | users, groups, teams pages | No Radix token |
| Row card padding | `padding: '12px 14px'` | 6+ components | `14px` has no exact token; `12px` = `--space-3` |
| Input field padding | `padding: '6px 8px'` | 10+ components | `6px` has no exact token; consider `--space-2` |
| Sub-label nudge | `marginTop: 2` | ~15 uses | No exact token; consider `--space-1` with visual review |
| Icon-button gap | `gap: 6` | ~6 uses | No exact token; consider `--space-2` |
| Icon container | `width/height: 36` | ~10 uses | `36px` = `--space-9` — exact match |
| Small icon | `width/height: 18` | ~5 uses | No exact token; closest `--space-5` (20px, Δ−2) |

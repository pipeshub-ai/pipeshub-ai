// ========================================
// Workspace-wide constants
// ========================================

import { GroupType } from './groups/types';

// ── Group types ──────────────────────────────────────────────────

/**
 * Group kinds from the userGroups API — enum lives in `./groups/types`.
 * - admin    : system group — members have the Admin role
 * - everyone : system group — every workspace member is in this group
 * - standard : user-created group (non-system)
 * - custom   : user-created group (non-system)
 */

/**
 * System-managed group types that cannot be deleted or displayed in
 * user-facing group pickers.
 */
export const SYSTEM_GROUP_TYPES: readonly GroupType[] = [
  GroupType.ADMIN,
  GroupType.EVERYONE,
];

/**
 * User-created group types that can be created, edited, and deleted.
 */
export const NON_SYSTEM_GROUP_TYPES: readonly GroupType[] = [
  GroupType.STANDARD,
  GroupType.CUSTOM,
];

// ── User roles ───────────────────────────────────────────────────

/**
 * Role labels shown in the UI and used when comparing / setting roles.
 * Role is derived server-side from group membership (admin group → Admin).
 */
export const USER_ROLES = {
  ADMIN: 'Admin',
  MEMBER: 'Member',
  GUEST: 'Guest',
} as const;

export type UserRoleValue = (typeof USER_ROLES)[keyof typeof USER_ROLES];

// ── Role option definitions (shared across components) ───────────

/**
 * Shape shared by SelectDropdown (invite sidebar) and SubMenuRadioOption
 * (row action role picker). Both use { value, label, description }.
 */
export interface RoleOptionDef {
  value: UserRoleValue;
  label: string;
  description: string;
}

/**
 * All available roles with their descriptions.
 * Used by the "Change Role" row action popover.
 *
 * NOTE: labels and descriptions here are static defaults.
 * Components using i18n should map over these and override
 * label / description with translated strings.
 */
export const ALL_ROLE_OPTIONS: RoleOptionDef[] = [
  {
    value: USER_ROLES.ADMIN,
    label: 'Admin',
    description: 'Access everything and perform all the actions in the workspace',
  },
  {
    value: USER_ROLES.MEMBER,
    label: 'Member',
    description: 'Access everything and perform all actions except administrative',
  },
  {
    value: USER_ROLES.GUEST,
    label: 'Guest',
    description: 'Can only view data',
  },
];

/**
 * Roles offered when inviting a new user (no Guest).
 * Used by the Invite User sidebar's "Assign Role" dropdown.
 */
export const INVITE_ROLE_OPTIONS: RoleOptionDef[] = ALL_ROLE_OPTIONS.filter(
  (r) => r.value !== USER_ROLES.GUEST
);

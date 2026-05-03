// ========================================
// Workspace-wide constants
// ========================================

// ── Group types ──────────────────────────────────────────────────

/**
 * Group type values as returned by the API.
 * - admin    : system group — members of this group have the Admin role
 * - everyone : system group — every workspace member is automatically in this group
 * - standard : user-created group (non-system)
 * - custom   : user-created group (non-system)
 */
export const GROUP_TYPES = {
  ADMIN: 'admin',
  EVERYONE: 'everyone',
  STANDARD: 'standard',
  CUSTOM: 'custom',
} as const;

export type GroupTypeValue = (typeof GROUP_TYPES)[keyof typeof GROUP_TYPES];

/**
 * System-managed group types that cannot be deleted or displayed in
 * user-facing group pickers.
 */
export const SYSTEM_GROUP_TYPES: readonly GroupTypeValue[] = [
  GROUP_TYPES.ADMIN,
  GROUP_TYPES.EVERYONE,
];

/**
 * User-created group types that can be created, edited, and deleted.
 */
export const NON_SYSTEM_GROUP_TYPES: readonly GroupTypeValue[] = [
  GROUP_TYPES.STANDARD,
  GROUP_TYPES.CUSTOM,
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

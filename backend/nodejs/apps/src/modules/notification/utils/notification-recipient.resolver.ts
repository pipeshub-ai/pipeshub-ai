import { getGroupRepository } from '../../../libs/repositories/group';
import { isValidId } from '../../../libs/repositories/ids';
import { findOrgAdminUserIds } from '../../user_management/services/user-admin.service';

/** An org id as a string or a driver id; callers outside the stores pass the string. */
type OrgIdLike = string | { toString(): string };

const SUPPORTED_GROUP_ROLE_TYPES = new Set(['standard', 'everyone', 'custom']);

function normalizeRole(role: string): string {
  return role.trim().toLowerCase();
}

/**
 * Resolves Kafka `recipientRoles` entries to user IDs.
 * - "admin" → Users with role === 'admin'
 * - other names → UserGroup.type match (standard / everyone / custom)
 */
export async function resolveRoleRecipientUserIds(
  orgId: OrgIdLike,
  recipientRoles: string[],
): Promise<string[]> {
  const validRoles = [
    ...new Set(
      recipientRoles
        .filter((r): r is string => typeof r === 'string' && r.trim() !== '')
        .map(normalizeRole),
    ),
  ];

  if (validRoles.length === 0) {
    return [];
  }

  const userIdStrings = new Set<string>();

  if (validRoles.includes('admin')) {
    const adminUserIds = await findOrgAdminUserIds(orgId);
    for (const userId of adminUserIds) {
      userIdStrings.add(userId);
    }
  }

  const groupRoles = validRoles.filter((r) => SUPPORTED_GROUP_ROLE_TYPES.has(r));
  if (groupRoles.length > 0) {
    const memberIds = await getGroupRepository().memberIdsOfTypes(
      String(orgId),
      groupRoles,
    );
    for (const asString of memberIds) {
      if (isValidId(asString)) {
        userIdStrings.add(asString);
      }
    }
  }

  return [...userIdStrings];
}

function parseRecipientUserIds(raw: unknown): string[] {
  const values = Array.isArray(raw) ? raw : raw != null ? [raw] : [];
  const ids = new Set<string>();
  for (const entry of values) {
    const asString = String(entry);
    if (isValidId(asString)) {
      ids.add(asString);
    }
  }
  return [...ids];
}

/**
 * Merges explicit recipient user IDs and users resolved from recipient roles (deduped).
 */
export async function resolveNotificationRecipientUserIds(
  orgId: OrgIdLike,
  recipientUserIds: unknown,
  recipientRoles: unknown,
): Promise<string[]> {
  const direct = parseRecipientUserIds(recipientUserIds);
  const roleNames = Array.isArray(recipientRoles)
    ? recipientRoles.filter((r): r is string => typeof r === 'string')
    : [];
  const fromRoles =
    roleNames.length > 0 ? await resolveRoleRecipientUserIds(orgId, roleNames) : [];

  const merged = new Set<string>();
  for (const id of [...direct, ...fromRoles]) {
    merged.add(id);
  }
  return [...merged];
}

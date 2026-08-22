import mongoose from 'mongoose';
import { UserGroups } from '../../user_management/schema/userGroup.schema';
import { findOrgAdminUserIds } from '../../user_management/services/user-admin.service';

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
  orgId: mongoose.Types.ObjectId,
  recipientRoles: string[],
): Promise<mongoose.Types.ObjectId[]> {
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
    const groups = await UserGroups.find({
      orgId,
      type: { $in: groupRoles },
      isDeleted: { $ne: true },
    })
      .select('users')
      .lean();

    for (const group of groups) {
      const users = (group as { users?: unknown[] }).users ?? [];
      for (const userId of users) {
        const asString = String(userId);
        if (mongoose.isValidObjectId(asString)) {
          userIdStrings.add(asString);
        }
      }
    }
  }

  return [...userIdStrings].map((id) => new mongoose.Types.ObjectId(id));
}

function parseRecipientUserIds(raw: unknown): mongoose.Types.ObjectId[] {
  const values = Array.isArray(raw) ? raw : raw != null ? [raw] : [];
  const ids = new Set<string>();
  for (const entry of values) {
    const asString = String(entry);
    if (mongoose.isValidObjectId(asString)) {
      ids.add(asString);
    }
  }
  return [...ids].map((id) => new mongoose.Types.ObjectId(id));
}

/**
 * Merges explicit recipient user IDs and users resolved from recipient roles (deduped).
 */
export async function resolveNotificationRecipientUserIds(
  orgId: mongoose.Types.ObjectId,
  recipientUserIds: unknown,
  recipientRoles: unknown,
): Promise<mongoose.Types.ObjectId[]> {
  const direct = parseRecipientUserIds(recipientUserIds);
  const roleNames = Array.isArray(recipientRoles)
    ? recipientRoles.filter((r): r is string => typeof r === 'string')
    : [];
  const fromRoles =
    roleNames.length > 0 ? await resolveRoleRecipientUserIds(orgId, roleNames) : [];

  const merged = new Set<string>();
  for (const oid of [...direct, ...fromRoles]) {
    merged.add(oid.toString());
  }
  return [...merged].map((id) => new mongoose.Types.ObjectId(id));
}

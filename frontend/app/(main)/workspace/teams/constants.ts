import type { SelectOption } from '../components';
import type { TeamMemberRole } from './types';

// ========================================
// Role options
// ========================================

export const ROLE_OPTIONS: SelectOption[] = [
  { value: 'READER', label: 'Reader' },
  { value: 'WRITER', label: 'Writer' },
  { value: 'OWNER', label: 'Owner' },
];

/**
 * Labels + descriptions used by role pickers in team contexts (Create Team,
 * Edit Team). Team-neutral wording — do not mention collections or specific
 * resources here.
 */
export const TEAM_ROLE_LABELS: Record<
  TeamMemberRole,
  { label: string; description: string }
> = {
  OWNER: { label: 'Owner', description: 'Full control over the team and its resources' },
  WRITER: { label: 'Writer', description: 'Can view and edit team resources' },
  READER: { label: 'Reader', description: 'Can view team resources' },
};

/** Coerce API/pending role values to a known team role (avoids RoleDropdownMenu crashes). */
export function normalizeTeamMemberRole(
  role: unknown,
  isOwner?: boolean
): TeamMemberRole {
  const upper = typeof role === 'string' ? role.trim().toUpperCase() : '';
  if (upper === 'OWNER' || upper === 'WRITER' || upper === 'READER') {
    return upper;
  }
  return isOwner ? 'OWNER' : 'READER';
}

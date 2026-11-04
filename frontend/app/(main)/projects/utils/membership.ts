import type { ProjectMember } from '@/chat/project-types';

/**
 * Whether the user holds their own member row. Leaving removes only that row,
 * so someone who reaches the project through the organization or a team has
 * nothing to leave.
 */
export function hasDirectMembership(members: ProjectMember[] | undefined, userId: string | undefined): boolean {
  if (!userId) return false;
  return (members ?? []).some((m) => m.principalType === 'user' && m.principalId === userId);
}

import type { Snapshot, TeamMembership } from './resolution';

/** The graph team role that administers a team (Python `entity.py`: only an OWNER may change a team). */
export const TEAM_ADMIN_ROLE = 'OWNER';

export const teamScope = (teamId: string): string => `team:${teamId}`;
export const teamOf = (ref: string): string | null =>
  ref.startsWith('team:') && ref.length > 5 ? ref.slice(5) : null;

/**
 * The team layer of a run (02-lld §3.1): the agent's owner team, never the
 * actor's teams. Joining a team must not reduce a person's authority, and a
 * meet over several teams is hard to explain; the actor's teams only widen
 * what they can see.
 */
export const teamLayerScope = (
  snapshot: Pick<Snapshot, 'agentKey' | 'agentOwnerTeam'>,
): string | null =>
  snapshot.agentKey === null || snapshot.agentOwnerTeam === null
    ? null
    : teamScope(snapshot.agentOwnerTeam);

/** Whether these memberships make the person an admin of the team. */
export const administers = (
  memberships: readonly TeamMembership[],
  teamId: string,
): boolean =>
  memberships.some((m) => m.teamId === teamId && m.role === TEAM_ADMIN_ROLE);

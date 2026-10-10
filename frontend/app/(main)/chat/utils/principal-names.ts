import { ShareCommonApi } from '@/app/components/share/api';
import { CollaborationApi } from '../collaboration-api';
import { isCollaboratorsView, type ConversationRef } from '../collaboration-types';

export interface PrincipalNames {
  users: Map<string, string>;
  teams: Map<string, string>;
}

/**
 * Display names for ids the authz routes return bare. Best effort: every source is optional and a
 * failure leaves the id unnamed (callers fall back to a generic label), never an error.
 * The collaborator list names the chat's own people and teams; the user's teams and a by-ids
 * lookup fill what is left.
 */
export async function resolvePrincipalNames(
  ref: ConversationRef,
  wanted: { userIds?: Iterable<string>; teamIds?: Iterable<string> },
): Promise<PrincipalNames> {
  const names: PrincipalNames = { users: new Map(), teams: new Map() };
  const userIds = new Set(wanted.userIds ?? []);
  const teamIds = new Set(wanted.teamIds ?? []);

  try {
    const response = await CollaborationApi.getCollaborators(ref);
    if (isCollaboratorsView(response)) {
      for (const c of response.collaborators) {
        if (c.state !== 'active' || !c.displayName) continue;
        (c.principalType === 'team' ? names.teams : names.users).set(c.principalId, c.displayName);
      }
      if (response.owner.displayName) names.users.set(response.owner.userId, response.owner.displayName);
    }
  } catch {
    // unnamed
  }

  const missingTeams = [...teamIds].filter((id) => !names.teams.has(id));
  if (missingTeams.length > 0) {
    try {
      for (const team of await ShareCommonApi.listUserTeams({ limit: 100 })) {
        if (team.name) names.teams.set(team.id, team.name);
      }
    } catch {
      // unnamed
    }
  }

  const missingUsers = [...userIds].filter((id) => !names.users.has(id));
  if (missingUsers.length > 0) {
    try {
      for (const user of await ShareCommonApi.getUsersByIds(missingUsers)) {
        if (user.name) names.users.set(user.id, user.name);
      }
    } catch {
      // unnamed
    }
  }
  return names;
}

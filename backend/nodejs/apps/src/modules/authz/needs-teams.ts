import { COLLAB_ERROR_CODES } from '../enterprise_search/services/collaboration/domain/errors';
import { Decision, LoadedChat } from './ports';

/**
 * The first pass runs with teams unresolved. It is final unless a team grant
 * could still change the answer: the rules signal that for the level the op
 * needs, and a not-found is revisited when team principals exist, because a
 * read-only team member must see 403 on a write, not 404.
 */
export function needsTeams(
  decision: Decision,
  loaded: LoadedChat,
  collab: boolean,
): boolean {
  if (decision.code === COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE) {
    return true;
  }
  const notFound = !decision.allow && decision.via.length === 0;
  const teamRows = (loaded.session.sharedWith ?? []).some(
    (row) => row.userId === undefined && row.teamId !== undefined,
  );
  const projectTeams = (loaded.project?.members ?? []).some(
    (m) => m.principalType === 'team',
  );
  return notFound && ((collab && teamRows) || projectTeams);
}

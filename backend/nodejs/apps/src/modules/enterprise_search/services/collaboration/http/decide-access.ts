import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../../../libs/types/caller-identity';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import { needsTeams } from '../../../../authz/needs-teams';
import { Subject, TeamIds } from '../../../../authz/domain/types';
import {
  Decision,
  IAuthorizationService,
  ResourceRef,
  CheckContext,
  ScopedLoadedChat,
} from '../../../../authz/ports';

export interface DecideAccessInput {
  authz: IAuthorizationService;
  identity: { userId: string; orgId: string };
  op: string;
  loaded: ScopedLoadedChat;
  /** Everything but `loaded`; carries the request memo scope, request id and per-operation facts. */
  context: Omit<CheckContext, 'loaded'>;
  collab: boolean;
  /** Called only when a team grant could change the outcome. */
  resolveTeams: () => Promise<TeamIds>;
}

/**
 * The one access decision for a loaded chat. Teams are resolved only when the first pass says a
 * team grant could decide the outcome; shared by the route guards and the notification list.
 */
export async function decideChatAccess(
  input: DecideAccessInput,
): Promise<{ decision: Decision; teamIds: TeamIds }> {
  const { authz, identity, op, loaded, context, collab } = input;
  const resource: ResourceRef = {
    type: 'chat',
    id: loaded.session._id.toString(),
  };
  const check = (teamIds: TeamIds): Promise<Decision> => {
    const subject: Subject = { ...identity, teamIds };
    return authz.check(subject, op, resource, { ...context, loaded });
  };
  let teamIds: TeamIds = 'unresolved';
  let decision = await check(teamIds);
  if (needsTeams(decision, loaded, collab)) {
    teamIds = await input.resolveTeams();
    if (teamIds !== 'unresolved') {
      decision = await check(teamIds);
    }
  }
  return { decision, teamIds };
}

/** The caller's team ids; a service account has none, and a lookup failure is `unresolved`. */
export async function resolveCallerTeams(
  teams: ITeamDirectory,
  req: AuthenticatedUserRequest,
): Promise<TeamIds> {
  if (req.user?.isServiceAccount === true) {
    return [];
  }
  const result = await teams.callerTeamIds(callerIdentityOf(req));
  return result.status === 'ok' ? result.teamIds : 'unresolved';
}

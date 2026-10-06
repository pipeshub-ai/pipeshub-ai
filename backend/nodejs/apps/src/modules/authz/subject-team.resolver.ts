import { TokenScopes } from '../../libs/enums/token-scopes.enum';
import { IServiceTokenIssuer } from '../../libs/services/service-token.issuer';
import { CallerIdentity } from '../../libs/types/caller-identity';
import { ITeamDirectory } from '../user_management/services/team-directory.service';
import { TeamIds } from './domain/types';

/**
 * Teams of a subject, resolved server-side. Another user's teams are read through the same
 * directory as the caller's own, with a one-minute token that can do nothing but list that
 * user's team ids; the caller's JWT is never forwarded for someone else.
 */
export class SubjectTeamResolver {
  constructor(
    private readonly teams: ITeamDirectory,
    private readonly tokens: IServiceTokenIssuer,
  ) {}

  forCaller(identity: CallerIdentity): Promise<TeamIds> {
    return this.resolve(identity);
  }

  /** The caller must already be allowed to ask about this user. */
  forUser(userId: string, orgId: string): Promise<TeamIds> {
    const token = this.tokens.issue(
      { userId, orgId, scopes: [TokenScopes.TEAM_IDS_READ] },
      '1m',
    );
    return this.resolve({
      userId,
      orgId,
      authHeaders: { Authorization: `Bearer ${token}` },
      requestKey: {},
    });
  }

  private async resolve(identity: CallerIdentity): Promise<TeamIds> {
    const result = await this.teams.callerTeamIds(identity);
    return result.status === 'ok' ? result.teamIds : 'unresolved';
  }
}

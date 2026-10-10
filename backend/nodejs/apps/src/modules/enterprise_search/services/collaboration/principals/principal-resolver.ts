import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { TeamResolutionUnavailableError } from '../domain/errors';
import { principalKey } from '../domain/collaborator.mapper';
import { Principal, PrincipalKey } from '../domain/types';

export type InvalidPrincipalReason =
  | 'not_found'
  | 'service_account'
  | 'disabled'
  | 'owner'
  | 'not_a_member';

export interface InvalidPrincipal {
  key: PrincipalKey;
  reason: InvalidPrincipalReason;
}

export interface PrincipalValidation {
  valid: readonly Principal[];
  invalid: readonly InvalidPrincipal[];
}

export interface IPrincipalResolver {
  /**
   * All-or-nothing check before any write. Users: same org, not deleted, not a service
   * account, not disabled, not the owner. Teams: the caller belongs to it and it exists
   * in the org; the org-wide team needs no lookup. Throws `TeamResolutionUnavailableError`
   * when team principals are present and the caller's teams cannot be resolved.
   */
  validate(
    identity: CallerIdentity,
    principals: readonly Principal[],
    opts: { ownerId: string },
  ): Promise<PrincipalValidation>;
}

const ORG_WIDE_PREFIX = 'all_';

export const orgWideTeamId = (orgId: string): string =>
  `${ORG_WIDE_PREFIX}${orgId}`;

export class PrincipalResolver implements IPrincipalResolver {
  constructor(
    private readonly users: IUserDirectory,
    private readonly teams: ITeamDirectory,
  ) {}

  async validate(
    identity: CallerIdentity,
    principals: readonly Principal[],
    opts: { ownerId: string },
  ): Promise<PrincipalValidation> {
    const users = principals.filter(
      (p): p is Extract<Principal, { type: 'user' }> => p.type === 'user',
    );
    const teams = principals.filter(
      (p): p is Extract<Principal, { type: 'team' }> => p.type === 'team',
    );
    const [userChecks, teamChecks] = await Promise.all([
      this.checkUsers(identity.orgId, users, opts.ownerId),
      this.checkTeams(identity, teams),
    ]);
    const checks = [...userChecks, ...teamChecks];
    return {
      valid: checks.flatMap((c) =>
        c.reason === undefined ? [c.principal] : [],
      ),
      invalid: checks.flatMap((c) =>
        c.reason === undefined
          ? []
          : [{ key: principalKey(c.principal), reason: c.reason }],
      ),
    };
  }

  private async checkUsers(
    orgId: string,
    users: ReadonlyArray<Extract<Principal, { type: 'user' }>>,
    ownerId: string,
  ): Promise<Check[]> {
    if (users.length === 0) {
      return [];
    }
    const found = new Map(
      (
        await this.users.findByIds(
          orgId,
          users.map((u) => u.userId),
        )
      ).map((u) => [u.userId, u]),
    );
    return users.map((principal): Check => {
      const user = found.get(principal.userId);
      if (principal.userId === ownerId) {
        return { principal, reason: 'owner' };
      }
      if (!user) {
        return { principal, reason: 'not_found' };
      }
      if (user.kind === 'service') {
        return { principal, reason: 'service_account' };
      }
      return user.isDisabled
        ? { principal, reason: 'disabled' }
        : { principal };
    });
  }

  private async checkTeams(
    identity: CallerIdentity,
    teams: ReadonlyArray<Extract<Principal, { type: 'team' }>>,
  ): Promise<Check[]> {
    if (teams.length === 0) {
      return [];
    }
    const own = orgWideTeamId(identity.orgId);
    const mine = teams.filter((t) => !t.teamId.startsWith(ORG_WIDE_PREFIX));
    let memberOf: ReadonlySet<string> = new Set();
    if (mine.length > 0) {
      const result = await this.teams.callerTeamIds(identity);
      if (result.status !== 'ok') {
        throw new TeamResolutionUnavailableError();
      }
      memberOf = new Set(result.teamIds);
    }
    return Promise.all(
      teams.map(async (principal): Promise<Check> => {
        if (principal.teamId === own) {
          return { principal };
        }
        if (principal.teamId.startsWith(ORG_WIDE_PREFIX)) {
          return { principal, reason: 'not_found' };
        }
        if (!memberOf.has(principal.teamId)) {
          return { principal, reason: 'not_a_member' };
        }
        return (await this.teams.exists(principal.teamId, identity))
          ? { principal }
          : { principal, reason: 'not_found' };
      }),
    );
  }
}

interface Check {
  principal: Principal;
  reason?: InvalidPrincipalReason;
}

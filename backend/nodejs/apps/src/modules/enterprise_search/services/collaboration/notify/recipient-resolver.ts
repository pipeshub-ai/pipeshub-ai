import { Logger } from '../../../../../libs/services/logger.service';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { Principal } from '../domain/types';
import { orgWideTeamId } from '../principals/principal-resolver';

export const TEAM_EXPANSION_TIMEOUT_MS = 2000;
export const TEAM_EXPANSION_MAX_MEMBERS = 50;

export interface IRecipientResolver {
  /**
   * The users to notify for `principals`: teams expanded as the caller, the org-wide team never,
   * de-duplicated, `exclude` dropped, and only active human users of the org kept. A team that
   * is too large, times out or fails is skipped and logged; it never throws for that.
   */
  resolve(args: {
    orgId: string;
    /** Needed only to expand teams; without it team principals are skipped. */
    identity?: CallerIdentity;
    principals: readonly Principal[];
    exclude?: ReadonlySet<string>;
  }): Promise<readonly string[]>;
}

const defaultLogger = Logger.getInstance({ service: 'RecipientResolver' });

export class RecipientResolver implements IRecipientResolver {
  constructor(
    private readonly users: IUserDirectory,
    private readonly teams: ITeamDirectory,
    private readonly logger: Pick<Logger, 'warn'> = defaultLogger,
    private readonly timeoutMs: number = TEAM_EXPANSION_TIMEOUT_MS,
  ) {}

  async resolve(args: {
    orgId: string;
    identity?: CallerIdentity;
    principals: readonly Principal[];
    exclude?: ReadonlySet<string>;
  }): Promise<readonly string[]> {
    const { orgId, identity, principals, exclude = new Set<string>() } = args;
    const direct = principals.flatMap((p) =>
      p.type === 'user' ? [p.userId] : [],
    );
    const teamIds = [
      ...new Set(
        principals.flatMap((p) =>
          p.type === 'team' && p.teamId !== orgWideTeamId(orgId)
            ? [p.teamId]
            : [],
        ),
      ),
    ];
    if (teamIds.length > 0 && !identity) {
      this.logger.warn('Teams skipped for notifications: no caller identity', {
        teamCount: teamIds.length,
      });
    }
    const expanded = await Promise.all(
      identity ? teamIds.map((teamId) => this.expand(teamId, identity)) : [],
    );
    const candidates = [...new Set([...direct, ...expanded.flat()])].filter(
      (id) => !exclude.has(id),
    );
    if (candidates.length === 0) {
      return [];
    }
    const found = await this.users.findByIds(orgId, candidates);
    const active = new Set(
      found
        .filter((u) => u.kind === 'human' && !u.isDisabled)
        .map((u) => u.userId),
    );
    return candidates.filter((id) => active.has(id));
  }

  private async expand(
    teamId: string,
    identity: CallerIdentity,
  ): Promise<readonly string[]> {
    let timer: NodeJS.Timeout | undefined;
    const timedOut = new Promise<'timeout'>((resolve) => {
      timer = setTimeout(() => {
        resolve('timeout');
      }, this.timeoutMs);
    });
    try {
      const result = await Promise.race([
        this.teams.memberUserIds(teamId, identity, {
          limit: TEAM_EXPANSION_MAX_MEMBERS,
        }),
        timedOut,
      ]);
      if (result === 'timeout' || result.status !== 'ok') {
        this.logger.warn('Team skipped for notifications: not expanded', {
          teamId,
          reason: result === 'timeout' ? 'timeout' : 'unresolved',
        });
        return [];
      }
      return result.userIds;
    } catch (error) {
      this.logger.warn('Team skipped for notifications: expansion failed', {
        teamId,
        error: error instanceof Error ? error.message : String(error),
      });
      return [];
    } finally {
      clearTimeout(timer);
    }
  }
}

import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { TeamIds } from '../../../../authz/domain/types';
import { ITeamDirectory } from '../../../../user_management/services/team-directory.service';
import {
  ITeamLookup,
  TeamInfo,
} from '../../../../user_management/services/team-lookup.service';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import { toCollaborator } from '../domain/collaborator.mapper';
import {
  CollaborationListResponse,
  CollaboratorDto,
  CollaboratorsSummary,
  CollaboratorsView,
  OwnerDto,
} from '../domain/collaboration-views';
import {
  Caller,
  Collaborator,
  ConversationAccessFields,
  GrantedRole,
} from '../domain/types';
import { orgWideTeamId } from '../principals/principal-resolver';

export const ANONYMOUS_TEAM_NAME = 'A team';
export const ORG_WIDE_TEAM_NAME = 'Everyone in your organization';
export const FORMER_MEMBER_NAME = 'Former member';
export const DELETED_TEAM_NAME = 'Deleted team';

export interface ProjectionInput {
  session: Pick<ConversationAccessFields, 'userId' | 'sharedWith' | 'settings'>;
  caller: Caller;
  identity: CallerIdentity;
  role: GrantedRole;
  /** Owner, or an editor the owner allowed to invite: sees who else has access (F-14). */
  full: boolean;
}

export interface IListProjector {
  project(input: ProjectionInput): Promise<CollaborationListResponse>;
}

export class CollaboratorListProjector implements IListProjector {
  constructor(
    private readonly users: IUserDirectory,
    private readonly teams: ITeamDirectory,
    private readonly teamLookup: ITeamLookup,
  ) {}

  async project(input: ProjectionInput): Promise<CollaborationListResponse> {
    const rows = this.rowsOf(input.session);
    const ownerId = input.session.userId.toString();
    return input.full
      ? this.fullView(input, ownerId, rows)
      : this.summary(input, ownerId, rows.length);
  }

  private rowsOf(session: ProjectionInput['session']): Collaborator[] {
    return (session.sharedWith ?? []).flatMap((row) => {
      const c = toCollaborator(row);
      return c ? [c] : [];
    });
  }

  private async summary(
    input: ProjectionInput,
    ownerId: string,
    count: number,
  ): Promise<CollaboratorsSummary> {
    const names = await this.users.displayNames(input.identity.orgId, [
      ownerId,
    ]);
    return {
      owner: ownerDto(ownerId, names),
      collaboratorCount: count,
      myAccess: input.role,
      ...(input.session.settings?.respondMode !== undefined && {
        respondMode: input.session.settings.respondMode,
      }),
    };
  }

  private async fullView(
    input: ProjectionInput,
    ownerId: string,
    rows: readonly Collaborator[],
  ): Promise<CollaboratorsView> {
    const { orgId } = input.identity;
    const userIds = new Set<string>([ownerId]);
    const teamIds: string[] = [];
    for (const row of rows) {
      if (row.principal.type === 'user') {
        userIds.add(row.principal.userId);
      } else if (row.principal.teamId !== orgWideTeamId(orgId)) {
        teamIds.push(row.principal.teamId);
      }
    }
    const [names, teamInfo, mine] = await Promise.all([
      this.users.displayNames(orgId, [...userIds]),
      teamIds.length > 0
        ? this.teamLookup.describeMany(teamIds, input.identity)
        : Promise.resolve(new Map<string, TeamInfo>()),
      teamIds.length > 0
        ? this.callerTeams(input.caller.teamIds, input.identity)
        : Promise.resolve(new Set<string>()),
    ]);
    const collaborators = rows.map((row) =>
      toDto(row, { orgId, names, teamInfo, mine }),
    );
    return {
      owner: ownerDto(ownerId, names),
      collaborators,
      collaboratorCount: collaborators.length,
      settings: {
        editorsCanInvite: input.session.settings?.editorsCanInvite === true,
        ownerContentShared: input.session.settings?.ownerContentShared === true,
        ...(input.session.settings?.respondMode !== undefined && {
          respondMode: input.session.settings.respondMode,
        }),
      },
    };
  }

  /** Empty when the caller's teams cannot be resolved, so every team name stays hidden. */
  private async callerTeams(
    known: TeamIds,
    identity: CallerIdentity,
  ): Promise<ReadonlySet<string>> {
    if (known !== 'unresolved') {
      return new Set(known);
    }
    const result = await this.teams.callerTeamIds(identity);
    return new Set(result.status === 'ok' ? result.teamIds : []);
  }
}

const nameOr = (name: string | undefined, fallback: string): string =>
  name === undefined || name === '' ? fallback : name;

const ownerDto = (
  ownerId: string,
  names: ReadonlyMap<string, string>,
): OwnerDto => ({
  userId: ownerId,
  displayName: nameOr(names.get(ownerId), FORMER_MEMBER_NAME),
});

interface Lookups {
  orgId: string;
  names: ReadonlyMap<string, string>;
  teamInfo: ReadonlyMap<string, TeamInfo>;
  mine: ReadonlySet<string>;
}

function toDto(row: Collaborator, lookups: Lookups): CollaboratorDto {
  const base = {
    accessLevel: row.accessLevel,
    ...(row.addedBy !== undefined && { addedBy: row.addedBy }),
    ...(row.addedAt && { addedAt: row.addedAt.toISOString() }),
  };
  if (row.principal.type === 'user') {
    const name = nameOr(lookups.names.get(row.principal.userId), '');
    return {
      principalType: 'user',
      principalId: row.principal.userId,
      displayName: nameOr(name, FORMER_MEMBER_NAME),
      state: name === '' ? 'former_member' : 'active',
      ...base,
    };
  }
  const { teamId } = row.principal;
  if (teamId === orgWideTeamId(lookups.orgId)) {
    return {
      principalType: 'team',
      principalId: teamId,
      displayName: ORG_WIDE_TEAM_NAME,
      state: 'active',
      ...base,
    };
  }
  const info = lookups.teamInfo.get(teamId);
  if (info?.status === 'missing') {
    return {
      principalType: 'team',
      principalId: teamId,
      displayName: DELETED_TEAM_NAME,
      state: 'deleted_team',
      ...base,
    };
  }
  const visibleName =
    info?.status === 'ok' && lookups.mine.has(teamId) ? info.name : undefined;
  return {
    principalType: 'team',
    principalId: teamId,
    displayName: visibleName ?? ANONYMOUS_TEAM_NAME,
    state: 'active',
    ...base,
  };
}

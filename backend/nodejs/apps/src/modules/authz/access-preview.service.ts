import { BadRequestError, NotFoundError } from '../../libs/errors/http.errors';
import { toChatFacts } from '../enterprise_search/services/collaboration/access/conversation-access.policy';
import {
  ConversationNotFoundError,
  TeamResolutionUnavailableError,
} from '../enterprise_search/services/collaboration/domain/errors';
import { IProjectDocument } from '../projects/types/project.interfaces';
import { atLeast, CanonicalRole, rank } from './domain/ladder';
import { chatRole, isUnresolved, projectRole } from './domain/rules';
import { ChatFacts, Subject, TeamIds } from './domain/types';
import { projectFactsOf } from './loaders/project.loader';
import { IChatAccessLoader } from './ports';
import { IProjectAccessPort } from './ports/project-access.port';

export const PREVIEW_PRINCIPAL_CAP = 500;

export type AccessChange =
  | { type: 'link'; projectId: string }
  | { type: 'unlink' }
  | { type: 'visibility'; visibility: 'private' | 'project' };

export interface PrincipalRole {
  userId?: string;
  teamId?: string;
  role: CanonicalRole;
}

export interface AccessPreview {
  /** No access before, or a higher role after; `role` is the role after. */
  gains: PrincipalRole[];
  /** No access after; `role` is the role before. */
  loses: PrincipalRole[];
  /** Still has the chat but can no longer write to it. */
  becomesReadOnly: PrincipalRole[];
  /** More principals than the cap: the first {@link PREVIEW_PRINCIPAL_CAP} were evaluated. */
  truncated: boolean;
}

interface Principal {
  userId?: string;
  teamId?: string;
}

/**
 * H8: what a link, unlink or visibility change would do to each current collaborator and project
 * member, using the same rules as the real check. Reads only. Teams are evaluated as teams, and a
 * user's own team memberships are not expanded.
 */
export class AccessPreviewService {
  constructor(
    private readonly chats: IChatAccessLoader,
    private readonly projects: IProjectAccessPort,
  ) {}

  /** The caller must already be the chat owner. */
  async preview(
    caller: Subject,
    chatId: string,
    change: AccessChange,
    resolveCallerTeams: () => Promise<TeamIds>,
  ): Promise<AccessPreview> {
    const loaded = await this.chats.load(caller.orgId, chatId);
    if (!loaded || loaded.session.isDeleted === true) {
      throw new ConversationNotFoundError();
    }
    const before = toChatFacts(loaded.session, loaded.project);

    let teams: TeamIds | undefined;
    const callerTeams = async (): Promise<TeamIds> =>
      (teams ??= await resolveCallerTeams());
    const visible = (
      projectId: string,
    ): Promise<{ project: IProjectDocument } | null> =>
      this.visibleProject(caller, projectId, callerTeams);

    const target =
      change.type === 'link' ? await visible(change.projectId) : undefined;
    if (change.type === 'link' && !target) {
      throw new NotFoundError('Project not found');
    }
    const after = this.applyChange(before, change, target?.project);

    // A project the caller cannot open must not reveal its members through the diff.
    const visibleProjectIds = new Set<string>(
      change.type === 'link' ? [change.projectId] : [],
    );
    if (
      before.projectId !== null &&
      !visibleProjectIds.has(before.projectId) &&
      (await visible(before.projectId))
    ) {
      visibleProjectIds.add(before.projectId);
    }
    const { principals, truncated } = this.principalsOf(
      before,
      after,
      visibleProjectIds,
    );

    const result: AccessPreview = {
      gains: [],
      loses: [],
      becomesReadOnly: [],
      truncated,
    };
    for (const principal of principals) {
      const was = effectiveRole(before, principal, caller.orgId);
      const now = effectiveRole(after, principal, caller.orgId);
      if (was === 'none' && now !== 'none') {
        result.gains.push({ ...principal, role: now });
      } else if (was !== 'none' && now === 'none') {
        result.loses.push({ ...principal, role: was });
      } else if (rank(now) > rank(was)) {
        result.gains.push({ ...principal, role: now });
      } else if (atLeast(was, 'editor') && !atLeast(now, 'editor')) {
        result.becomesReadOnly.push({ ...principal, role: now });
      }
    }
    return result;
  }

  private applyChange(
    before: ChatFacts,
    change: AccessChange,
    target: IProjectDocument | undefined,
  ): ChatFacts {
    switch (change.type) {
      case 'unlink':
        return {
          ...before,
          projectId: null,
          projectVisibility: 'private',
          project: null,
        };
      case 'visibility':
        if (before.projectId === null) {
          throw new BadRequestError('Conversation is not linked to a project');
        }
        return { ...before, projectVisibility: change.visibility };
      case 'link':
        return {
          ...before,
          projectId: change.projectId,
          // Same default as the link route.
          projectVisibility:
            before.projectVisibility === 'project' ||
            target?.chatSharing === 'members'
              ? 'project'
              : 'private',
          project: target ? projectFactsOf(target) : null,
        };
    }
  }

  private async visibleProject(
    caller: Subject,
    projectId: string,
    callerTeams: () => Promise<TeamIds>,
  ): Promise<{ project: IProjectDocument } | null> {
    const found = await this.projects.roleOf(caller, projectId);
    if (found || caller.teamIds !== 'unresolved') {
      return found;
    }
    const teamIds = await callerTeams();
    if (teamIds === 'unresolved') {
      throw new TeamResolutionUnavailableError();
    }
    return this.projects.roleOf({ ...caller, teamIds }, projectId);
  }

  private principalsOf(
    before: ChatFacts,
    after: ChatFacts,
    visibleProjectIds: ReadonlySet<string>,
  ): { principals: Principal[]; truncated: boolean } {
    const seen = new Map<string, Principal>();
    const add = (p: Principal): void => {
      const key =
        p.userId !== undefined ? `u:${p.userId}` : `t:${p.teamId ?? ''}`;
      if (!seen.has(key)) {
        seen.set(key, p);
      }
    };
    for (const row of before.sharedWith) {
      add(
        row.userId !== undefined
          ? { userId: row.userId }
          : { teamId: row.teamId },
      );
    }
    for (const facts of [before, after]) {
      if (facts.projectId === null || !visibleProjectIds.has(facts.projectId)) {
        continue;
      }
      if (facts.project) {
        add({ userId: facts.project.ownerId });
        for (const m of facts.project.members) {
          add(
            m.principalType === 'user'
              ? { userId: m.principalId }
              : { teamId: m.principalId },
          );
        }
      }
    }
    seen.delete(`u:${before.ownerId}`);
    const all = [...seen.values()];
    return {
      principals: all.slice(0, PREVIEW_PRINCIPAL_CAP),
      truncated: all.length > PREVIEW_PRINCIPAL_CAP,
    };
  }
}

/** H1/H2 role, lowered to viewer when the principal could not send because they lack access to the linked project (D7). */
function effectiveRole(
  chat: ChatFacts,
  principal: Principal,
  orgId: string,
): CanonicalRole {
  const subject: Subject =
    principal.userId !== undefined
      ? { userId: principal.userId, orgId, teamIds: [] }
      : { userId: '', orgId, teamIds: [principal.teamId ?? ''] };
  const resolved = chatRole(chat, subject, { collab: true });
  if (isUnresolved(resolved)) {
    return 'none';
  }
  if (
    atLeast(resolved.role, 'editor') &&
    chat.project !== null &&
    projectRole(chat.project, subject) === 'none'
  ) {
    return 'viewer';
  }
  return resolved.role;
}

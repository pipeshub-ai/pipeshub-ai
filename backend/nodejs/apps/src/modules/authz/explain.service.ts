import { ForbiddenError } from '../../libs/errors/http.errors';
import { isUserOrgAdmin } from '../user_management/services/user-admin.service';
import { ChatExplanation } from './domain/explain';
import { AccessPath, Subject, TeamIds } from './domain/types';
import { IAuthorizationService, IChatAccessLoader, ResourceRef } from './ports';

export const REDACTED_TEAM_REF = 'redacted';

export type OrgAdminCheck = (userId: string, orgId: string) => Promise<boolean>;

/**
 * C-3: a subject's access can be explained by that subject, by the resource owner,
 * or by an org admin. Team references the caller is not a member of are redacted.
 */
export class ExplainService {
  constructor(
    private readonly authz: IAuthorizationService,
    private readonly chats: IChatAccessLoader,
    private readonly isOrgAdmin: OrgAdminCheck = isUserOrgAdmin,
  ) {}

  async explain(
    caller: Subject,
    target: Subject,
    resource: ResourceRef,
  ): Promise<ChatExplanation> {
    if (target.orgId !== caller.orgId) {
      throw new ForbiddenError('Not allowed to explain this access');
    }
    if (target.userId !== caller.userId) {
      await this.assertMayExplainOthers(caller, resource);
    }
    return this.build(caller, target, resource);
  }

  /**
   * Like `explain`, but the target's teams are resolved only after the caller is allowed to ask,
   * so a refused request never reaches the team directory.
   */
  async explainUser(
    caller: Subject,
    targetUserId: string,
    resource: ResourceRef,
    resolveTeams: (userId: string) => Promise<TeamIds>,
  ): Promise<ChatExplanation> {
    if (targetUserId === caller.userId) {
      return this.build(caller, caller, resource);
    }
    await this.assertMayExplainOthers(caller, resource);
    const teamIds = await resolveTeams(targetUserId);
    return this.build(
      caller,
      { userId: targetUserId, orgId: caller.orgId, teamIds },
      resource,
    );
  }

  private async build(
    caller: Subject,
    target: Subject,
    resource: ResourceRef,
  ): Promise<ChatExplanation> {
    const explanation = await this.authz.explain(target, resource);
    return {
      ...explanation,
      via: explanation.via.map((p) => this.redact(p, caller)),
    };
  }

  private async assertMayExplainOthers(
    caller: Subject,
    resource: ResourceRef,
  ): Promise<void> {
    if (await this.isOrgAdmin(caller.userId, caller.orgId)) {
      return;
    }
    const chat =
      resource.type === 'chat'
        ? await this.chats.load(caller.orgId, resource.id)
        : null;
    if (chat?.session.userId.toString() !== caller.userId) {
      // Same answer whether or not the chat exists, so this cannot probe for ids.
      throw new ForbiddenError('Not allowed to explain this access');
    }
  }

  private redact(path: AccessPath, caller: Subject): AccessPath {
    if (path.type !== 'team') {
      return path;
    }
    const member =
      caller.teamIds !== 'unresolved' && caller.teamIds.includes(path.ref);
    return member ? path : { ...path, ref: REDACTED_TEAM_REF };
  }
}

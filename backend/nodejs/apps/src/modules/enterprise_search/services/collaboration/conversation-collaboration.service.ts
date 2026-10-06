import { CallerIdentity } from '../../../../libs/types/caller-identity';
import { IScopedChatLoader, ScopedSession } from '../../../authz/ports';
import { IProjectAccessPort } from '../../../authz/ports/project-access.port';
import { COLLAB_FLAG_KEYS } from '../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../configuration_manager/services/platform-feature-flags.service';
import { EmailTemplateType } from '../../../mail/middlewares/types';
import { toCollaborator } from './domain/collaborator.mapper';
import { CollaborationListResponse } from './domain/collaboration-views';
import {
  ConversationNotFoundError,
  ConversationOwnerOnlyError,
  InvalidPrincipalError,
  ProjectAccessRequiredError,
} from './domain/errors';
import {
  AccessLevel,
  AccessView,
  Caller,
  ConversationSettings,
  GrantedRole,
  Principal,
} from './domain/types';
import { ConversationAccessGrant } from './http/conversation-context';
import { ILegacySharing, LegacyShareResult } from './legacy/legacy-sharing';
import { chatAudit } from './mutation/audit-events';
import { CollaboratorWriter } from './mutation/collaborator-writer';
import {
  auditContextOf,
  eventBaseOf,
  loadTargetOf,
  scopeOf,
} from './mutation/mutation-context';
import { MutationEffects } from './mutation/mutation-effects';
import { IMutationRunner } from './mutation/mutation-runner';
import { ICollaborationEmailIntents } from './notify/collaboration-email-intents';
import { ICollaboratorRepository } from './persistence/collaborator.repository';
import { IPrincipalResolver } from './principals/principal-resolver';
import {
  assertOrgWideAllowed,
  planUpsert,
  RequestedCollaborator,
} from './rules/upsert-plan';
import { IListProjector } from './views/collaborator-list.projector';

export interface UpsertInput {
  collaborators: readonly RequestedCollaborator[];
  /** Hand-over note, at most 500 characters; travels in the `chat.shared` events only. */
  note?: string;
  confirmOrgWide?: boolean;
}

export interface IConversationCollaborationService {
  list(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
  ): Promise<CollaborationListResponse>;
  upsert(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    input: UpsertInput,
  ): Promise<CollaborationListResponse>;
  remove(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    principal: Principal,
  ): Promise<CollaborationListResponse>;
  updateSettings(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    patch: ConversationSettings,
  ): Promise<CollaborationListResponse>;
  transferOwnership(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    newOwnerUserId: string,
  ): Promise<CollaborationListResponse>;
  leave(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
  ): Promise<void>;
  /** The legacy `/share` wrapper: with collaborative chats off it stores `read`, as before. */
  legacyShare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
    accessLevel: AccessLevel,
  ): Promise<LegacyShareResult>;
  legacyUnshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): Promise<LegacyShareResult>;
}

/** Sharing a chat at the moment it is created: the sender is its owner, so no share permission is checked. */
export interface INewChatSharing {
  /** Everything `upsert` would refuse, before any chat exists: org-wide policy, principals. Throws the same errors. */
  validate(
    caller: Caller,
    identity: CallerIdentity,
    input: UpsertInput,
  ): Promise<void>;
  /** Shares the chat just created; audit rows and notifications are written after the chat and its rows exist. */
  apply(
    caller: Caller,
    identity: CallerIdentity,
    chat: Parameters<IScopedChatLoader['loadScoped']>[1],
    input: UpsertInput,
  ): Promise<void>;
}

export interface CollaborationServiceDeps {
  repo: ICollaboratorRepository;
  effects: MutationEffects;
  runner: IMutationRunner;
  principals: IPrincipalResolver;
  projector: IListProjector;
  emailIntents: ICollaborationEmailIntents;
  chats: IScopedChatLoader;
  projects: IProjectAccessPort;
  flags: IFeatureFlags;
  legacy: ILegacySharing;
}

const userPrincipal = (userId: string): Principal => ({
  type: 'user',
  userId,
});

const OWNER_VIEW: AccessView = {
  role: 'owner',
  isOwner: true,
  accessLevel: 'owner',
  canSend: true,
  canManage: true,
  canInvite: true,
  isCollaborative: false,
};

export class ConversationCollaborationService
  implements IConversationCollaborationService, INewChatSharing
{
  private readonly writer: CollaboratorWriter;

  constructor(private readonly deps: CollaborationServiceDeps) {
    this.writer = new CollaboratorWriter(deps.repo, deps.effects);
  }

  list(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
  ): Promise<CollaborationListResponse> {
    return this.deps.projector.project({
      session: grant.session,
      caller: grant.caller,
      identity,
      role: grant.role,
      full: grant.view.canInvite,
    });
  }

  async upsert(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    input: UpsertInput,
  ): Promise<CollaborationListResponse> {
    const session = await this.applyUpsert(grant, identity, input);
    return this.project(session, grant, identity, grant.role);
  }

  async remove(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    principal: Principal,
  ): Promise<CollaborationListResponse> {
    const session = await this.applyRemoval(grant, identity, [principal]);
    return this.project(session, grant, identity, grant.role);
  }

  async updateSettings(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    patch: ConversationSettings,
  ): Promise<CollaborationListResponse> {
    const { effects, repo, runner } = this.deps;
    const audit = auditContextOf(grant, identity);
    const before = grant.session.settings ?? {};
    await runner.run(async (dbSession) => {
      const out = await repo.updateSettings(scopeOf(grant, true), patch, {
        session: dbSession,
      });
      if (out.status === 'not_found') {
        throw new ConversationNotFoundError();
      }
      if (out.status === 'applied') {
        await effects.recordAudit(
          chatAudit(audit, 'chat.settingsChange', {
            before,
            after: { ...before, ...patch },
            aclVersion: out.aclVersion,
          }),
          dbSession,
        );
      }
    });
    return this.project(await this.reload(grant), grant, identity, grant.role);
  }

  async transferOwnership(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    newOwnerUserId: string,
  ): Promise<CollaborationListResponse> {
    const { effects, repo, runner, emailIntents } = this.deps;
    const { caller } = grant;
    await this.assertTransferTarget(grant, identity, newOwnerUserId);
    const intents = await emailIntents.forDirectUsers({
      orgId: caller.orgId,
      actorUserId: caller.userId,
      template: EmailTemplateType.ChatOwnershipTransferred,
      recipients: [{ userId: newOwnerUserId, accessLevel: 'write' }],
    });
    const audit = auditContextOf(grant, identity);
    const applied = await runner.run(async (dbSession) => {
      const out = await repo.transfer(
        scopeOf(grant, false),
        { fromUserId: caller.userId, toUserId: newOwnerUserId },
        { session: dbSession },
      );
      if (out.status !== 'applied') {
        return false;
      }
      const intent = intents.get(newOwnerUserId);
      await effects.recordAudit(
        chatAudit(audit, 'chat.ownershipTransfer', {
          principal: userPrincipal(newOwnerUserId),
          before: { ownerId: caller.userId },
          after: { ownerId: newOwnerUserId },
          aclVersion: out.aclVersion,
        }),
        dbSession,
      );
      await effects.publish(
        [
          {
            type: 'chat.ownershipTransferred',
            ...eventBaseOf(grant),
            newOwnerUserId,
            previousOwnerUserId: caller.userId,
            aclVersion: out.aclVersion,
            ...(intent && { emailIntent: intent }),
          },
        ],
        dbSession,
        identity,
      );
      return true;
    });
    if (!applied) {
      throw await this.transferLostRace(grant, newOwnerUserId);
    }
    return this.project(await this.reload(grant), grant, identity, 'write');
  }

  async leave(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
  ): Promise<void> {
    const { effects, repo, runner } = this.deps;
    const audit = auditContextOf(grant, identity);
    await runner.run(async (dbSession) => {
      const out = await repo.leave(scopeOf(grant, false), grant.caller.userId, {
        session: dbSession,
      });
      if (out.status === 'owner') {
        throw new ConversationOwnerOnlyError();
      }
      if (out.status === 'not_found') {
        throw new ConversationNotFoundError();
      }
      await effects.recordAudit(
        chatAudit(audit, 'chat.leave', {
          principal: userPrincipal(grant.caller.userId),
          aclVersion: out.aclVersion,
        }),
        dbSession,
      );
      // Leaving archives the leaver's own "shared with you" notification, as a removal does.
      await effects.publish(
        [{ type: 'chat.unshared', ...eventBaseOf(grant), principal: userPrincipal(grant.caller.userId) }],
        dbSession,
        identity,
      );
    });
  }

  async legacyShare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
    accessLevel: AccessLevel,
  ): Promise<LegacyShareResult> {
    if (!(await this.collabEnabled())) {
      return this.deps.legacy.share(grant, identity, userIds);
    }
    const session = await this.applyUpsert(grant, identity, {
      collaborators: userIds.map((id) => ({
        principal: userPrincipal(id),
        accessLevel,
      })),
    });
    return legacyResult(session, accessLevel);
  }

  async legacyUnshare(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    userIds: readonly string[],
  ): Promise<LegacyShareResult> {
    if (!(await this.collabEnabled())) {
      return this.deps.legacy.unshare(grant, identity, userIds);
    }
    const session = await this.applyRemoval(
      grant,
      identity,
      userIds.map(userPrincipal),
    );
    return legacyResult(session, 'read');
  }

  async validate(
    caller: Caller,
    identity: CallerIdentity,
    input: UpsertInput,
  ): Promise<void> {
    if (!(await this.collabEnabled())) {
      throw new ConversationNotFoundError();
    }
    assertOrgWideAllowed(input.collaborators, {
      orgId: caller.orgId,
      confirmOrgWide: input.confirmOrgWide === true,
      writeAllowed: await this.deps.flags.isEnabled(
        COLLAB_FLAG_KEYS.orgWideChatWrite,
      ),
    });
    const ops = planUpsert({
      requested: input.collaborators,
      existing: [],
      isOwner: true,
    });
    const checked = await this.deps.principals.validate(
      identity,
      ops.flatMap((o) => (o.kind === 'add' ? [o.principal] : [])),
      { ownerId: caller.userId },
    );
    if (checked.invalid.length > 0) {
      throw new InvalidPrincipalError(checked.invalid);
    }
  }

  async apply(
    caller: Caller,
    identity: CallerIdentity,
    chat: Parameters<IScopedChatLoader['loadScoped']>[1],
    input: UpsertInput,
  ): Promise<void> {
    const loaded = await this.deps.chats.loadScoped(caller.orgId, chat);
    if (!loaded) {
      throw new ConversationNotFoundError();
    }
    await this.applyUpsert(
      { session: loaded.session, role: 'owner', via: [], caller, view: OWNER_VIEW },
      identity,
      input,
    );
  }

  /** Validate everything first (all-or-nothing), then write, audit and emit in one unit. Returns the session as stored afterwards. */
  private async applyUpsert(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    input: UpsertInput,
  ): Promise<ScopedSession> {
    const { principals, flags, runner, emailIntents } = this.deps;
    const { caller, session } = grant;
    assertOrgWideAllowed(input.collaborators, {
      orgId: caller.orgId,
      confirmOrgWide: input.confirmOrgWide === true,
      writeAllowed: await flags.isEnabled(COLLAB_FLAG_KEYS.orgWideChatWrite),
    });
    const ops = planUpsert({
      requested: input.collaborators,
      existing: session.sharedWith ?? [],
      isOwner: grant.role === 'owner',
    });
    const additions = ops.flatMap((o) => (o.kind === 'add' ? [o] : []));
    const checked = await principals.validate(
      identity,
      additions.map((o) => o.principal),
      { ownerId: session.userId.toString() },
    );
    if (checked.invalid.length > 0) {
      throw new InvalidPrincipalError(checked.invalid);
    }
    if (ops.length === 0) {
      return this.reload(grant);
    }
    const intents = await emailIntents.forDirectUsers({
      orgId: caller.orgId,
      actorUserId: caller.userId,
      template: EmailTemplateType.ChatShared,
      recipients: additions.flatMap((o) =>
        o.principal.type === 'user'
          ? [{ userId: o.principal.userId, accessLevel: o.accessLevel }]
          : [],
      ),
    });
    await runner.run((dbSession) =>
      this.writer.apply({ grant, identity, dbSession }, ops, {
        note: input.note,
        emailIntents: intents,
      }),
    );
    return this.reload(grant);
  }

  private async applyRemoval(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    principals: readonly Principal[],
  ): Promise<ScopedSession> {
    await this.deps.runner.run((dbSession) =>
      this.writer.remove({ grant, identity, dbSession }, principals),
    );
    return this.reload(grant);
  }

  /** The target must already be a direct `write` collaborator and a valid user; on a project chat they also need project access (D7). */
  private async assertTransferTarget(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    targetId: string,
  ): Promise<void> {
    const { session, caller } = grant;
    const target = userPrincipal(targetId);
    const isWriteRow = (session.sharedWith ?? []).some((row) => {
      const c = toCollaborator(row);
      return (
        c?.principal.type === 'user' &&
        c.principal.userId === targetId &&
        c.accessLevel === 'write'
      );
    });
    if (!isWriteRow) {
      throw new InvalidPrincipalError([
        { key: `user:${targetId}`, reason: 'not_a_write_collaborator' },
      ]);
    }
    const checked = await this.deps.principals.validate(identity, [target], {
      ownerId: caller.userId,
    });
    if (checked.invalid.length > 0) {
      throw new InvalidPrincipalError(checked.invalid);
    }
    const projectId = session.projectId?.toString();
    if (projectId === undefined) {
      return;
    }
    // Team memberships of the target are unknown here, so only direct and org-wide project access counts: fail closed.
    const found = await this.deps.projects.roleOf(
      { userId: targetId, orgId: caller.orgId, teamIds: [] },
      projectId,
    );
    if (!found) {
      throw new ProjectAccessRequiredError(projectId);
    }
  }

  /** The atomic transfer matched nothing: say why from the stored state. */
  private async transferLostRace(
    grant: ConversationAccessGrant,
    targetId: string,
  ): Promise<Error> {
    const loaded = await this.deps.chats.loadScoped(
      grant.caller.orgId,
      loadTargetOf(grant.session),
    );
    if (!loaded) {
      return new ConversationNotFoundError();
    }
    if (loaded.session.userId.toString() !== grant.caller.userId) {
      return new ConversationOwnerOnlyError();
    }
    return new InvalidPrincipalError([
      { key: `user:${targetId}`, reason: 'not_a_write_collaborator' },
    ]);
  }

  private async project(
    session: ScopedSession,
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    role: GrantedRole,
  ): Promise<CollaborationListResponse> {
    return this.deps.projector.project({
      session,
      caller: grant.caller,
      identity,
      role,
      full:
        role === 'owner' ||
        (role === 'write' && session.settings?.editorsCanInvite === true),
    });
  }

  private async reload(grant: ConversationAccessGrant): Promise<ScopedSession> {
    const loaded = await this.deps.chats.loadScoped(
      grant.caller.orgId,
      loadTargetOf(grant.session),
    );
    if (!loaded) {
      throw new ConversationNotFoundError();
    }
    return loaded.session;
  }

  private collabEnabled(): Promise<boolean> {
    return this.deps.flags.isEnabled(COLLAB_FLAG_KEYS.collaborativeChats);
  }
}

function legacyResult(
  session: ScopedSession,
  appliedAccessLevel: AccessLevel,
): LegacyShareResult {
  const rows = session.sharedWith ?? [];
  return {
    id: session._id,
    isShared: rows.length > 0,
    sharedWith: rows,
    appliedAccessLevel,
  };
}

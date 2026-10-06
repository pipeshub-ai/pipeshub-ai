import { BadRequestError } from '../../libs/errors/http.errors';
import { COLLAB_FLAG_KEYS } from '../configuration_manager/constants/constants';
import { IFeatureFlags } from '../configuration_manager/services/platform-feature-flags.service';
import { ConversationAccessAuthorizer } from '../enterprise_search/services/collaboration/access/conversation-access.authorizer';
import {
  OPERATION_REQUIREMENTS,
  toChatFacts,
} from '../enterprise_search/services/collaboration/access/conversation-access.policy';
import { COLLAB_ERROR_CODES } from '../enterprise_search/services/collaboration/domain/errors';
import { ConversationOperation } from '../enterprise_search/services/collaboration/domain/types';
import { readAclVersion } from './cache/acl-version';
import { DecisionCache } from './cache/decision-cache';
import {
  DecisionLogEntry,
  IDecisionLog,
  NOOP_DECISION_LOG,
} from './decision-log';
import { ChatExplanation, explainChat } from './domain/explain';
import { atLeast, CanonicalRole, maxRole } from './domain/ladder';
import { legacyAllows } from './domain/legacy';
import {
  canReadArtifact,
  canReadAttachment,
  chatRole,
  isUnresolved,
} from './domain/rules';
import type { ITeamDirectory } from '../user_management/services/team-directory.service';
import { Subject } from './domain/types';
import {
  CheckContext,
  Decision,
  IAuthorizationService,
  IChatAccessLoader,
  IContentOwnershipLoader,
  LoadedChat,
  ResourceRef,
} from './ports';
import { IProjectAccessPort } from './ports/project-access.port';

/** Project actions are the minimum role required. */
const PROJECT_ACTIONS: Readonly<Record<string, CanonicalRole>> = {
  viewer: 'viewer',
  editor: 'editor',
  owner: 'owner',
};

const NOT_FOUND: Decision = { allow: false, role: 'none', via: [] };

export interface AuthorizationServiceDeps {
  chats: IChatAccessLoader;
  projects: IProjectAccessPort;
  flags: IFeatureFlags;
  /** Without it, attachment and artifact reads are denied. */
  content?: IContentOwnershipLoader;
  cache?: DecisionCache<Decision>;
  /** Its teams version is part of the decision-cache key; without it the version is 0. */
  teams?: Pick<ITeamDirectory, 'teamsVersion'>;
  log?: IDecisionLog;
}

/**
 * The single decision point over the ladder, rules and loaders. Fails closed:
 * a missing resource or content loader is a deny, never an allow.
 */
export class AuthorizationService implements IAuthorizationService {
  private readonly evaluator = new ConversationAccessAuthorizer();
  private readonly log: IDecisionLog;

  constructor(private readonly deps: AuthorizationServiceDeps) {
    this.log = deps.log ?? NOOP_DECISION_LOG;
  }

  async check(
    subject: Subject,
    action: string,
    resource: ResourceRef,
    context: CheckContext = {},
  ): Promise<Decision> {
    const decision = await this.decide(subject, action, resource, context);
    this.log.record(this.entry(subject, action, resource, decision, context));
    return decision;
  }

  async explain(
    subject: Subject,
    resource: ResourceRef,
  ): Promise<ChatExplanation> {
    if (resource.type !== 'chat') {
      throw new BadRequestError('Only chat access can be explained');
    }
    const loaded = await this.loadLive(subject.orgId, resource.id);
    if (!loaded) {
      return { role: 'none', via: [], teamsUnresolved: false };
    }
    return explainChat(toChatFacts(loaded.session, loaded.project), subject, {
      collab: await this.collabEnabled(),
    });
  }

  private async decide(
    subject: Subject,
    action: string,
    resource: ResourceRef,
    context: CheckContext,
  ): Promise<Decision> {
    switch (resource.type) {
      case 'chat':
        return this.decideChat(subject, action, resource.id, context);
      case 'project':
        return this.decideProject(subject, action, resource.id);
      case 'chatAttachment':
      case 'chatArtifact':
        return this.decideContent(subject, resource);
    }
  }

  private async decideChat(
    subject: Subject,
    action: string,
    chatId: string,
    context: CheckContext,
  ): Promise<Decision> {
    if (!(action in OPERATION_REQUIREMENTS)) {
      throw new BadRequestError(`Unknown chat action: ${action}`);
    }
    const op = action as ConversationOperation;
    const loaded =
      context.loaded ?? (await this.loadLive(subject.orgId, chatId));
    if (!loaded || loaded.session.isDeleted === true) {
      return NOT_FOUND;
    }
    const collab = await this.collabEnabled();
    const aclVersion = readAclVersion(loaded.session);
    const cacheable = context.operation === undefined;
    const keyParts = {
      orgId: subject.orgId,
      subjectKey: subject.userId,
      // The flag changes what a role means, and H2 depends on the project's ACL, so both are part of the key.
      resourceType: `chat:${op}:${collab ? 'collab' : `legacy-${context.legacyKind ?? 'any'}`}:p${String(loaded.project?.aclVersion ?? 0)}`,
      resourceId: chatId,
      aclVersion,
      teamsVersion: await this.deps.teams?.teamsVersion(subject.orgId),
    };
    const request = context.request ?? subject;
    if (cacheable) {
      const hit = await this.deps.cache?.get(request, keyParts);
      if (hit) {
        return hit;
      }
    }
    const decision =
      !collab && context.legacyKind !== undefined
        ? this.evaluateLegacy(subject, op, loaded, aclVersion, context)
        : this.evaluateChat(subject, op, loaded, collab, aclVersion, context);
    if (cacheable) {
      await this.deps.cache?.set(request, keyParts, decision, {
        teamsResolved: subject.teamIds !== 'unresolved',
      });
    }
    return decision;
  }

  /** Paths are resolved at read level, then the per-kind table decides; a denial is always not-found. */
  private evaluateLegacy(
    subject: Subject,
    op: ConversationOperation,
    loaded: LoadedChat,
    aclVersion: number,
    context: CheckContext,
  ): Decision {
    const kind = context.legacyKind;
    const { decision, via } = this.evaluator.evaluate({
      op: 'read',
      conversationId: '',
      session: loaded.session,
      caller: subject,
      flags: { collab: false },
      requestId: context.requestId ?? '',
      project: loaded.project,
    });
    const role = maxRole(...via.map((p) => p.role));
    if (!decision.allowed) {
      return {
        allow: false,
        role,
        via,
        aclVersion,
        code:
          decision.code === COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE
            ? decision.code
            : COLLAB_ERROR_CODES.NOT_FOUND,
      };
    }
    return kind !== undefined && legacyAllows(kind, op, via)
      ? { allow: true, role, via, aclVersion }
      : {
          allow: false,
          role,
          via,
          aclVersion,
          code: COLLAB_ERROR_CODES.NOT_FOUND,
        };
  }

  private evaluateChat(
    subject: Subject,
    op: ConversationOperation,
    loaded: LoadedChat,
    collab: boolean,
    aclVersion: number,
    context: CheckContext,
  ): Decision {
    const { decision, via } = this.evaluator.evaluate({
      op,
      conversationId: '',
      session: loaded.session,
      caller: subject,
      flags: { collab },
      requestId: context.requestId ?? '',
      project: loaded.project,
      ctx: context.operation,
    });
    const role = maxRole(...via.map((p) => p.role));
    return decision.allowed
      ? { allow: true, role, via, aclVersion }
      : { allow: false, role, via, aclVersion, code: decision.code };
  }

  private async decideProject(
    subject: Subject,
    action: string,
    projectId: string,
  ): Promise<Decision> {
    const min = PROJECT_ACTIONS[action];
    if (min === undefined) {
      throw new BadRequestError(`Unknown project action: ${action}`);
    }
    // The project load is the only read, and it also yields aclVersion, so a decision cache would not save a round trip.
    const found = await this.deps.projects.roleOf(subject, projectId);
    if (!found) {
      return NOT_FOUND;
    }
    return {
      allow: atLeast(found.role, min),
      role: found.role,
      via: [{ type: 'project', ref: projectId, role: found.role }],
      aclVersion: readAclVersion(found.project),
    };
  }

  private async decideContent(
    subject: Subject,
    resource: Extract<ResourceRef, { type: 'chatAttachment' | 'chatArtifact' }>,
  ): Promise<Decision> {
    const loaded = await this.loadLive(subject.orgId, resource.conversationId);
    const content = loaded ? await this.deps.content?.load(resource) : null;
    if (!loaded || !content) {
      return NOT_FOUND;
    }
    const collab = await this.collabEnabled();
    const resolved = chatRole(
      toChatFacts(loaded.session, loaded.project),
      subject,
      { collab },
    );
    const aclVersion = readAclVersion(loaded.session);
    if (isUnresolved(resolved)) {
      return {
        allow: false,
        role: 'none',
        via: [],
        aclVersion,
        code: COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE,
      };
    }
    const consent = loaded.session.settings?.ownerContentShared === true;
    const isCreator = content.creatorId === subject.userId;
    const allow =
      resource.type === 'chatAttachment'
        ? canReadAttachment({
            isUploader: isCreator,
            chatRole: resolved.role,
            consent,
          })
        : canReadArtifact({
            isCreator,
            chatRole: resolved.role,
            consent,
            kind: content.kind ?? '',
          });
    return { allow, role: resolved.role, via: resolved.via, aclVersion };
  }

  /** A soft-deleted chat is treated as missing before the cache is consulted, so no delete path has to bump aclVersion. */
  private async loadLive(
    orgId: string,
    chatId: string,
  ): Promise<LoadedChat | null> {
    const loaded = await this.deps.chats.load(orgId, chatId);
    return loaded && loaded.session.isDeleted !== true ? loaded : null;
  }

  private async collabEnabled(): Promise<boolean> {
    return this.deps.flags.isEnabled(COLLAB_FLAG_KEYS.collaborativeChats);
  }

  private entry(
    subject: Subject,
    action: string,
    resource: ResourceRef,
    decision: Decision,
    context: CheckContext,
  ): DecisionLogEntry {
    return {
      subject: subject.userId,
      action,
      resource: `${resource.type}:${resource.id}`,
      decision: decision.allow ? 'allow' : 'deny',
      role: decision.allow ? decision.role : 'none',
      via: decision.via.map((p) => `${p.type}:${p.ref}`),
      aclVersion: decision.aclVersion,
      requestId: context.requestId ?? '',
      ...(!decision.allow && {
        code: decision.code ?? COLLAB_ERROR_CODES.NOT_FOUND,
      }),
    };
  }
}

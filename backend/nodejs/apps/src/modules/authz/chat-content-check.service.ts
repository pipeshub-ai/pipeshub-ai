import { Logger } from '../../libs/services/logger.service';
import { COLLAB_FLAG_KEYS } from '../configuration_manager/constants/constants';
import { IFeatureFlags } from '../configuration_manager/services/platform-feature-flags.service';
import { IUserDirectory } from '../user_management/services/user-directory.service';
import { readAclVersion } from './cache/acl-version';
import { needsTeams } from './needs-teams';
import {
  attachmentAuthorOf,
  canReadChatArtifact,
  canReadChatAttachment,
  ChatArtifactKind,
} from './domain/chat-content.rules';
import { CanonicalRole } from './domain/ladder';
import { Subject } from './domain/types';
import {
  IAuthorizationService,
  IChatAccessLoader,
  IChatContentLoader,
  LoadedChat,
} from './ports';
import { SubjectTeamResolver } from './subject-team.resolver';

export interface ChatContentCheck {
  userId: string;
  orgId: string;
  resource: {
    type: 'chatAttachment' | 'chatArtifact';
    recordId: string;
    /** Uploader or creator of the record, resolved by the caller from its OWNER edge. */
    ownerUserId: string;
    conversationId?: string;
    runId?: string;
    kind?: ChatArtifactKind;
  };
}

/** `aclVersion` is the version of the chat that allowed the read; null on every deny, so a deny reveals nothing. */
export interface ChatContentVerdict {
  allow: boolean;
  aclVersion: number | null;
}

const DENY: ChatContentVerdict = { allow: false, aclVersion: null };

interface SessionRole {
  loaded: LoadedChat;
  role: CanonicalRole;
}

/**
 * H4/H5: may this user read a chat attachment or artifact they do not own. Fails
 * closed; every unknown subject, chat or record is the same deny.
 */
export class ChatContentCheckService {
  constructor(
    private readonly deps: {
      authz: IAuthorizationService;
      chats: IChatAccessLoader;
      content: IChatContentLoader;
      users: IUserDirectory;
      teams: Pick<SubjectTeamResolver, 'forUser'>;
      flags: IFeatureFlags;
      logger: Logger;
    },
  ) {}

  async check(req: ChatContentCheck): Promise<ChatContentVerdict> {
    const verdict = await this.decide(req);
    this.deps.logger.debug('Chat content decision', {
      userId: req.userId,
      resourceType: req.resource.type,
      recordId: req.resource.recordId,
      allow: verdict.allow,
    });
    return verdict;
  }

  private async decide(req: ChatContentCheck): Promise<ChatContentVerdict> {
    const [user] = await this.deps.users.findByIds(req.orgId, [req.userId]);
    if (!user || user.kind === 'service' || user.isDisabled) {
      return DENY;
    }
    const flagOn = await this.deps.flags.isEnabled(
      COLLAB_FLAG_KEYS.collaborativeChats,
    );
    const subject: Subject = {
      userId: user.userId,
      orgId: req.orgId,
      teamIds: 'unresolved',
    };
    const roleIn = async (chatId: string): Promise<SessionRole | null> => {
      const loaded = await this.deps.chats.load(req.orgId, chatId);
      if (!loaded || loaded.session.isDeleted === true) {
        return null;
      }
      let decision = await this.deps.authz.check(
        subject,
        'read',
        {
          type: 'chat',
          id: chatId,
        },
        { loaded },
      );
      if (needsTeams(decision, loaded, flagOn)) {
        const teamIds = await this.deps.teams.forUser(user.userId, req.orgId);
        if (teamIds !== 'unresolved') {
          decision = await this.deps.authz.check(
            { ...subject, teamIds },
            'read',
            { type: 'chat', id: chatId },
            { loaded },
          );
        }
      }
      return { loaded, role: decision.allow ? decision.role : 'none' };
    };
    return req.resource.type === 'chatAttachment'
      ? this.attachment(req, flagOn, roleIn)
      : this.artifact(req, flagOn, roleIn);
  }

  private async attachment(
    req: ChatContentCheck,
    flagOn: boolean,
    roleIn: (chatId: string) => Promise<SessionRole | null>,
  ): Promise<ChatContentVerdict> {
    const { recordId, conversationId, ownerUserId } = req.resource;
    const rows = await this.deps.content.loadAttachmentContext(
      req.orgId,
      recordId,
      conversationId,
    );
    const roles = new Map<string, SessionRole | null>();
    for (const row of rows) {
      if (conversationId !== undefined && row.sessionId !== conversationId) {
        continue;
      }
      if (!roles.has(row.sessionId)) {
        roles.set(row.sessionId, await roleIn(row.sessionId));
      }
      const found = roles.get(row.sessionId);
      if (!found) {
        continue;
      }
      const turn = {
        ...row,
        sessionOwnerId: found.loaded.session.userId.toString(),
      };
      // Only the owner's own attachments count, so a turn cannot consent someone else's file.
      if (
        attachmentAuthorOf(turn) === ownerUserId &&
        canReadChatAttachment(found.role, turn, flagOn)
      ) {
        return {
          allow: true,
          aclVersion: readAclVersion(found.loaded.session),
        };
      }
    }
    return DENY;
  }

  private async artifact(
    req: ChatContentCheck,
    flagOn: boolean,
    roleIn: (chatId: string) => Promise<SessionRole | null>,
  ): Promise<ChatContentVerdict> {
    const { conversationId, runId, kind } = req.resource;
    if (conversationId === undefined) {
      return DENY;
    }
    const found = await roleIn(conversationId);
    if (!found) {
      return DENY;
    }
    const turn = flagOn
      ? await this.deps.content.loadArtifactContext(
          req.orgId,
          conversationId,
          runId,
        )
      : null;
    return canReadChatArtifact(found.role, turn, kind ?? {}, flagOn)
      ? { allow: true, aclVersion: readAclVersion(found.loaded.session) }
      : DENY;
  }
}

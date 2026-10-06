import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../../../libs/types/caller-identity';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { ScopedSession } from '../../../../authz/ports';
import { ChatTarget } from '../../../utils/ai-chat-payload';
import { UpsertInput } from '../conversation-collaboration.service';
import { toStoredCollaborator } from '../domain/collaborator.mapper';
import { ConnectorSetupRequiredError } from '../domain/errors';
import { ConversationAccessGrant } from '../http/conversation-context';
import { IAgentReadinessPort } from '../readiness/agent-readiness.port';
import { guestAgentKeyOf } from '../turn/turn-responder';
import { MessageIsNoteError } from './mention.errors';
import { claimedMentions, inertUnlistedTokens } from './mention.parser';
import { DEFAULT_RESPOND_MODE, MentionRef, SessionKind } from './mention.types';
import { IMentionValidator } from './mention.validator';
import { classifyResponder } from './responder-router';
import { setTurnMentions } from './turn-mentions';
import { Types } from 'mongoose';

export interface IMentionTurnGate {
  /**
   * Runs inside `runLease()`, before readiness and the lease. With the mentions flag off it does
   * nothing. Otherwise it validates the body's mentions, stores the result for the handler, and
   * answers 422 `MESSAGE_IS_NOTE` when the respond mode says no AI should run. A mentioned agent
   * other than the chat's own is a guest turn: its readiness is checked here, as the sender.
   */
  admit(
    req: AuthenticatedUserRequest,
    grant: ConversationAccessGrant,
    kind: SessionKind,
  ): Promise<void>;
  /**
   * Regenerating a guest agent's answer re-runs it on that agent, so the sender must still be allowed to:
   * the same verdict as a fresh mention (403, 503 or the setup error), checked before the lease is kept.
   * With the mentions flag off this does nothing.
   */
  admitRegenerate(
    req: AuthenticatedUserRequest,
    grant: ConversationAccessGrant,
    agentKey: string,
  ): Promise<void>;
  /**
   * The first send has no session yet, so its mentions are checked against the chat it is about
   * to create: the sender alone, plus the draft collaborators of `share`, who count as in the chat.
   */
  admitFirstSend(
    req: AuthenticatedUserRequest,
    target: ChatTarget,
    share?: UpsertInput,
  ): Promise<void>;
}

export interface MentionTurnGateDeps {
  flags: IFeatureFlags;
  validator: IMentionValidator;
  readiness?: IAgentReadinessPort;
}

interface MentionBody {
  query?: unknown;
  mentions?: readonly MentionRef[];
}

export class MentionTurnGate implements IMentionTurnGate {
  constructor(private readonly deps: MentionTurnGateDeps) {}

  async admit(
    req: AuthenticatedUserRequest,
    grant: ConversationAccessGrant,
    kind: SessionKind,
  ): Promise<void> {
    if (!(await this.deps.flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions))) {
      return;
    }
    const body = (req.body ?? {}) as MentionBody;
    const respondMode =
      grant.session.settings?.respondMode ?? DEFAULT_RESPOND_MODE;
    const claimed = claimedMentions(body.query, body.mentions);
    const responder = classifyResponder({
      mentions: claimed,
      respondMode,
      sessionKind: kind,
    });
    const validated =
      claimed.length === 0
        ? undefined
        : await this.deps.validator.validate(claimed, {
            session: grant.session,
            identity: callerIdentityOf(req),
          });
    const mentions = validated?.mentions ?? [];
    if (responder === 'note') throw new MessageIsNoteError();
    if (typeof body.query === 'string') {
      (req.body as MentionBody).query = inertUnlistedTokens(
        body.query,
        mentions,
      );
    }
    const guest = guestAgentKeyOf(mentions, grant.session.agentKey);
    if (guest !== undefined) await this.assertReady(req, guest);
    setTurnMentions(req, mentions, guest, validated?.nonParticipants);
  }

  async admitRegenerate(
    req: AuthenticatedUserRequest,
    grant: ConversationAccessGrant,
    agentKey: string,
  ): Promise<void> {
    if (!(await this.deps.flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions))) {
      return;
    }
    const { mentions } = await this.deps.validator.validate(
      [{ type: 'agent', id: agentKey }],
      { session: grant.session, identity: callerIdentityOf(req) },
    );
    const guest = guestAgentKeyOf(mentions, grant.session.agentKey);
    if (guest === undefined) return;
    await this.assertReady(req, guest);
    setTurnMentions(req, [], guest);
  }

  async admitFirstSend(
    req: AuthenticatedUserRequest,
    target: ChatTarget,
    share?: UpsertInput,
  ): Promise<void> {
    if (!(await this.deps.flags.isEnabled(COLLAB_FLAG_KEYS.chatMentions))) {
      return;
    }
    const body = (req.body ?? {}) as MentionBody;
    const claimed = claimedMentions(body.query, body.mentions);
    if (claimed.length === 0) {
      this.makeUnlistedTokensInert(req, []);
      return;
    }
    const identity = callerIdentityOf(req);
    const ownAgentKey = target.kind === 'agent' ? target.agentKey : undefined;
    const draftChat = {
      _id: new Types.ObjectId(),
      orgId: new Types.ObjectId(identity.orgId),
      userId: new Types.ObjectId(identity.userId),
      sharedWith: (share?.collaborators ?? []).map((c) =>
        toStoredCollaborator({ principal: c.principal, accessLevel: c.accessLevel }),
      ),
      ...(ownAgentKey !== undefined && { agentKey: ownAgentKey }),
    } as unknown as ScopedSession;
    const { mentions, nonParticipants } = await this.deps.validator.validate(
      claimed,
      { session: draftChat, identity },
    );
    const guest = guestAgentKeyOf(mentions, ownAgentKey);
    if (guest !== undefined) await this.assertReady(req, guest);
    this.makeUnlistedTokensInert(req, mentions);
    setTurnMentions(req, mentions, guest, nonParticipants);
  }

  private makeUnlistedTokensInert(
    req: AuthenticatedUserRequest,
    mentions: readonly MentionRef[],
  ): void {
    const body = req.body as MentionBody;
    if (typeof body.query === 'string') {
      body.query = inertUnlistedTokens(body.query, mentions);
    }
  }

  /** The sender's own toolset credentials decide; an unknown answer passes, because the AI backend refuses an unready run itself. */
  private async assertReady(
    req: AuthenticatedUserRequest,
    agentKey: string,
  ): Promise<void> {
    const { readiness } = this.deps;
    if (!readiness || req.user?.isServiceAccount === true) return;
    const { orgId, userId } = callerIdentityOf(req);
    const ready = await readiness.check({ orgId, userId }, agentKey);
    if (ready.status === 'blocked') {
      throw new ConnectorSetupRequiredError(ready.toolsets);
    }
  }
}

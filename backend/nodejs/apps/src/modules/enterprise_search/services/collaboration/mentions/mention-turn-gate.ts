import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../../../libs/types/caller-identity';
import { COLLAB_FLAG_KEYS } from '../../../../configuration_manager/constants/constants';
import { IFeatureFlags } from '../../../../configuration_manager/services/platform-feature-flags.service';
import { ConversationAccessGrant } from '../http/conversation-context';
import { MessageIsNoteError } from './mention.errors';
import { claimedMentions, inertUnlistedTokens } from './mention.parser';
import { DEFAULT_RESPOND_MODE, MentionRef, SessionKind } from './mention.types';
import { IMentionValidator } from './mention.validator';
import { classifyResponder } from './responder-router';
import { setTurnMentions } from './turn-mentions';

export interface IMentionTurnGate {
  /**
   * Runs inside `runLease()`, before readiness and the lease. With the mentions flag off it does
   * nothing. Otherwise it validates the body's mentions, stores the result for the handler, and
   * answers 422 `MESSAGE_IS_NOTE` when the respond mode says no AI should run.
   */
  admit(
    req: AuthenticatedUserRequest,
    grant: ConversationAccessGrant,
    kind: SessionKind,
  ): Promise<void>;
}

export interface MentionTurnGateDeps {
  flags: IFeatureFlags;
  validator: IMentionValidator;
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
    const mentions =
      claimed.length === 0
        ? []
        : (
            await this.deps.validator.validate(claimed, {
              session: grant.session,
              identity: callerIdentityOf(req),
            })
          ).mentions;
    if (responder === 'note') throw new MessageIsNoteError();
    if (typeof body.query === 'string') {
      (req.body as MentionBody).query = inertUnlistedTokens(
        body.query,
        mentions,
      );
    }
    setTurnMentions(req, mentions);
  }
}

import { NextFunction, Response } from 'express';
import { ExpectedRefusalError } from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  TURN_REFUSAL_MESSAGES,
  TurnRefusal,
  turnRefusal,
} from '../../agent-harness/domain/session-roles';
import { SessionRoleLookup } from '../../agent-harness/ports/session-roles';
import { CollaboratorTurnsSetting } from '../../agent-harness/ports/collaborator-turns';
import { getCurrentOrgId } from '../../../libs/context/orgContext';
import { ConversationNotFoundError } from '../services/conversation/domain/errors';
import { getConversationRepository } from '../../../libs/repositories/conversation';
import { idOf, rowAuthorOf } from '../services/conversation/privacy/visibility';

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

/** `HTTP_COLLABORATOR_TURNS_DISABLED` or `HTTP_VIEWER_READ_ONLY`, as 403. */
export class TurnRefusedError extends ExpectedRefusalError {
  constructor(readonly reason: TurnRefusal) {
    super(reason, TURN_REFUSAL_MESSAGES[reason], HTTP_STATUS.FORBIDDEN);
  }
}

/**
 * Who asked the question whose answer a regenerate names: the person a stored row names (its sender, for a
 * collaborator's turn), or undefined when it names nobody (the owner's own) or is not there.
 */
export type RegeneratedAsker = (scope: {
  orgId: string;
  sessionId: string;
  messageId: string;
}) => Promise<string | undefined>;

/**
 * `RegeneratedAsker` as the conversation store answers it: whom the row names, else who sent it (a collaborator's turn,
 * as the harness stores it).
 */
export const storedAsker: RegeneratedAsker = async ({
  orgId,
  sessionId,
  messageId,
}) => {
  const row = await getConversationRepository().findMessage(
    sessionId,
    orgId,
    messageId,
  );
  return row === null ? undefined : rowAuthorOf(row, idOf(row.senderId));
};

/**
 * Refuses a turn (a new message, a regenerated answer) from a participant
 * whose role does not allow one (SS0), with the role's reason code, before
 * the handler runs. The owner and anyone who is not a participant pass: the
 * handler's own owner filter answers the latter "not found", as before.
 *
 * `collaboratorTurns`: the org's setting the harness admission reads too
 * (`turnRefusal`), so a collaborator the org lets send turns reaches the
 * harness, which runs their turn as them (OWD-19). Without it the route serves
 * its owner only: today's loop and anything else that has no collaborator path
 * (R38AUTHZ-FU-2: the chat route once passed no setting, so every
 * collaborator was refused although the org allowed their turns).
 *
 * `askerOf`, on a regenerate route: such a collaborator regenerates only the
 * answers to their own questions. Anyone else's stays its owner's, and is
 * denied as the conversation policy denies every operation it keeps for the
 * owner (`ConversationNotFoundError`).
 */
export function refuseNonOwnerTurns(
  roles: SessionRoleLookup,
  collaboratorTurns?: CollaboratorTurnsSetting,
  askerOf?: RegeneratedAsker,
) {
  return async function refuseNonOwnerTurn(
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): Promise<void> {
    const userId: unknown = req.user?.userId;
    const actorId = typeof userId === 'string' ? userId : undefined;
    const orgId = getCurrentOrgId();
    const sessionId = req.params['conversationId'];
    if (!actorId || !orgId || !sessionId) {
      next();
      return;
    }
    const agentKey = req.params['agentKey'];
    try {
      const role = await roles.roleOf({
        orgId,
        actorId,
        sessionId,
        ...(agentKey ? { agentKey } : {}),
      });
      const reason =
        role === null
          ? null
          : turnRefusal(
              role,
              'loop',
              role === 'collaborator' && collaboratorTurns !== undefined
                ? await collaboratorTurns.enabled(orgId)
                : false,
            );
      const messageId = req.params['messageId'];
      if (
        reason === null &&
        role === 'collaborator' &&
        askerOf !== undefined &&
        messageId !== undefined &&
        (await askerOf({ orgId, sessionId, messageId })) !== actorId
      ) {
        logger.info('session.turn_refused', {
          event: 'session.turn_refused',
          orgId,
          sessionId,
          actorId,
          role,
          reason: 'not_asker',
          requestId: req.context?.requestId,
        });
        next(new ConversationNotFoundError());
        return;
      }
      if (reason === null) {
        next();
        return;
      }
      logger.info('session.turn_refused', {
        event: 'session.turn_refused',
        orgId,
        sessionId,
        actorId,
        role,
        reason,
        requestId: req.context?.requestId,
      });
      next(new TurnRefusedError(reason));
    } catch (error) {
      next(error);
    }
  };
}

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

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

/** `HTTP_COLLABORATOR_TURNS_DISABLED` or `HTTP_VIEWER_READ_ONLY`, as 403. */
export class TurnRefusedError extends ExpectedRefusalError {
  constructor(readonly reason: TurnRefusal) {
    super(reason, TURN_REFUSAL_MESSAGES[reason], HTTP_STATUS.FORBIDDEN);
  }
}

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
 */
export function refuseNonOwnerTurns(
  roles: SessionRoleLookup,
  collaboratorTurns?: CollaboratorTurnsSetting,
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

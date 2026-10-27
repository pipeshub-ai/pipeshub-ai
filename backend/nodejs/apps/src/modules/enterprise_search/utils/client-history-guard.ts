import { NextFunction, Request, Response } from 'express';
import { Types } from 'mongoose';
import { BadRequestError } from '../../../libs/errors/http.errors';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({ service: 'Client history guard' });

export const CLIENT_HISTORY_REJECTED_REASON = 'client_history_not_accepted';

export const CLIENT_HISTORY_REJECTED_MESSAGE =
  'previousConversations is not accepted. Conversation history is read from ' +
  'the stored conversation: create the conversation, then send follow-up ' +
  'questions to its messages endpoint.';

const payloadBytes = (value: unknown): number => {
  try {
    return Buffer.byteLength(JSON.stringify(value), 'utf8');
  } catch {
    return -1;
  }
};

/** For the log line only; never used to decide anything. */
const orgIdForLog = (req: Request): string | undefined => {
  const candidates: unknown[] = [
    (req as AuthenticatedUserRequest).user?.orgId,
    (req as AuthenticatedServiceRequest).tokenPayload?.orgId,
  ];
  for (const value of candidates) {
    if (typeof value === 'string') return value;
    if (value instanceof Types.ObjectId) return value.toHexString();
  }
  return undefined;
};

/**
 * Refuses conversation history supplied in a create request. The agent
 * replays history as its own earlier answers and tool results, so history
 * must come from the server's stored conversation; a new conversation has
 * none. An empty list is dropped rather than refused, since it asks for
 * nothing. Mount after authentication so the log line carries the org.
 */
export const rejectClientConversationHistory = (
  req: Request,
  _res: Response,
  next: NextFunction,
): void => {
  const body = req.body as Record<string, unknown> | undefined;
  if (!body || typeof body !== 'object' || !('previousConversations' in body)) {
    next();
    return;
  }
  const history = body.previousConversations;
  if (Array.isArray(history) && history.length === 0) {
    delete body.previousConversations;
    next();
    return;
  }

  logger.warn('Rejected client-supplied conversation history', {
    requestId: req.context?.requestId,
    orgId: orgIdForLog(req),
    path: req.baseUrl + req.path,
    reason: CLIENT_HISTORY_REJECTED_REASON,
    entries: Array.isArray(history) ? history.length : undefined,
    valueType: Array.isArray(history) ? 'array' : typeof history,
    bytes: payloadBytes(history),
  });
  next(
    new BadRequestError(CLIENT_HISTORY_REJECTED_MESSAGE, {
      reason: CLIENT_HISTORY_REJECTED_REASON,
    }),
  );
};

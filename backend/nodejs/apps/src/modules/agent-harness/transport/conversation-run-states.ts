import { NextFunction, Request, RequestHandler, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import type { RunListService } from '../application/run-lists';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../application/harness-log';
import type { ConversationRunState } from '../domain/run-lists';
import { identityString } from './request-identity';

/** The field each conversation of the caller's list carries (`conversation-run-state.schema.json`). */
export const HARNESS_STATE_FIELD = 'harnessState';

/**
 * ADR-UI-01 D2 (UI-B1 G1): adds `harnessState` to each conversation of the caller's conversation list
 * (`GET /api/v1/conversations`, `GET /api/v1/agents/:agentKey/conversations`): working now, needs the caller, finished
 * since they last opened it. The states are the caller's (their asks, their runs, their mark); a list read while the
 * harness is off, or whose states cannot be read, is answered as it was, never refused (L-21).
 */
export function conversationRunStatesMiddleware(
  service: () => RunListService | null,
  enabled: () => boolean,
  logger: HarnessLogger = silentHarnessLogger,
): RequestHandler {
  return (req: Request, res: Response, next: NextFunction): void => {
    const runs = enabled() ? service() : null;
    const user = (req as AuthenticatedUserRequest).user as
      Record<string, unknown> | undefined;
    const orgId = identityString(user?.['orgId']);
    const actorId = identityString(user?.['userId']);
    if (runs === null || orgId === null || actorId === null) {
      next();
      return;
    }
    const json = res.json.bind(res);
    res.json = ((body: unknown) => {
      const ids = conversationIds(body);
      if (ids.length === 0 || res.statusCode >= 300) return json(body);
      runs
        .conversationStates({ orgId, actorId }, ids)
        .then((states) => json(withStates(body, states)))
        .catch((error: unknown) => {
          logger.warn(
            'run.list_states_failed',
            harnessLogMeta('run.list_states_failed', {
              orgId,
              actorId,
              conversations: ids.length,
              errorType: error instanceof Error ? error.name : typeof error,
              choice: 'list_without_states',
            }),
          );
          json(body);
        });
      return res;
    }) as Response['json'];
    next();
  };
}

const conversationsOf = (body: unknown): Array<Record<string, unknown>> => {
  const list = (body as { conversations?: unknown } | null)?.conversations;
  return Array.isArray(list)
    ? list.filter(
        (item): item is Record<string, unknown> =>
          item !== null && typeof item === 'object',
      )
    : [];
};

const idOf = (conversation: Record<string, unknown>): string | null => {
  const id = conversation['_id'] ?? conversation['id'];
  if (id === undefined || id === null) return null;
  // A Mongo document's _id is an ObjectId here, whose own toString is its hex id.
  return typeof id === 'string'
    ? id
    : (id as { toString(): string }).toString();
};

function conversationIds(body: unknown): string[] {
  return conversationsOf(body).flatMap((conversation) => {
    const id = idOf(conversation);
    return id === null ? [] : [id];
  });
}

function withStates(
  body: unknown,
  states: readonly ConversationRunState[],
): unknown {
  const bySession = new Map(states.map((state) => [state.sessionId, state]));
  const conversations = conversationsOf(body).map((conversation) => {
    const state = bySession.get(idOf(conversation) ?? '');
    if (state === undefined) return conversation;
    const { sessionId: _sessionId, ...view } = state;
    return { ...conversation, [HARNESS_STATE_FIELD]: view };
  });
  return { ...(body as Record<string, unknown>), conversations };
}

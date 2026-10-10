import { Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { callerIdentityOf } from '../../../libs/types/caller-identity';
import { Logger } from '../../../libs/services/logger.service';
import { ChatAccessLoader } from '../../authz/loaders/chat.loader';
import { conversationEventProducers } from '../services/collaboration/notify/conversation-event-producers';
import { notifiableMentions } from '../services/collaboration/notify/mention-principals';
import { ConversationTurnDeps } from '../services/collaboration/turn/turn-deps';
import { AGUIEventType, frameAGUI } from './agui';
import { FirstTurn } from './first-turn';

const logger = Logger.getInstance({ service: 'First-send note' });

/**
 * The people a first message mentioned are told once the chat holds the draft collaborators, so the
 * session is read again after the share. Best effort: the chat exists and a retry of the same
 * `clientMessageId` is a 409, so a failed publish is logged, never raised.
 */
export async function notifyFirstSendNote(
  deps: Pick<ConversationTurnDeps, 'producers'>,
  req: AuthenticatedUserRequest,
  turn: FirstTurn,
): Promise<void> {
  const mentions = notifiableMentions(turn.userRow);
  if (mentions.length === 0) return;
  const { conversation } = turn;
  const identity = callerIdentityOf(req);
  try {
    const loaded = await new ChatAccessLoader().loadScoped(identity.orgId, {
      id: String(conversation._id),
      kind: conversation.agentKey ? 'agent' : 'chat',
      ...(conversation.agentKey && { agentKey: conversation.agentKey }),
    });
    if (!loaded) return;
    await (deps.producers ?? conversationEventProducers)().mentioned({
      session: loaded.session,
      identity,
      actorUserId: identity.userId,
      messageId: String(turn.userRow._id),
      mentions,
    });
  } catch (error) {
    logger.warn('chat.mentioned publish failed for a first-send note', {
      sessionId: String(conversation._id),
      error: error instanceof Error ? error.message : String(error),
    });
  }
}

/** The conversation as a completed turn returns it, holding only the note. */
export const firstSendNoteView = (
  turn: FirstTurn,
): Record<string, unknown> => ({
  ...turn.conversation.toObject(),
  messages: [turn.userRow.toObject()],
});

/** Ends a note's stream the way a finished turn does, so the browser settles on the stored note without a run. */
export function writeFirstSendNoteResult(
  res: Response,
  turn: FirstTurn,
  startedAt: number,
  requestId?: string,
): void {
  res.write(
    frameAGUI(AGUIEventType.RUN_FINISHED, {
      result: {
        conversation: firstSendNoteView(turn),
        recordsUsed: 0,
        meta: {
          requestId,
          timestamp: new Date().toISOString(),
          duration: Date.now() - startedAt,
          recordsUsed: 0,
        },
      },
    }),
  );
  res.end();
}

import { Response } from 'express';
import { Logger } from '../../../libs/services/logger.service';
import { AGUIEventType, frameAGUI } from './agui';
import { ChatCompareFeature } from './chat-compare-feature';

const logger = Logger.getInstance({ service: 'ChatCompareTurn' });

/**
 * For a request that asked for a Compare models turn (`compareTurn: true`) with
 * the flag on, tells the client the saved question's id as soon as it exists so
 * it can start the second model in parallel. Any other request, and any failure
 * here, leaves the stream exactly as it would have been.
 */
export const emitTurnStarted = async (
  res: Response,
  body: unknown,
  conversationId: string,
  userMessageId: () => Promise<string | undefined>,
): Promise<void> => {
  if ((body as { compareTurn?: unknown } | undefined)?.compareTurn !== true) {
    return;
  }
  try {
    if (!(await ChatCompareFeature.isEnabled())) return;
    const id = await userMessageId();
    if (!id) return;
    res.write(
      frameAGUI(AGUIEventType.CUSTOM, {
        name: 'turn_started',
        value: { conversationId, userMessageId: id },
      }),
    );
    (res as unknown as { flush?: () => void }).flush?.();
  } catch (error) {
    logger.warn('Could not announce the Compare models turn', {
      conversationId,
      error: error instanceof Error ? error.message : String(error),
    });
  }
};

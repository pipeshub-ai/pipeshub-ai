import { Readable } from 'stream';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { handleBackendError } from '../../../libs/errors/backend-error';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

// Common helper to start AI streams with consistent error mapping and logging
export const startAIStream = async (
  options: AICommandOptions,
  operation: string,
  logContext: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<Readable | undefined> => {
  const aiServiceCommand = new AIServiceCommand(options);
  try {
    return await aiServiceCommand.executeStream(signal);
  } catch (error: unknown) {
    // The caller aborted the open itself (detached-run cap or a lost lease) and already finalized the run.
    if (signal?.aborted) {
      logger.info('AI service stream open abandoned', logContext);
      return undefined;
    }
    const mappedError = handleBackendError(error, operation);
    logger.error('AI service stream start failed', {
      ...logContext,
      message: error instanceof Error ? error.message : String(error),
    });
    throw mappedError;
  }
};

import { Readable } from 'stream';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { handleBackendError } from '../../../libs/errors/backend-error';
import { DomainHttpError } from '../../../libs/errors/domain-http.error';
import { Logger } from '../../../libs/services/logger.service';
import { CHAT_ERROR_MESSAGES } from './chat-error-messages';

const logger = Logger.getInstance({ service: 'Enterprise Search Service' });

// A code the client can look up: capitals, digits and underscores.
const REFUSAL_CODE = /^[A-Z][A-Z0-9_]{2,63}$/;

/**
 * The AI service refused the request and named why. Its text stays general,
 * since the client words its code.
 */
export class AIRequestRefusedError extends DomainHttpError {
  constructor(
    code: string,
    message: string = CHAT_ERROR_MESSAGES.failed,
    statusCode = 502,
  ) {
    super(code, message, statusCode);
  }
}

interface FailedCall {
  response?: {
    status?: unknown;
    data?: unknown;
  };
}

/** A 400 whose `detail` names a code; other statuses keep their own handling. */
export const refusalFrom = (
  status: unknown,
  data: unknown,
): AIRequestRefusedError | undefined => {
  const detail = (
    data as { detail?: { code?: unknown } | null } | null
  )?.detail;
  const code = detail?.code;
  return status === 400 && typeof code === 'string' && REFUSAL_CODE.test(code)
    ? new AIRequestRefusedError(code)
    : undefined;
};

const refusalOf = (error: unknown): AIRequestRefusedError | undefined => {
  const response = (error as FailedCall | null)?.response;
  return refusalFrom(response?.status, response?.data);
};

// Common helper to start AI streams with consistent error mapping and logging
export const startAIStream = async (
  options: AICommandOptions,
  operation: string,
  signal?: AbortSignal,
): Promise<Readable | undefined> => {
  const aiServiceCommand = new AIServiceCommand(options);
  try {
    return await aiServiceCommand.executeStream(signal);
  } catch (error: unknown) {
    // The caller aborted the open itself (detached-run cap or a lost lease) and already finalized the run.
    if (signal?.aborted) {
      logger.debug('AI service stream open abandoned');
      return undefined;
    }
    const refusal = refusalOf(error);
    if (refusal) {
      logger.warn('AI service refused the stream', { code: refusal.code });
      throw refusal;
    }
    // The call line and the error middleware log it; handleBackendError only maps it.
    throw handleBackendError(error, operation);
  }
};

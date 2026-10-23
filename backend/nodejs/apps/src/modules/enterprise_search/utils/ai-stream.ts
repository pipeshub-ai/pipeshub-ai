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

/** The chat stream's own code for a request the AI service refused with 402, so a refusal before the stream reads the same. */
export const AI_BUDGET_EXCEEDED = 'ai_budget_exceeded';

/**
 * The AI service refused the request and named why. A malformed request's text
 * stays general, since the client words its code; a budget refusal keeps the
 * AI service's text, which tells the person whom to ask.
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

/** A 400 whose `detail` names a code, or a 402 from the AI service; other statuses keep their own handling. */
export const refusalFrom = (
  status: unknown,
  data: unknown,
): AIRequestRefusedError | undefined => {
  const detail = (
    data as { detail?: { code?: unknown; message?: unknown } | null } | null
  )?.detail;
  if (status === 402) {
    const message = detail?.message;
    return new AIRequestRefusedError(
      AI_BUDGET_EXCEEDED,
      typeof message === 'string' && message.trim() !== ''
        ? message
        : CHAT_ERROR_MESSAGES.budgetExceeded,
      402,
    );
  }
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
    const refusal = refusalOf(error);
    const mappedError = refusal ?? handleBackendError(error, operation);
    logger.error('AI service stream start failed', {
      ...logContext,
      message: error instanceof Error ? error.message : String(error),
      ...(refusal && { code: refusal.code }),
    });
    throw mappedError;
  }
};

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
 * The AI service refused the request and named why. A malformed request's text
 * stays general, since the client words its code; a refusal only an admin can
 * lift keeps the AI service's text, which tells the person whom to ask.
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

/**
 * Refusals of the AI service that only an admin can lift: the same request gets the same answer until an admin acts,
 * so trying again cannot help. One keeps the AI service's words, which say whom to ask, and a run it stops keeps its
 * code on the failed row. There are none by default; `useAdminRefusals` names them.
 */
export interface AdminRefusals {
  /** The refusal a failed AI-service response stands for, when it is one of these. */
  fromResponse(
    status: unknown,
    data: unknown,
  ): AIRequestRefusedError | undefined;
  /** Whether a run's error code names one of these. */
  isCode(code: string | undefined): boolean;
}

export const noAdminRefusals: AdminRefusals = {
  fromResponse: () => undefined,
  isCode: () => false,
};

let currentAdminRefusals: AdminRefusals = noAdminRefusals;

export const adminRefusals = (): AdminRefusals => currentAdminRefusals;

/** Names the refusals only an admin can lift; `undefined` restores the default, which has none. */
export function useAdminRefusals(next: AdminRefusals | undefined): void {
  currentAdminRefusals = next ?? noAdminRefusals;
}

/** A refusal only an admin can lift, or a 400 whose `detail` names a code; other statuses keep their own handling. */
export const refusalFrom = (
  status: unknown,
  data: unknown,
): AIRequestRefusedError | undefined => {
  const adminOnly = adminRefusals().fromResponse(status, data);
  if (adminOnly) return adminOnly;
  const code = (data as { detail?: { code?: unknown } | null } | null)?.detail
    ?.code;
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

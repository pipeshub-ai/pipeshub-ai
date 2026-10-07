'use client';

import { extractApiErrorMessage } from './api-error';

/**
 * What a person reads when a streamed request (chat, agent chat) fails. The
 * error's message becomes the assistant's reply, so it must never be a status
 * line or a browser network error.
 */
export const STREAM_ERROR_MESSAGES = {
  sessionExpired: 'Your session has expired. Sign in again to continue.',
  forbidden: "You don't have access to this. Ask a workspace admin if you need it.",
  offline: "Couldn't reach PipesHub. Check your internet connection, then try again.",
  interrupted: 'The connection to PipesHub dropped before this finished. Please try again.',
  unavailable: 'PipesHub is having trouble right now. Please try again in a minute.',
} as const;

/** Chat shows the error as the assistant's reply, so it speaks about the answer. */
export const CHAT_STREAM_ERROR_MESSAGES = {
  ...STREAM_ERROR_MESSAGES,
  interrupted:
    'The answer was interrupted before it finished. Click Regenerate or send your message again.',
  unavailable: "PipesHub couldn't answer right now. Please try again in a minute.",
} as const;

type StreamErrorMessages = typeof STREAM_ERROR_MESSAGES | typeof CHAT_STREAM_ERROR_MESSAGES;

/** An error whose message was written for the user, so it can be shown as-is. */
export class StreamError extends Error {
  readonly status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'StreamError';
    this.status = status;
  }
}

export function busyStreamMessage(retryAfterSeconds?: number): string {
  return retryAfterSeconds
    ? `PipesHub is busy right now. Please try again in ${retryAfterSeconds} second${retryAfterSeconds === 1 ? '' : 's'}.`
    : 'PipesHub is busy right now. Please try again in a few seconds.';
}

// Server text that is a raw error rather than a sentence for the user.
const TECHNICAL_TEXT =
  /traceback|\b\w+(Error|Exception)\b|\[object Object\]|status code|NoneType|undefined|ECONN|socket|^\s*\d{3}\b|\{\s*"|'\w+'$/i;

function readableServerMessage(body: unknown): string | null {
  const message = extractApiErrorMessage(body);
  return message && !TECHNICAL_TEXT.test(message) ? message : null;
}

function retryAfterSeconds(response: Response): number | undefined {
  const seconds = Number(response.headers.get('retry-after'));
  return Number.isInteger(seconds) && seconds > 0 && seconds <= 120 ? seconds : undefined;
}

/**
 * Builds the error for a stream request the server refused before streaming
 * began. A clear message from the server is kept for a 4xx; otherwise the
 * status picks a fixed message with a next step.
 */
export async function streamHttpError(
  response: Response,
  messages: StreamErrorMessages = STREAM_ERROR_MESSAGES,
): Promise<StreamError> {
  let body: unknown = null;
  try {
    body = await response.clone().json();
  } catch {
    // Not JSON (a proxy's HTML page, an empty body): fall back on the status.
  }
  const { status } = response;
  if (status === 401) return new StreamError(messages.sessionExpired, status);
  if (status === 429 || status === 503 || status === 504) {
    return new StreamError(busyStreamMessage(retryAfterSeconds(response)), status);
  }
  if (status >= 400 && status < 500) {
    const serverMessage = readableServerMessage(body);
    if (serverMessage) return new StreamError(serverMessage, status);
    if (status === 403) return new StreamError(messages.forbidden, status);
  }
  return new StreamError(messages.unavailable, status);
}

/**
 * The message for a stream that failed in the browser: the request never
 * reached PipesHub, or the connection dropped part-way through an answer.
 * Errors this module already built pass through unchanged.
 */
export function streamFailure(
  error: unknown,
  responseStarted: boolean,
  messages: StreamErrorMessages = STREAM_ERROR_MESSAGES,
): StreamError {
  if (error instanceof StreamError) return error;
  return new StreamError(responseStarted ? messages.interrupted : messages.offline);
}

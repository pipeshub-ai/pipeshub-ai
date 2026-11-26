import { NextFunction, Request, Response } from 'express';
import {
  ChatPathMetrics,
  ChatSurface,
  ChatTurnOutcome,
} from '../ports/chat-path-metrics';

const TERMINAL_EVENTS = new Set(['complete', 'RUN_FINISHED']);
const ERROR_EVENTS = new Set(['error', 'RUN_ERROR']);

interface SavedMessage {
  messageType?: string;
  content?: string;
  tools?: Array<{ toolName?: string }>;
}

interface SavedConversation {
  status?: string;
  messages?: SavedMessage[];
}

interface CompletePayload {
  result?: { conversation?: SavedConversation };
  conversation?: SavedConversation;
}

/** The outcome a saved conversation records for its last turn (`recordClassifiedFailureOnSession`). */
const savedOutcome = (payload: CompletePayload | null): ChatTurnOutcome => {
  const conversation = payload?.result?.conversation ?? payload?.conversation;
  if (conversation?.status === 'Stopped') return 'cancelled';
  if (conversation?.status === 'Failed') return 'failed';
  const last = conversation?.messages?.at(-1);
  if (last?.messageType === 'error') return 'failed';
  const asks = (last?.tools ?? []).some(
    (t) => t.toolName?.includes('ask_user_question') === true,
  );
  return asks && (last?.content ?? '') === '' ? 'needs_input' : 'answered';
};

/** The last terminal frame among `text`'s SSE frames, if any. */
export const terminalOutcome = (text: string): ChatTurnOutcome | null => {
  let outcome: ChatTurnOutcome | null = null;
  for (const frame of text.split('\n\n')) {
    const event = /^event: (\S+)$/m.exec(frame)?.[1];
    if (event === undefined) continue;
    if (ERROR_EVENTS.has(event)) {
      outcome = 'failed';
    } else if (TERMINAL_EVENTS.has(event)) {
      const data = /^data: (.*)$/m.exec(frame)?.[1];
      try {
        outcome = savedOutcome(
          data === undefined ? null : (JSON.parse(data) as CompletePayload),
        );
      } catch {
        outcome = 'answered';
      }
    }
  }
  return outcome;
};

/**
 * Counts a turn of today's loop (DUR3 condition f) on the legacy stream
 * routes, after the surface middleware: a request the durable path takes
 * never reaches it. The outcome is read from the frames Node itself writes
 * last (the saved conversation, or an error), so the five stream handlers
 * need no change; a response the browser abandoned is `disconnected`.
 */
export const legacyTurnMetrics =
  (surface: ChatSurface, metrics: ChatPathMetrics) =>
  (_req: Request, res: Response, next: NextFunction): void => {
    if (typeof res.write !== 'function' || typeof res.on !== 'function') {
      next();
      return;
    }
    const started = Date.now();
    let outcome: ChatTurnOutcome | null = null;
    const write = res.write.bind(res) as (...args: unknown[]) => boolean;
    (res as unknown as { write: (...args: unknown[]) => boolean }).write = (
      ...args: unknown[]
    ): boolean => {
      const chunk = args[0];
      if (typeof chunk === 'string' || Buffer.isBuffer(chunk)) {
        outcome = terminalOutcome(chunk.toString()) ?? outcome;
      }
      return write(...args);
    };
    res.on('close', () => {
      const ended = res.writableEnded || res.writableFinished;
      metrics.turnEnded(
        'legacy',
        surface,
        ended ? (outcome ?? 'failed') : 'disconnected',
        (Date.now() - started) / 1000,
      );
    });
    next();
  };

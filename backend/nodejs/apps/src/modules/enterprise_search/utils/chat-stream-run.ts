/**
 * One `chat_stream.end` line per client chat stream: how it ended, why, and what the AI service sent.
 * The handlers keep their own parsers and persistence; they only feed this what they already see.
 * No answer or error text is logged: ids (bound once), counts, classes and codes.
 */
import type { Request } from 'express';
import { bindLogContext } from '../../../libs/context/request-context';
import { BaseError } from '../../../libs/errors/base.error';
import { routeTemplate } from '../../../libs/middlewares/access-log.middleware';
import { Logger, SNAKE_TOKEN } from '../../../libs/services/logger.service';
import {
  classifyError,
  classifyRequestError,
  ErrorClass,
  levelFor,
} from '../../../libs/utils/error-class';
import type { IAIResponse } from '../types/conversation.interfaces';
import { isClassifiedFailureAnswer } from './utils';
import type { ResponseWatchable } from './stream-lifecycle';

const EVENT = 'chat_stream.end';
const BAD_FRAME = 'chat_stream.bad_frame';

const logger = Logger.getInstance({ service: 'ChatStream' });

/** Classes that are our own fault: never "already reported" by the AI call's line. */
const OUR_FAULT: ReadonlySet<ErrorClass> = new Set<ErrorClass>([
  'internal',
  'misconfigured',
]);

/** How the handler finished with the client stream. */
export type StreamEnd =
  /** finalizeRun wrote its last frame: what the run saw says how. */
  | { kind: 'answered' }
  /** The stream's `'error'` (`fromStream`), the outer catch, or a failed save. */
  | { kind: 'failed'; error: unknown; fromStream?: true }
  /** The detached-run cap fired and the partial text was saved. */
  | { kind: 'abandoned' };

interface Ending {
  reason: string;
  outcome: 'success' | 'failure' | 'partial';
  level: 'error' | 'warn' | 'info';
  error_class?: ErrorClass;
  code?: string;
  error?: unknown;
  error_type?: string;
}

/** A code from the AI service is logged only when it is a snake_case token: free text never reaches the line. */
const safeCode = (code: unknown): string =>
  typeof code === 'string' && SNAKE_TOKEN.test(code.trim())
    ? code.trim()
    : 'unknown_error';

const upstreamFailure = (code: string): Ending => ({
  reason: 'upstream_error',
  outcome: 'failure',
  level: 'warn',
  error_class: 'upstream_5xx',
  code,
});

/** A frame from the AI service that would not parse: its type and size, never its data. */
export function logBadFrame(
  frame: 'RUN_FINISHED' | 'RUN_ERROR' | 'CUSTOM',
  dataLine: string,
): void {
  // A bad CUSTOM frame changes no outcome.
  logger[frame === 'CUSTOM' ? 'debug' : 'warn'](BAD_FRAME, {
    frame,
    bytes: Buffer.byteLength(dataLine),
    event_name: BAD_FRAME,
  });
}

/** One client chat stream: its counters and its end line. */
export class ChatStreamRun {
  private readonly started = Date.now();
  private ttfbMs: number | undefined;
  private events = 0;
  private bytes = 0;
  /** A lone `\n` that ended the last chunk: the start of a terminator split across chunks. */
  private pendingNewline = false;
  private result: IAIResponse | null = null;
  private runError: { code: unknown } | undefined;
  private openError: unknown;
  private clientLeft = false;
  private ended = false;

  constructor(
    private readonly req: Request,
    res: ResponseWatchable,
  ) {
    res.on('close', () => {
      if (!res.writableEnded) this.clientLeft = true;
    });
  }

  /** Adds the conversation and agent to every later line of the request. */
  bind(ids: { conversationId: string; agentKey?: string }): void {
    bindLogContext({
      conversation_id: ids.conversationId,
      agent_id: ids.agentKey,
    });
  }

  /** Opens the AI stream; remembers the error it throws, so the end line knows the call line reported it. */
  async open<T>(start: () => Promise<T>): Promise<T> {
    try {
      return await start();
    } catch (error) {
      this.openError = error;
      throw error;
    }
  }

  /** A chunk from the AI service: bytes, SSE frames (`\n\n`, also across chunks), first-byte time. */
  observe(chunk: Buffer | string): void {
    this.ttfbMs ??= Date.now() - this.started;
    this.bytes += Buffer.byteLength(chunk);
    const parts = `${this.pendingNewline ? '\n' : ''}${chunk.toString()}`.split(
      '\n\n',
    );
    this.events += parts.length - 1;
    this.pendingNewline = parts[parts.length - 1]?.endsWith('\n') ?? false;
  }

  /** The run's final answer (the last one wins, as the handlers' `completeData`). */
  sawResult(result: IAIResponse | null): void {
    this.result = result;
  }

  /** The AI service's RUN_ERROR, with its raw `code` (the first one wins). */
  sawRunError(code: unknown): void {
    this.runError ??= { code };
  }

  /** Writes the end line, once. Never throws. */
  end(how: StreamEnd): void {
    if (this.ended) return;
    this.ended = true;
    try {
      const { level, reason, ...fields } = this.ending(how);
      logger[level](EVENT, {
        ...fields,
        reason: this.clientLeft ? 'client_disconnect' : reason,
        route: routeTemplate(this.req),
        events: this.events,
        bytes: this.bytes,
        ttfb_ms: this.ttfbMs,
        duration_ms: Date.now() - this.started,
        event_name: EVENT,
      });
    } catch {
      // A log line that cannot be written must never fail the stream it describes.
    }
  }

  /** The rows are matched in order; the first match wins. */
  private ending(how: StreamEnd): Ending {
    if (how.kind === 'abandoned') {
      return {
        reason: 'client_disconnect',
        outcome: 'partial',
        level: 'warn',
        code: 'detached_cap',
      };
    }
    return how.kind === 'answered'
      ? this.answered()
      : this.failed(how.error, how.fromStream === true);
  }

  private answered(): Ending {
    const { result } = this;
    if (result?.status === 'stopped') {
      return { reason: 'cancelled', outcome: 'partial', level: 'info' };
    }
    if (result && isClassifiedFailureAnswer(result)) {
      return upstreamFailure(safeCode(result.errorCode));
    }
    if (result)
      return { reason: 'completed', outcome: 'success', level: 'info' };
    return upstreamFailure(
      this.runError ? safeCode(this.runError.code) : 'no_response',
    );
  }

  private failed(error: unknown, fromStream: boolean): Ending {
    const errorClass = classifyError(error);
    // The AI call's own line already reported it at its level: say how the stream ended, at INFO.
    if (
      (fromStream || error === this.openError) &&
      !OUR_FAULT.has(errorClass)
    ) {
      return {
        reason: 'upstream_error',
        outcome: 'failure',
        level: 'info',
        error_class: errorClass,
      };
    }
    if (error instanceof BaseError && error.statusCode < 500) {
      return {
        reason: 'rejected',
        outcome: 'failure',
        level: levelFor(errorClass),
        error_class: errorClass,
        error_type: error.name,
      };
    }
    const requestClass = classifyRequestError(error, 500);
    return {
      reason: 'internal_error',
      outcome: 'failure',
      level: levelFor(requestClass),
      error_class: requestClass,
      error,
      error_type: error instanceof Error ? error.name : undefined,
    };
  }
}

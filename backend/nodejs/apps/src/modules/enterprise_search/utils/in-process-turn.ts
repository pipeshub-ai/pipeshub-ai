import { EventEmitter } from 'node:events';
import { randomUUID } from 'node:crypto';
import type { NextFunction, Request, RequestHandler, Response } from 'express';
import { BaseError } from '../../../libs/errors/base.error';
import type {
  ClassicTurnRequest,
  ClassicTurns,
} from '../../agent-harness/application/classic-schedule-runner';
import { markScheduledTurn } from '../../agent-harness/transport/scheduled-turn-mark';

/**
 * Where a turn started in this process writes its response (ADR-SCH-02 D3): the subset of an Express response the
 * chat pipeline uses, read by the runner instead of sent to a socket. It parses the stream's SSE frames only to learn
 * the conversation the turn opened.
 */
export class TurnResponseSink extends EventEmitter {
  statusCode = 200;
  headersSent = false;
  writableEnded = false;
  writableFinished = false;
  locals: Record<string, unknown> = {};
  private readonly headers = new Map<string, unknown>();
  private pending = '';
  private conversationSeen: ((id: string) => void) | null = null;
  /** The body of an answer that was not a stream (a refusal the pipeline answered itself). */
  body: unknown = undefined;
  readonly done: Promise<void>;
  private finish!: () => void;

  constructor() {
    super();
    this.done = new Promise((resolve) => {
      this.finish = resolve;
    });
  }

  /** Resolves with the conversation the turn's `connected` frame names. */
  onConversation(listener: (id: string) => void): void {
    this.conversationSeen = listener;
  }

  status(code: number): this {
    this.statusCode = code;
    return this;
  }

  setHeader(name: string, value: unknown): this {
    this.headers.set(name.toLowerCase(), value);
    return this;
  }

  getHeader(name: string): unknown {
    return this.headers.get(name.toLowerCase());
  }

  removeHeader(name: string): void {
    this.headers.delete(name.toLowerCase());
  }

  writeHead(code: number, headers?: Record<string, unknown>): this {
    this.statusCode = code;
    for (const [name, value] of Object.entries(headers ?? {})) {
      this.setHeader(name, value);
    }
    this.headersSent = true;
    return this;
  }

  flushHeaders(): void {
    this.headersSent = true;
  }

  flush(): void {}

  write(chunk: unknown): boolean {
    if (this.writableEnded) return false;
    this.headersSent = true;
    this.read(chunkText(chunk));
    return true;
  }

  json(body: unknown): this {
    this.body = body;
    return this.end();
  }

  send(body: unknown): this {
    this.body = body;
    return this.end();
  }

  end(chunk?: unknown): this {
    if (this.writableEnded) return this;
    this.read(chunkText(chunk));
    this.headersSent = true;
    this.writableEnded = true;
    this.writableFinished = true;
    this.emit('finish');
    this.emit('close');
    this.finish();
    return this;
  }

  private read(text: string): void {
    if (this.conversationSeen === null) return;
    this.pending += text;
    const frames = this.pending.split('\n\n');
    this.pending = frames.pop() ?? '';
    for (const frame of frames) {
      const data = frame
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trim())
        .join('');
      if (data === '') continue;
      try {
        const id = conversationOfFrame(JSON.parse(data) as unknown);
        if (id !== null) {
          const seen = this.conversationSeen;
          this.conversationSeen = null;
          this.pending = '';
          seen?.(id);
          return;
        }
      } catch {
        // A frame that is not JSON is not the one naming the conversation.
      }
    }
  }
}

/**
 * The conversation a frame announces: the legacy stream's `connected` event or the AG-UI stream's
 * `CUSTOM conversation_created` (the chat routes' default protocol).
 */
function conversationOfFrame(frame: unknown): string | null {
  if (typeof frame !== 'object' || frame === null) return null;
  const f = frame as {
    type?: unknown;
    name?: unknown;
    value?: { conversationId?: unknown };
    message?: unknown;
    conversationId?: unknown;
  };
  const id =
    f.type === 'CUSTOM' && f.name === 'conversation_created'
      ? f.value?.conversationId
      : f.type === undefined
        ? f.conversationId
        : undefined;
  return typeof id === 'string' && id !== '' ? id : null;
}

/** A firing's refusal reason for an error the pipeline gave before the turn opened its conversation. */
export function turnRefusalReason(error: unknown, status?: number): string {
  const code = error instanceof BaseError ? error.statusCode : status;
  if (code === 401 || code === 403) return 'agent_forbidden';
  if (code === 404) return 'not_found';
  if (code === 400 || code === 422) return 'turn_invalid';
  if (code === 429) return 'quota';
  return 'unavailable';
}

/**
 * Runs Express handlers in order on one request, as the router would: each one's `next()` calls the following one, a
 * `next(error)` stops the chain. Resolves when the chain stops; the last handler's stream may still be writing.
 */
export function runHandlers(
  handlers: readonly RequestHandler[],
  req: Request,
  res: Response,
): Promise<{ error?: unknown }> {
  return new Promise((resolve) => {
    let index = 0;
    const next: NextFunction = (error?: unknown) => {
      if (error !== undefined && error !== null && error !== 'route') {
        resolve({ error });
        return;
      }
      const position = index;
      const handler = handlers[position];
      index += 1;
      if (handler === undefined) {
        resolve({});
        return;
      }
      // Only the last handler's own end ends the chain: an earlier one may still be awaiting after it called next().
      const last = position === handlers.length - 1;
      try {
        const out = handler(req, res, next) as unknown;
        if (out instanceof Promise) {
          out.then(
            () => {
              if (last) resolve({});
            },
            (thrown: unknown) => {
              resolve({ error: thrown });
            },
          );
        } else if (last) {
          resolve({});
        }
      } catch (thrown) {
        resolve({ error: thrown });
      }
    };
    next();
  });
}

/** The two chat pipelines a scheduled classic turn goes through: the assistant's and an agent's. */
export interface ClassicTurnPipelines {
  assistant: readonly RequestHandler[];
  agent: readonly RequestHandler[];
}

/**
 * Starts a schedule's classic turn through the chat route's own handlers (ADR-SCH-02 D3), with the actor's minted
 * credential and no browser. `start` settles once the turn opened its conversation or was refused before it.
 */
export class InProcessClassicTurns implements ClassicTurns {
  constructor(private readonly pipelines: () => ClassicTurnPipelines) {}

  async start(
    request: ClassicTurnRequest,
  ): Promise<{ conversationId: string } | { refused: string }> {
    const sink = new TurnResponseSink();
    const req = turnRequest(request);
    markScheduledTurn(req, request.mark);
    const handlers =
      request.agentKey === undefined
        ? this.pipelines().assistant
        : this.pipelines().agent;
    const opened = new Promise<string>((resolve) => {
      sink.onConversation(resolve);
    });
    void sink.done.then(() => request.ended?.());
    const stopped = runHandlers(handlers, req, sink as unknown as Response);
    const first = await Promise.race([
      opened.then((conversationId) => ({ conversationId })),
      stopped.then((outcome) => ({ outcome })),
      sink.done.then(() => ({ ended: true as const })),
    ]);
    if ('conversationId' in first) return first;
    // The chain stopped or answered without opening a conversation: it refused the turn.
    const outcome = 'outcome' in first ? first.outcome : await stopped;
    return {
      refused: turnRefusalReason(outcome.error, sink.statusCode),
    };
  }
}

/** What a handler wrote, as text; anything else (a callback `end` was given) is nothing. */
function chunkText(chunk: unknown): string {
  if (typeof chunk === 'string') return chunk;
  if (Buffer.isBuffer(chunk)) return chunk.toString('utf8');
  return '';
}

function turnRequest(request: ClassicTurnRequest): Request {
  const path =
    request.agentKey === undefined
      ? '/api/v1/conversations/stream'
      : `/api/v1/agents/${encodeURIComponent(request.agentKey)}/conversations/stream`;
  const headers: Record<string, string> = {
    authorization: request.authorization,
    'content-type': 'application/json',
  };
  const header = (name: string): string | undefined =>
    headers[name.toLowerCase()];
  const req = Object.assign(new EventEmitter(), {
    method: 'POST',
    url: path,
    originalUrl: path,
    baseUrl: '',
    path,
    headers,
    body: structuredClone(request.body),
    params:
      request.agentKey !== undefined ? { agentKey: request.agentKey } : {},
    query: {},
    cookies: {},
    ip: '127.0.0.1',
    context: { requestId: randomUUID() },
    get: header,
    header,
  });
  return req as unknown as Request;
}

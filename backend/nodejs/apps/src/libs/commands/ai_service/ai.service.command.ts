import { AIServiceResponse } from '../../../modules/enterprise_search/types/conversation.interfaces';
import { HttpMethod } from '../../enums/http-methods.enum';
import { BaseCommand } from '../command.interface';
import {
  endUpstreamCall,
  upstreamFetch,
  UpstreamCall,
} from '../../http/upstream-call';
import { Readable } from 'stream';

export interface AICommandOptions {
  uri: string;
  method: HttpMethod;
  headers?: Record<string, string>;
  queryParams?: Record<string, string | number | boolean>;
  body?: any;
  // Total budget across all attempts and the waits between them: once it runs
  // out no further attempt is made.
  timeoutMs?: number;
  maxAttempts?: number;
  signal?: AbortSignal;
  /**
   * A schedule's classic turn (ADR-SCH-02 D4): its firing and ask deadline, sent past the header allowlist. Given
   * only by the turn's own handler, never read from the request, so no client can send them.
   */
  scheduledTurnHeaders?: Record<string, string>;
}

interface FetchCommandError extends Error {
  response?: {
    status: number;
    data: unknown;
  };
  status?: number;
  statusText?: string;
}

const AI_SERVICE_TIMEOUT_MS = 620_000;

/**
 * Node's signed resolution of a legacy chat turn (CPD-10 §3). Only the query
 * service reads it, so only this command forwards it past the allow-list;
 * the chat proxy sites are its one source, never the client's request.
 */
export const AI_RESOLVED_TURN_HEADER = 'x-pipeshub-resolved';

export class AIServiceCommand<T> extends BaseCommand<AIServiceResponse<T>> {
  private scheduledTurn: Record<string, string> | undefined;
  private method: HttpMethod;
  private body?: any;
  private timeoutMs: number;
  private maxAttempts: number;
  private readonly signal?: AbortSignal;

  constructor(options: AICommandOptions) {
    super(options.uri, options.queryParams, options.headers);
    this.method = options.method;
    this.signal = options.signal;
    this.body = this.sanitizeBody(options.body);
    this.scheduledTurn = { ...(options.scheduledTurnHeaders ?? {}) };
    this.headers = this.sanitizeHeaders(options.headers!);
    this.timeoutMs = options.timeoutMs ?? AI_SERVICE_TIMEOUT_MS;
    this.maxAttempts = options.maxAttempts ?? 3;
  }

  protected override sanitizeHeaders(
    headers: Record<string, string>,
  ): Record<string, string> {
    const sanitized = {
      ...super.sanitizeHeaders(headers),
      ...(this.scheduledTurn ?? {}),
    };
    const resolved = headers[AI_RESOLVED_TURN_HEADER];
    return typeof resolved === 'string' && resolved.length > 0
      ? { ...sanitized, [AI_RESOLVED_TURN_HEADER]: resolved }
      : sanitized;
  }

  // Execute the HTTP request based on the provided options.
  public async execute(): Promise<AIServiceResponse<T>> {
    const url = this.buildUrl();
    const sanitizedHeaders = this.sanitizeHeaders(this.headers);
    const budget = AbortSignal.timeout(this.timeoutMs);
    const requestOptions: RequestInit = {
      method: this.method,
      headers: sanitizedHeaders,
      body: this.body,
      // Above the AI service's own ceiling (600s for a first-time
      // embedding-model download, `health.py`'s HEALTH_CHECK_TIMEOUT).
      // Without it, undici's ~300s default cuts the call off first and the
      // caller sees a transport error instead of the "model is still
      // downloading, retry shortly" message the health check produced.
      // Not applied to executeStream(), whose SSE progress stream is meant to
      // outlive any single request.
      signal: budget,
    };

    const response = await this.fetchWithRetry(
      (attempt) => upstreamFetch(url, requestOptions, attempt),
      this.maxAttempts,
      300,
      undefined,
      budget,
    );

    return {
      statusCode: response.status,
      data: await this.readBody<T>(response),
      msg: response.statusText,
      ...this.retryAfterHeaders(response),
    };
  }

  private parseErrorPayload(rawPayload: string): unknown {
    const normalizedPayload = rawPayload.trim();
    if (!normalizedPayload) {
      return null;
    }

    try {
      return JSON.parse(normalizedPayload);
    } catch {
      // Fall through to SSE/plain-text parsing.
    }

    const sseDataLines = normalizedPayload
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.replace(/^data:\s?/, '').trim())
      .filter(Boolean);

    if (sseDataLines.length > 0) {
      const sseDataPayload = sseDataLines.join('\n');
      try {
        return JSON.parse(sseDataPayload);
      } catch {
        return sseDataPayload;
      }
    }

    return normalizedPayload;
  }

  private async buildStreamHttpError(response: Response): Promise<FetchCommandError> {
    let responsePayload: unknown = null;
    try {
      responsePayload = this.parseErrorPayload(await response.text());
    } catch {
      responsePayload = null;
    }

    const payloadObject =
      responsePayload && typeof responsePayload === 'object'
        ? responsePayload
        : null;
    const payloadRecord = payloadObject as Record<string, unknown> | null;

    // Python sends `detail` as text, or as `{ code, message, ... }` (a 402 refusal
    // does); an object here would become the message "[object Object]".
    const textOf = (value: unknown): string | undefined => {
      const text =
        value !== null && typeof value === 'object'
          ? (value as Record<string, unknown>).message
          : value;
      return typeof text === 'string' && text !== '' ? text : undefined;
    };
    const errorDetail =
      textOf(payloadRecord?.detail) ??
      textOf(payloadRecord?.message) ??
      textOf(payloadRecord?.error) ??
      textOf(responsePayload) ??
      textOf(response.statusText) ??
      `HTTP error! status: ${response.status}`;

    const error = new Error(errorDetail) as FetchCommandError;
    error.response = {
      status: response.status,
      data:
        payloadObject ||
        {
          detail: errorDetail,
          message: errorDetail,
        },
    };
    error.status = response.status;
    error.statusText = response.statusText;
    return error;
  }

  // Execute streaming request
  public async executeStream(signal?: AbortSignal): Promise<Readable> {
    const url = this.buildUrl();
    const sanitizedHeaders = this.sanitizeHeaders(this.headers);
    const abortController = new AbortController();
    const callerSignal = AbortSignal.any(
      [this.signal, signal].filter((s): s is AbortSignal => s !== undefined),
    );
    const requestOptions: RequestInit = {
      method: this.method,
      headers: sanitizedHeaders,
      body: this.body,
      // Propagates a client disconnect to the upstream fetch — without this,
      // aborting on our end only tore down the Node-side Readable while
      // Python kept generating against a socket nobody was reading.
      signal: AbortSignal.any([abortController.signal, callerSignal]),
    };

    // A 2xx with a body is held: its call line is written when the stream ends, not at its headers.
    const held: { call?: UpstreamCall } = {};
    const end = (result: { status: number; error?: unknown }): void => {
      if (held.call) endUpstreamCall(held.call, result);
      held.call = undefined;
    };
    let response: Response;
    try {
      response = await this.fetchWithRetry(
        (attempt) => {
          requestOptions.signal?.throwIfAborted();
          return upstreamFetch(url, requestOptions, attempt, (call) => {
            held.call = call;
          });
        },
        this.maxAttempts,
        300,
        undefined,
        callerSignal,
      );
    } catch (error: unknown) {
      abortController.abort();
      throw error;
    }

    if (!response.ok) {
      let error: FetchCommandError;
      try {
        error = await this.buildStreamHttpError(response);
      } catch {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      throw error;
    }

    if (!response.body) {
      end({ status: response.status, error: new Error('Response body is null') });
      throw new Error('Response body is null');
    }

    const reader = response.body.getReader();
    let finished = false;
    let released = false;
    const release = () => {
      if (!released) {
        reader.releaseLock();
        released = true;
      }
    };
    const readable = new Readable({
      encoding: 'utf8',
      // Read only on demand so a slow consumer bounds the upstream buffer.
      read() {
        void reader.read().then(
          ({ done, value }) => {
            if (this.destroyed) return;
            if (done) {
              finished = true;
              end({ status: response.status });
              release();
              this.push(null);
            } else {
              this.push(Buffer.from(value));
            }
          },
          (error: unknown) => {
            this.destroy(error instanceof Error ? error : new Error('Stream read failed'));
          },
        );
      },
      destroy(error, callback) {
        if (finished) {
          callback(error);
          return;
        }
        end({ status: response.status, error: error ?? new Error('Stream closed before it ended') });
        abortController.abort();
        void reader.cancel().then(
          () => { release(); callback(error); },
          () => { release(); callback(error); },
        );
      },
    });

    return readable;
  }
}

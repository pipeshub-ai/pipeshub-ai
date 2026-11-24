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

export class AIServiceCommand<T> extends BaseCommand<AIServiceResponse<T>> {
  private method: HttpMethod;
  private body?: any;
  private timeoutMs: number;
  private maxAttempts: number;

  constructor(options: AICommandOptions) {
    super(options.uri, options.queryParams, options.headers);
    this.method = options.method;
    this.body = this.sanitizeBody(options.body);
    this.headers = this.sanitizeHeaders(options.headers!);
    this.timeoutMs = options.timeoutMs ?? AI_SERVICE_TIMEOUT_MS;
    this.maxAttempts = options.maxAttempts ?? 3;
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

    // Assuming the response is JSON; adjust if needed.
    const data = await response.json();
    const retryAfter = response.headers.get('retry-after');
    return {
      statusCode: response.status,
      data: data,
      msg: response.statusText,
      ...(retryAfter ? { headers: { 'retry-after': retryAfter } } : {}),
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
    const requestOptions: RequestInit = {
      method: this.method,
      headers: sanitizedHeaders,
      body: this.body,
      // Lets Node abandon a run nobody is waiting for (see `attachUpstreamAbort`);
      // aborting only the Readable would leave Python generating into the void.
      ...(signal ? { signal } : {}),
    };

    // A 2xx with a body is held: its call line is written when the stream ends, not at its headers.
    const held: { call?: UpstreamCall } = {};
    const response = await this.fetchWithRetry(
      (attempt) =>
        upstreamFetch(url, requestOptions, attempt, (call) => {
          held.call = call;
        }),
      this.maxAttempts,
      300,
      undefined,
      signal,
    );
    const end = (result: { status: number; error?: unknown }): void => {
      if (held.call) endUpstreamCall(held.call, result);
    };

    if (!response.ok) {
      let error: FetchCommandError;
      try{
        error = await this.buildStreamHttpError(response);
      }
      catch (error) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      throw error;
    }

    if (!response.body) {
      throw new Error('Response body is null');
    }

    // Convert ReadableStream to Node.js Readable
    const readable = new Readable({
      read() {}
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    const pump = async () => {
      try {
        while (true) {
          const { done, value } = await reader.read();
          
          if (done) {
            end({ status: response.status });
            readable.push(null);
            break;
          }
          
          const chunk = decoder.decode(value, { stream: true });
          readable.push(chunk);
        }
      } catch (error) {
        end({ status: response.status, error });
        readable.destroy(error as Error);
      }
    };

    pump();

    return readable;
  }
}

import { HttpMethod } from '../../enums/http-methods.enum';
import { BaseCommand } from '../command.interface';
import { Readable } from 'stream';
import { upstreamFetch } from '../../http/upstream-call';

export interface ConnectorServiceCommandOptions {
  uri: string;
  method: HttpMethod;
  headers?: Record<string, string>;
  queryParams?: Record<string, string | number | boolean>;
  body?: any;
  /** Abort an attempt after this long. A timed-out request is not retried: the service
   * may still be doing the work. */
  timeoutMs?: number;
  /** Attempts in all, the first included. Defaults to 3; pass 1 for a request that must not
   * be sent twice. */
  retries?: number;
}

export interface ConnectorServiceResponse<T> {
  statusCode: number;
  data?: T;
  msg?: string;
  headers?: Record<string, string>;
}

const isTimeout = (error: unknown): boolean =>
  (error as { name?: unknown } | null)?.name === 'TimeoutError';

export class ConnectorServiceCommand<T> extends BaseCommand<ConnectorServiceResponse<T>> {
  private method: HttpMethod;
  private body?: any;
  private timeoutMs?: number;
  private retries: number;

  constructor(options: ConnectorServiceCommandOptions) {
    super(options.uri, options.queryParams, options.headers);
    this.method = options.method;
    this.body = this.sanitizeBody(options.body);
    this.headers = this.sanitizeHeaders(options.headers || {});
    this.timeoutMs = options.timeoutMs;
    this.retries = options.retries ?? 3;
  }

  private attempt(
    url: string,
    requestOptions: RequestInit,
    attempt: number,
  ): Promise<Response> {
    return upstreamFetch(
      url,
      this.timeoutMs
        ? { ...requestOptions, signal: AbortSignal.timeout(this.timeoutMs) }
        : requestOptions,
      attempt,
    );
  }
  
  // Execute the HTTP request based on the provided options.
  public async execute(): Promise<ConnectorServiceResponse<T>> {
    const url = this.buildUrl();
    const requestOptions: RequestInit = {
      method: this.method,
      headers: this.headers,
      body: this.body,
    };

    const response = await this.fetchWithRetry(
      (n) => this.attempt(url, requestOptions, n),
      this.retries,
      300,
      (error) => !isTimeout(error),
    );

    // A 204 has no body to parse; parsing it would throw and turn a success into a 500.
    const data = response.status === 204 ? undefined : await response.json();
    
    // Convert Headers object to plain object
    const responseHeaders: Record<string, string> = {};
    response.headers.forEach((value, key) => {
      responseHeaders[key] = value;
    });
    
    return {
      statusCode: response.status,
      data: data,
      msg: response.statusText,
      headers: responseHeaders,
    };
  }

  // Execute streaming request
  public async executeStream(): Promise<Readable> {
    const url = this.buildUrl();
    const requestOptions: RequestInit = {
      method: this.method,
      headers: this.headers,
      body: this.body,
    };

    const response = await this.fetchWithRetry(
      (attempt) => upstreamFetch(url, requestOptions, attempt),
      3,
      300,
    );

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
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
            readable.push(null);
            break;
          }
          
          const chunk = decoder.decode(value, { stream: true });
          readable.push(chunk);
        }
      } catch (error) {
        readable.destroy(error as Error);
      }
    };

    pump();

    return readable;
  }
}

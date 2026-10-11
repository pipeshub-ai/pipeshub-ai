import { HttpMethod } from '../../enums/http-methods.enum';
import { Logger } from '../../services/logger.service';
import { BaseCommand } from '../command.interface';
import { Readable } from 'stream';
import { logSafeUrl } from '../log-safe-url';

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

const logger = Logger.getInstance({
  service: 'ConnectorServiceCommand',
});

const isTimeout = (error: unknown): boolean => (error as { name?: unknown } | null)?.name === 'TimeoutError';

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

  private attempt(url: string, requestOptions: RequestInit): Promise<Response> {
    return fetch(url, this.timeoutMs ? { ...requestOptions, signal: AbortSignal.timeout(this.timeoutMs) } : requestOptions);
  }
  
  // Execute the HTTP request based on the provided options.
  public async execute(): Promise<ConnectorServiceResponse<T>> {
    const url = this.buildUrl();
    const requestOptions: RequestInit = {
      method: this.method,
      headers: this.headers,
      body: this.body,
    };

    try {
      const response = await this.fetchWithRetry(
        async () => this.attempt(url, requestOptions),
        this.retries,
        300,
        (error) => !isTimeout(error),
      );

      logger.debug('Connector service command success', {
        url: logSafeUrl(url),
        statusCode: response.status,
        statusText: response.statusText,
      });

      // Assuming the response is JSON; adjust if needed.
      const data = await response.json();
      
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
    } catch (error: any) {
      logger.error('Connector service command failed', {
        error: error.message,
        url: logSafeUrl(url),
        // Headers carry the caller's bearer token and bodies carry connector secrets.
        method: this.method,
      });
      throw error;
    }
  }

  // Execute streaming request
  public async executeStream(): Promise<Readable> {
    const url = this.buildUrl();
    const requestOptions: RequestInit = {
      method: this.method,
      headers: this.headers,
      body: this.body,
    };

    try {
      const response = await this.fetchWithRetry(
        async () => fetch(url, requestOptions),
        3,
        300,
      );

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      if (!response.body) {
        throw new Error('Response body is null');
      }

      logger.info('Connector service streaming command success', {
        url: logSafeUrl(url),
        statusCode: response.status,
        statusText: response.statusText,
      });

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
    } catch (error: any) {
      logger.error('Connector service streaming command failed', {
        error: error.message,
        url: logSafeUrl(url),
        // Headers carry the caller's bearer token and bodies carry connector secrets.
        method: this.method,
      });
      throw error;
    }
  }
}

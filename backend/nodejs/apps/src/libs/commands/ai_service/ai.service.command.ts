import { AIServiceResponse } from '../../../modules/enterprise_search/types/conversation.interfaces';
import { HttpMethod } from '../../enums/http-methods.enum';
import { Logger } from '../../services/logger.service';
import { BaseCommand } from '../command.interface';
import { Readable } from 'stream';

export interface AICommandOptions {
  uri: string;
  method: HttpMethod;
  headers?: Record<string, string>;
  queryParams?: Record<string, string | number | boolean>;
  body?: any;
}

interface FetchCommandError extends Error {
  response?: {
    status: number;
    data: unknown;
  };
  status?: number;
  statusText?: string;
}

const logger = Logger.getInstance({
  service: 'AIServiceCommand',
});

export class AIServiceCommand<T> extends BaseCommand<AIServiceResponse<T>> {
  private method: HttpMethod;
  private body?: any;

  constructor(options: AICommandOptions) {
    super(options.uri, options.queryParams, options.headers);
    this.method = options.method;
    this.body = this.sanitizeBody(options.body);
    this.headers = this.sanitizeHeaders(options.headers!);
  }
  
  // Execute the HTTP request based on the provided options.
  public async execute(): Promise<AIServiceResponse<T>> {
    const url = this.buildUrl();
    const sanitizedHeaders = this.sanitizeHeaders(this.headers);
    const requestOptions: RequestInit = {
      method: this.method,
      headers: sanitizedHeaders,
      body: this.body,
    };

    try {
      const response = await this.fetchWithRetry(
        async () => fetch(url, requestOptions),
        3,
        300,
      );

      logger.info('AI service command success', {
        url: url,
        statusCode: response.status,
        statusText: response.statusText,
      });

      // Assuming the response is JSON; adjust if needed.
      const data = await response.json();
      return {
        statusCode: response.status,
        data: data,
        msg: response.statusText,
      };
    } catch (error: any) {
      logger.error('AI service command failed', {
        error: error.message,
        url: url,
        requestOptions: requestOptions,
      });
      throw error;
    }
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

    const errorDetail =
      (payloadRecord?.detail as string | undefined) ||
      (payloadRecord?.message as string | undefined) ||
      (payloadRecord?.error as string | undefined) ||
      (typeof responsePayload === 'string' ? responsePayload : undefined) ||
      response.statusText ||
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
  public async executeStream(): Promise<Readable> {
    const url = this.buildUrl();
    const sanitizedHeaders = this.sanitizeHeaders(this.headers);
    const requestOptions: RequestInit = {
      method: this.method,
      headers: sanitizedHeaders,
      body: this.body,
    };

    try {
      const response = await this.fetchWithRetry(
        async () => fetch(url, requestOptions),
        3,
        300,
      );

      if (!response.ok) {
        try{
          throw await this.buildStreamHttpError(response);
        }
        catch {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
      }

      if (!response.body) {
        throw new Error('Response body is null');
      }

      logger.info('AI service streaming command success', {
        url: url,
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
      logger.error('AI service streaming command failed', {
        error: error.message,
        url: url,
        requestOptions: requestOptions,
      });
      throw error;
    }
  }
}

import { HttpMethod } from '../../enums/http-methods.enum';
import { BaseCommand } from '../command.interface';
import { Logger } from '../../services/logger.service';

const logger = Logger.getInstance({
  service: 'StorageServiceCommand',
});

export interface StorageCommandOptions {
  uri: string;
  method: HttpMethod;
  headers?: Record<string, string>;
  queryParams?: Record<string, string | number | boolean>;
  // For methods that support a request body (PUT, POST, PATCH).
  body?: any;
}

export class StorageServiceCommand extends BaseCommand<Response> {
  private method: HttpMethod;
  private body?: any;

  constructor(options: StorageCommandOptions) {
    super(options.uri, options.queryParams, options.headers);
    this.method = options.method;
    this.body = this.sanitizeBody(options.body);
    this.headers = this.sanitizeHeaders(options.headers!);
  }

  // Execute the HTTP request based on the provided options.
  public async execute(): Promise<Response> {
    const url = this.buildUrl();
    const requestOptions: RequestInit = {
      method: this.method,
      headers: this.headers,
    };

    // If a body is provided by the caller, pass it as-is.
    if (this.body !== undefined) {
      requestOptions.body = this.body;
    }

    try {
      const response = await this.fetchWithRetry(
        async () => fetch(url, requestOptions),
        3,
        300,
      );

      // Assuming the response is JSON; adjust as needed.
      const data = await response.json();
      return data;
    } catch (error: any) {
      logger.error('Storage service command failed', {
        error: error.message,
        url: url,
      });
      throw error;
    }
  }
}

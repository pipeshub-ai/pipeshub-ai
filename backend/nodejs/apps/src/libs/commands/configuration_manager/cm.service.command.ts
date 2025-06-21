import { HttpMethod } from '../../enums/http-methods.enum';
import { BaseCommand } from '../command.interface';
import { Logger } from '../../services/logger.service';

const logger = Logger.getInstance({
  service: 'ConfigurationManagerServiceCommand',
});

export interface ConfigurationManagerCommandOptions {
  uri: string;
  method: HttpMethod;
  headers?: Record<string, string>;
  queryParams?: Record<string, string | number | boolean>;
  // For methods that support a request body (PUT, POST, PATCH).
  body?: any;
}

export interface ConfigurationManagerResponse {
  statusCode: number;
  data?: any;
  msg?: string;
}

export class ConfigurationManagerServiceCommand extends BaseCommand<ConfigurationManagerResponse> {
  private method: HttpMethod;
  private body?: any;

  constructor(options: ConfigurationManagerCommandOptions) {
    super(options.uri, options.queryParams, options.headers);
    this.method = options.method;
    this.body = this.sanitizeBody(options.body);
    this.headers = this.sanitizeHeaders(options.headers!);
  }

  // Execute the HTTP request based on the provided options.
  public async execute(): Promise<ConfigurationManagerResponse> {
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

      logger.debug('Configuration Manager service command response', {
        statusCode: response.status,
        statusText: response.statusText,
        url: url,
      });

      // Assuming the response is JSON; adjust as needed.
      const data = await response.json();
      return {
        statusCode: response.status,
        data: data,
        msg: response.statusText,
      };
    } catch (error: any) {
      logger.error('Configuration Manager service command failed', {
        error: error.message,
        url: url,
        requestOptions: requestOptions,
      });
      throw error;
    }
  }
}

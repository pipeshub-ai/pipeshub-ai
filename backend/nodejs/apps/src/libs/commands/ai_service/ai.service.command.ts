import { AIServiceResponse } from '../../../modules/enterprise_search/types/conversation.interfaces';
import { HttpMethod } from '../../enums/http-methods.enum';
import { Logger } from '../../services/logger.service';
import { BaseCommand } from '../command.interface';

export interface AICommandOptions {
  uri: string;
  method: HttpMethod;
  headers?: Record<string, string>;
  queryParams?: Record<string, string | number | boolean>;
  body?: any;
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
}

import { HttpMethod } from '../../enums/http-methods.enum';
import { BaseCommand } from '../command.interface';
import { upstreamFetch } from '../../http/upstream-call';

export interface IAMCommandOptions {
  uri: string;
  method: HttpMethod;
  headers?: Record<string, string>;
  queryParams?: Record<string, string | number | boolean>;
  // For methods that support a request body (PUT, POST, PATCH).
  body?: any;
}

export interface IAMResponse {
  statusCode: number;
  data?: any;
  msg?: string;
}

export class IAMServiceCommand extends BaseCommand<IAMResponse> {
  private method: HttpMethod;
  private body?: any;

  constructor(options: IAMCommandOptions) {
    super(options.uri, options.queryParams, options.headers);
    this.method = options.method;
    this.body = this.sanitizeBody(options.body);
    this.headers = this.sanitizeHeaders(options.headers!);
  }

  // Execute the HTTP request based on the provided options.
  public async execute(): Promise<IAMResponse> {
    const url = this.buildUrl();
    const requestOptions: RequestInit = {
      method: this.method,
      headers: this.headers,
    };

    // If a body is provided by the caller, pass it as-is.
    if (this.body !== undefined) {
      requestOptions.body = this.body;
    }

    const response = await this.fetchWithRetry(
      (attempt) => upstreamFetch(url, requestOptions, attempt),
      3,
      300,
    );

    // Assuming the response is JSON; adjust as needed.
    const data = await response.json();
    return {
      statusCode: response.status,
      data: data,
      msg: response.statusText,
    };
  }
}

import { InternalServerError } from "../errors/http.errors";

export interface ICommand<T> {
  execute(): Promise<T>;
}

export abstract class BaseCommand<T> implements ICommand<T> {
  protected uri: string;
  protected queryParams?: Record<string, string | number | boolean>;
  protected headers: Record<string, string>;

  constructor(
    uri: string,
    queryParams?: Record<string, string | number | boolean>,
    headers?: Record<string, string>,
  ) {
    this.uri = uri;
    this.queryParams = queryParams;
    this.headers = headers || {};
  }

  // Helper to build the full URL including query parameters.
  protected buildUrl(): string {
    if (!this.queryParams) return this.uri;
    const queryString = new URLSearchParams(
      Object.entries(this.queryParams).reduce<Record<string, string>>(
        (acc, [key, value]) => {
          acc[key] = String(value);
          return acc;
        },
        {},
      ),
    ).toString();
    return `${this.uri}?${queryString}`;
  }

  protected sanitizeBody(body: any): any {
    // Check if it's a FormData object by looking for typical FormData properties
    if (
      body &&
      typeof body === 'object' &&
      body._boundary
    ) {
      return body;
    }
    return typeof body === 'string' ? body : JSON.stringify(body);
  }

  protected sanitizeHeaders(
    headers: Record<string, string>,
  ): Record<string, string> {
    // Define allowed headers in lowercase.
    const allowedHeaders = new Set(['content-type', 'authorization']);
    // Ensure content-type is set to application/json if not present
    if (!headers['content-type'] && !headers['Content-Type']) {
      headers['content-type'] = 'application/json';
    }
    return Object.fromEntries(
      Object.entries(headers).filter(([key]) =>
        allowedHeaders.has(key.toLowerCase()),
      ),
    );
  }

  /**
   * Retry helper method using exponential backoff.
   * @param fn - A function returning a promise to retry.
   * @param retries - Number of retries (default is 3).
   * @param backoff - Initial backoff delay in milliseconds (default is 300ms).
   */
  protected async fetchWithRetry<T>(
    fn: () => Promise<T>,
    retries: number = 3,
    backoff: number = 300,
  ): Promise<T> {
    let attempt = 0;
    while (attempt < retries) {
      try {
        const response = await fn();
        return response;
      } catch (error) {
        attempt++;
        if (attempt >= retries) {
          throw error;
        }
        // Calculate exponential backoff delay.
        const delay = backoff * Math.pow(2, attempt);
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
    // Should never reach here.
    throw new InternalServerError('Unexpected error in retry logic.');
  }

  public abstract execute(): Promise<T>;
}

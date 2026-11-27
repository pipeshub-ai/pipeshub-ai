import { InternalServerError } from "../errors/http.errors";
import {
  injectRequestHeaders,
  HEADER_REQUEST_ID,
  HEADER_MCP_RELAY,
} from "../context/request-context";
import { checkUpstreamUrl, isUpstreamRefusal } from '../http/upstream-call';

export interface ICommand<T> {
  execute(): Promise<T>;
}

// Waits out a backoff, or less: it ends the moment the signal aborts.
const backoffDelay = (ms: number, signal?: AbortSignal): Promise<void> =>
  new Promise((resolve) => {
    if (signal?.aborted) {
      resolve();
      return;
    }
    const done = (): void => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener('abort', done, { once: true });
  });

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

  /**
   * The full URL including query parameters, refused unless it may be sent (`checkUpstreamUrl`: http(s),
   * no credentials, a registered PipesHub service). Checked here too, so a refusal fails before the retry
   * loop instead of being retried.
   */
  protected buildUrl(): string {
    let raw = this.uri;
    if (this.queryParams) {
      const queryString = new URLSearchParams(
        Object.entries(this.queryParams).reduce<Record<string, string>>(
          (acc, [key, value]) => {
            acc[key] = String(value);
            return acc;
          },
          {},
        ),
      ).toString();
      raw = `${this.uri}?${queryString}`;
    }
    return checkUpstreamUrl(raw);
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
    // Propagate the trace id onto every outbound service call.
    headers = injectRequestHeaders(headers);
    // Define allowed headers in lowercase.
    const allowedHeaders = new Set([
      'content-type',
      'authorization',
      HEADER_REQUEST_ID,
      HEADER_MCP_RELAY,
      'client-name',
      // Opts a record read into the stored-version checks (a deleted or disabled connector is refused).
      'x-record-content-open',
    ]);
    // Normalize to lowercase keys so case-variants (e.g. content-type from
    // Express + Content-Type from the caller) collapse to one entry. fetch
    // otherwise emits "application/json, application/json", which FastAPI
    // does not treat as JSON and then fails Pydantic body parsing with
    // model_attributes_type.
    const normalized: Record<string, string> = {};
    for (const [key, value] of Object.entries(headers)) {
      const lower = key.toLowerCase();
      if (allowedHeaders.has(lower) && value !== undefined && value !== null) {
        normalized[lower] = String(value);
      }
    }
    if (!normalized['content-type']) {
      normalized['content-type'] = 'application/json';
    }
    return normalized;
  }

  /**
   * Retry helper method using exponential backoff.
   * @param fn - A function returning a promise to retry; it gets the 1-based attempt number.
   * @param retries - Number of retries (default is 3).
   * @param backoff - Initial backoff delay in milliseconds (default is 300ms).
   * @param signal - The caller's signal: once it aborts there is no further attempt and no wait.
   */
  protected async fetchWithRetry<T>(
    fn: (attempt: number) => Promise<T>,
    retries: number = 3,
    backoff: number = 300,
    shouldRetry: (error: unknown) => boolean = () => true,
    signal?: AbortSignal,
  ): Promise<T> {
    let attempt = 0;
    while (attempt < retries) {
      try {
        const response = await fn(attempt + 1);
        return response;
      } catch (error) {
        // A caller that aborted a request is not waiting for another try.
        if ((error as { name?: string } | null)?.name === 'AbortError') {
          throw error;
        }
        // A refused destination answers the same every time, and a refused redirect follows a request that was sent.
        if (isUpstreamRefusal(error)) {
          throw error;
        }
        attempt++;
        if (attempt >= retries || !shouldRetry(error)) {
          throw error;
        }
        // Calculate exponential backoff delay.
        const delay = backoff * Math.pow(2, attempt);
        await backoffDelay(delay, signal);
        // The caller stopped waiting: throw its signal's reason, as fetch on that signal would.
        signal?.throwIfAborted();
      }
    }
    // Should never reach here.
    throw new InternalServerError('Unexpected error in retry logic.');
  }

  /**
   * The answer's JSON. A 204 has none, and an error answer that is not JSON (a
   * proxy's HTML page) is dropped so its status still reaches the caller. A 2xx
   * that is not JSON still throws: a broken success is not hidden.
   */
  protected async readBody<B>(response: Response): Promise<B | undefined> {
    if (response.status === 204) return undefined;
    try {
      return (await response.json()) as B;
    } catch (error) {
      if (response.status >= 400 && error instanceof SyntaxError) {
        return undefined;
      }
      throw error;
    }
  }

  /** The answer's Retry-After, which the error middleware relays on a 429/503/504. */
  protected retryAfterHeaders(response: Response): {
    headers?: Record<string, string>;
  } {
    const retryAfter = response.headers.get('retry-after');
    return retryAfter !== null && retryAfter !== ''
      ? { headers: { 'retry-after': retryAfter } }
      : {};
  }

  public abstract execute(): Promise<T>;
}

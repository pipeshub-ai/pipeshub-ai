import { Logger } from '../services/logger.service';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  GatewayTimeoutError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  TooManyRequestsError,
  UnauthorizedError,
  UnprocessableEntityError,
} from './http.errors';
import { BaseError } from './base.error';
import {
  isReaderWritten,
  markClientSafe,
  serverFailureMessage,
} from './reader-friendly';

const logger = Logger.getInstance({ service: 'Backend Error' });

/**
 * Shown when one PipesHub service cannot reach another. The fault is on the
 * server, so it never asks the reader to check their own connection, and it
 * never names the service that is down.
 */
export const SERVICE_UNAVAILABLE_MESSAGE =
  'PipesHub is having trouble reaching one of its services. Try again in a minute; if it continues, ask your admin to check the services page.';

/**
 * FastAPI validation errors (422) send `detail` as an array of
 * `{loc, msg, type}` objects rather than a string. Stringifying that array
 * directly (e.g. in a template literal) yields "[object Object]" since
 * Array.prototype.toString calls the default Object.toString on each entry.
 * This extracts a readable message instead.
 */
export const stringifyErrorDetail = (detail: unknown): string => {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((entry) =>
        entry && typeof entry === 'object' && 'msg' in entry
          ? String((entry as { msg: unknown }).msg)
          : JSON.stringify(entry),
      )
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    return JSON.stringify(detail);
  }
  return 'Unknown error';
};

// The error middleware relays this as the Retry-After header.
const retryAfterMetadata = (
  error: { headers?: Record<string, unknown> } | null | undefined,
): { retryAfter: string } | undefined => {
  const value: unknown = error?.headers?.['retry-after'];
  const text =
    typeof value === 'number' && Number.isFinite(value)
      ? String(value)
      : typeof value === 'string'
        ? value.trim()
        : '';
  return text ? { retryAfter: text } : undefined;
};

const MAX_RETRY_HINT_SECONDS = 120;

/**
 * Seconds to wait from a Retry-After value: whole seconds, or an HTTP date
 * still in the future. Undefined when invalid, past, or too far off to quote.
 */
export const retryAfterToSeconds = (
  value: string | undefined,
  now: number = Date.now(),
): number | undefined => {
  const text = value?.trim();
  if (!text) return undefined;
  let seconds: number;
  if (/^\d+$/.test(text)) {
    seconds = Number(text);
  } else {
    const at = Date.parse(text);
    if (Number.isNaN(at)) return undefined;
    seconds = Math.ceil((at - now) / 1000);
  }
  return seconds > 0 && seconds <= MAX_RETRY_HINT_SECONDS ? seconds : undefined;
};

// Shown when a busy or slow backend sends no message of its own.
const retryHint = (retry: { retryAfter: string } | undefined): string => {
  const seconds = retryAfterToSeconds(retry?.retryAfter);
  return seconds
    ? `Please try again in ${seconds} second${seconds === 1 ? '' : 's'}.`
    : 'Please try again in a few seconds.';
};

const TRANSIENT_FALLBACK: Record<429 | 503 | 504, string> = {
  429: 'PipesHub is handling a lot of requests right now.',
  503: 'This part of PipesHub is briefly unavailable.',
  504: 'This took longer than expected to respond.',
};

const transientError = (
  statusCode: 429 | 503 | 504,
  upstreamDetail: unknown,
  error: { headers?: Record<string, unknown> } | undefined,
): Error => {
  const retry = retryAfterMetadata(error);
  const detail = upstreamDetail ? stringifyErrorDetail(upstreamDetail) : '';
  // A 429 is about the caller, so the service's own words are worth repeating.
  // A 503 or 504 is about the service, so only a line we know a person wrote
  // survives; anything else would be our topology on someone's screen.
  const keepDetail =
    detail.length > 0 && (statusCode === 429 || isReaderWritten(detail));
  const message = keepDetail
    ? detail
    : `${TRANSIENT_FALLBACK[statusCode]} ${retryHint(retry)}`;
  // Marked as we build it: without that the middleware would treat these as
  // plumbing and replace the very wording chosen here.
  if (statusCode === 429)
    return markClientSafe(new TooManyRequestsError(message, retry));
  if (statusCode === 503)
    return markClientSafe(new ServiceUnavailableError(message, retry));
  return markClientSafe(new GatewayTimeoutError(message, retry));
};

type Indexed = Record<string, unknown>;

const asRecord = (value: unknown): Indexed | undefined =>
  typeof value === 'object' && value !== null ? (value as Indexed) : undefined;

const asText = (value: unknown): string | undefined =>
  typeof value === 'string' && value ? value : undefined;

const isConnectionRefused = (error: Indexed): boolean => {
  const cause = asRecord(error.cause);
  if (asText(cause?.code) === 'ECONNREFUSED') return true;
  return (asText(error.message) ?? '').includes('fetch failed');
};

/**
 * Turn a failed call to another PipesHub service into the error a caller can
 * hand to the error middleware. Callers read every shape the service commands
 * return: `{ statusCode, data, message }`, axios's `{ response: { status, data } }`
 * and `{ statusCode, data, msg }`.
 *
 * A 4xx keeps the service's own words — those are written for the person who
 * made the request. A 5xx does not: the reader gets `operation` in plain words
 * while the upstream detail is logged.
 */
export const handleBackendError = (
  error: unknown,
  operation: string,
): Error => {
  // Already mapped (e.g. thrown by a pre-check and caught again); re-mapping
  // would turn any status outside the switch below into a 500.
  if (error instanceof BaseError) {
    return error;
  }

  const source = asRecord(error);
  if (!source) {
    return markClientSafe(
      new InternalServerError(serverFailureMessage(operation)),
    );
  }

  if (isConnectionRefused(source)) {
    logger.error(`Could not reach the service during ${operation}`, {
      cause: asText(asRecord(source.cause)?.code) ?? asText(source.message),
    });
    return markClientSafe(
      new ServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE),
    );
  }

  // axios-style `{ response: { status, data } }`, or a service command's
  // `{ statusCode, data, message | msg }`.
  const response = asRecord(source.response);
  const rawStatus = response ? response.status : source.statusCode;
  const statusCode = typeof rawStatus === 'number' ? rawStatus : undefined;
  const data = asRecord(response ? response.data : source.data);

  // Only the body's own words. The error's own `message` is axios's wording
  // ("Request failed with status code 503"), which must never reach a reader.
  const bodyDetail =
    data?.detail ?? data?.reason ?? data?.message ?? source.msg;
  // A caught error's own message is a last resort, and only when it was not
  // written by the HTTP client.
  const ownMessage = asText(source.message);
  const fallbackDetail =
    ownMessage && !/status code/i.test(ownMessage) ? ownMessage : undefined;

  // Some services report a refused connection in the body instead of a status.
  if (bodyDetail === 'ECONNREFUSED') {
    logger.error(`Could not reach the service during ${operation}`, {
      statusCode,
    });
    return markClientSafe(
      new ServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE),
    );
  }

  if (statusCode === undefined) {
    // A validation failure sent as a bare `{ detail }`, with no status.
    if (source.detail !== undefined && source.detail !== null) {
      return new BadRequestError(stringifyErrorDetail(source.detail));
    }
    if (source.request !== undefined && source.request !== null) {
      logger.error(`No response from the service during ${operation}`);
      return markClientSafe(
        new ServiceUnavailableError(SERVICE_UNAVAILABLE_MESSAGE),
      );
    }
    logger.error(`${operation} failed`, { error: ownMessage });
    return markClientSafe(
      new InternalServerError(serverFailureMessage(operation)),
    );
  }

  const errorDetail = stringifyErrorDetail(
    bodyDetail ?? fallbackDetail ?? 'Unknown error',
  );

  logger.error(`Backend error during ${operation}`, {
    statusCode,
    errorDetail,
    fullResponse: data,
  });

  switch (statusCode) {
    case 400:
      return new BadRequestError(errorDetail);
    case 401:
      return new UnauthorizedError(errorDetail);
    case 403:
      return new ForbiddenError(errorDetail);
    case 404:
      return new NotFoundError(errorDetail);
    case 409:
      return new ConflictError(errorDetail);
    case 422:
      return new UnprocessableEntityError(errorDetail);
    // Transient: the caller should retry, so they must not read as a 500.
    case 429:
    case 503:
    case 504:
      return transientError(statusCode, bodyDetail, response ?? source);
    default:
      // Every 5xx (502 included, as the upload pre-check documents), and
      // anything unrecognised: the service's own words describe its internals,
      // so the reader gets the plain sentence instead.
      return markClientSafe(
        new InternalServerError(serverFailureMessage(operation)),
      );
  }
};

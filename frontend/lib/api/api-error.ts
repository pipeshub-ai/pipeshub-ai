import { AxiosError, isAxiosError } from 'axios';

export enum ErrorType {
  AUTHENTICATION_ERROR = 'AUTHENTICATION_ERROR',
  AUTHORIZATION_ERROR = 'AUTHORIZATION_ERROR',
  VALIDATION_ERROR = 'VALIDATION_ERROR',
  NOT_FOUND = 'NOT_FOUND',
  CONFLICT = 'CONFLICT',
  NETWORK_ERROR = 'NETWORK_ERROR',
  SERVER_ERROR = 'SERVER_ERROR',
  TIMEOUT_ERROR = 'TIMEOUT_ERROR',
  UNKNOWN_ERROR = 'UNKNOWN_ERROR',
  REQUEST_CANCELLED = 'REQUEST_CANCELLED',
}

export interface ProcessedError {
  type: ErrorType;
  message: string;
  statusCode?: number;
  details?: Record<string, unknown>;
  /** Reference the server logged this failure under, shown so it can be quoted. */
  requestId?: string;
  originalError?: Error;
}

interface NestedApiError {
  code?: string;
  message?: string;
}

interface ApiErrorResponse {
  message?: string;
  error?: string | NestedApiError;
  errors?: Record<string, string[]>;
  details?: Record<string, unknown>;
  /** FastAPI / similar */
  detail?: string | Array<string | { msg?: string }>;
  /** Retrieval/search and other services: machine-readable outcome (e.g. `accessible_records_not_found`). */
  status?: string;
  /** Python backend error responses (e.g., KB service permission errors). */
  reason?: string;
  /** Node error middleware: the reference to quote when asking for help. */
  requestId?: string;
}

/**
 * Best-effort user-facing message from a parsed JSON API error body (e.g. fetch responses).
 */
export function extractApiErrorMessage(data: unknown): string | null {
  if (data == null || typeof data !== 'object') return null;
  const d = data as ApiErrorResponse;

  const nestedErrorMessage =
    typeof d.error === 'string'
      ? d.error
      : d.error && typeof d.error === 'object' && 'message' in d.error
        ? String((d.error as { message?: string }).message ?? '')
        : '';

  if (typeof d.message === 'string' && d.message.trim()) {
    return d.message.trim();
  }
  if (typeof d.reason === 'string' && d.reason.trim()) {
    return d.reason.trim();
  }
  if (nestedErrorMessage.trim()) {
    return nestedErrorMessage.trim();
  }

  if (d.errors && typeof d.errors === 'object') {
    for (const arr of Object.values(d.errors)) {
      if (Array.isArray(arr) && typeof arr[0] === 'string' && arr[0].trim()) {
        return arr[0].trim();
      }
    }
  }

  if (typeof d.detail === 'string' && d.detail.trim()) {
    return d.detail.trim();
  }
  if (Array.isArray(d.detail) && d.detail.length > 0) {
    const first = d.detail[0];
    if (typeof first === 'string' && first.trim()) return first.trim();
    if (first && typeof first === 'object' && typeof (first as { msg?: string }).msg === 'string') {
      const msg = (first as { msg: string }).msg.trim();
      if (msg) return msg;
    }
  }

  return null;
}

const MAX_RETRY_HINT_SECONDS = 120;

/**
 * Seconds to wait from a Retry-After value: whole seconds, or an HTTP date
 * still in the future. Undefined when invalid, past, or above `max` (too far off
 * to quote in a message).
 */
export function parseRetryAfter(
  value: unknown,
  now: number = Date.now(),
  max: number = MAX_RETRY_HINT_SECONDS,
): number | undefined {
  const text = typeof value === 'number' ? String(value) : typeof value === 'string' ? value.trim() : '';
  if (!text) return undefined;
  let seconds: number;
  if (/^\d+$/.test(text)) {
    seconds = Number(text);
  } else {
    const at = Date.parse(text);
    if (Number.isNaN(at)) return undefined;
    seconds = Math.ceil((at - now) / 1000);
  }
  return seconds > 0 && seconds <= max ? seconds : undefined;
}

function retryAfterSeconds(error: AxiosError): number | undefined {
  return parseRetryAfter(error.response?.headers?.['retry-after']);
}

export function busyMessage(retryAfter?: number): string {
  return retryAfter
    ? `PipesHub is busy right now. Please try again in ${retryAfter} second${retryAfter === 1 ? '' : 's'}.`
    : 'PipesHub is busy right now. Please try again in a few seconds.';
}

function isAxiosRequestCancelled(error: AxiosError): boolean {
  return error.code === 'ERR_CANCELED' || error.message === 'canceled';
}

export function processError(error: AxiosError<ApiErrorResponse>): ProcessedError {
  // Network error - no response received
  if (!error.response) {
    if (isAxiosRequestCancelled(error)) {
      return {
        type: ErrorType.REQUEST_CANCELLED,
        message: 'Request was cancelled.',
        originalError: error,
      };
    }
    if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
      return {
        type: ErrorType.TIMEOUT_ERROR,
        message: 'Request timed out. Please try again.',
        originalError: error,
      };
    }

    return {
      type: ErrorType.NETWORK_ERROR,
      message: 'Network error. Please check your connection.',
      originalError: error,
    };
  }

  const { status, data } = error.response;
  // The Node error middleware sends this beside the message; a nested
  // `error.requestId` is the same value one level down.
  const nestedRequestId =
    data?.error && typeof data.error === 'object'
      ? (data.error as { requestId?: string }).requestId
      : undefined;
  const requestId =
    typeof data?.requestId === 'string' ? data.requestId : nestedRequestId;
  // Handle nested error objects: { error: { code: '...', message: '...' } }
  const errorField =
    typeof data?.error === 'string'
      ? data.error
      : (data?.error as Record<string, string> | undefined)?.message;
  // Also check `detail` for FastAPI-style error responses.
  const detailField = typeof data?.detail === 'string' ? data.detail : undefined;
  // Check `reason` for Python backend error responses (e.g., KB permission errors).
  const reasonField = typeof (data as { reason?: string })?.reason === 'string' ? (data as { reason: string }).reason : undefined;
  // Only the server's own words, never axios's ("Request failed with status
  // code 500"): each case below supplies a sentence a person can act on.
  const message = data?.message || reasonField || errorField || detailField || '';

  // Map HTTP status codes to error types
  const processed = ((): ProcessedError => {
    switch (status) {
      case 401:
        return {
          type: ErrorType.AUTHENTICATION_ERROR,
          message: message || 'Session expired. Please sign in again.',
          statusCode: status,
          details: data?.details,
          originalError: error,
        };

      case 403:
        return {
          type: ErrorType.AUTHORIZATION_ERROR,
          message: message || 'You do not have permission to perform this action.',
          statusCode: status,
          details: data?.details,
          originalError: error,
        };

      case 404: {
        const bodyStatus = typeof data?.status === 'string' ? data.status : undefined;
        const baseDetails =
          data?.details && typeof data.details === 'object' ? { ...data.details } : {};
        return {
          type: ErrorType.NOT_FOUND,
          message: message || 'The requested resource was not found.',
          statusCode: status,
          details: bodyStatus ? { ...baseDetails, apiStatus: bodyStatus } : data?.details,
          originalError: error,
        };
      }

      case 400:
      case 422:
        return {
          type: ErrorType.VALIDATION_ERROR,
          message:
            extractApiErrorMessage(data) ||
            (typeof message === 'string' ? message.trim() : '') ||
            'Invalid request. Please check your input.',
          statusCode: status,
          details: data?.errors ? { errors: data.errors } : data?.details,
          originalError: error,
        };

      case 409:
        return {
          type: ErrorType.CONFLICT,
          message: message || 'A conflict occurred. Please try again.',
          statusCode: status,
          details: data?.details,
          originalError: error,
        };

      // Busy or slow, not broken: the server's own words when it sent any
      // (never axios's "Request failed with status code 503"), else a retry hint.
      case 429:
      case 503:
      case 504: {
        const serverMessage = data?.message || reasonField || errorField || detailField;
        return {
          type: ErrorType.SERVER_ERROR,
          message: serverMessage || busyMessage(retryAfterSeconds(error)),
          statusCode: status,
          details: data?.details,
          originalError: error,
        };
      }

      case 500:
      case 502:
        return {
          type: ErrorType.SERVER_ERROR,
          message: message || 'Server error. Please try again later.',
          statusCode: status,
          details: data?.details,
          originalError: error,
        };

      default:
        return {
          type: ErrorType.UNKNOWN_ERROR,
          message: message || 'An unexpected error occurred.',
          statusCode: status,
          details: data?.details,
          originalError: error,
        };
    }

  })();

  // The reference rides beside the message, never inside it: a client that
  // filters technical-looking text would otherwise drop the whole sentence.
  return requestId ? { ...processed, requestId } : processed;
}

/**
 * Text that describes our machinery rather than the reader's problem: Python
 * reprs, tracebacks, ids, axios's own wording, internal service names. A
 * message matching any of these is dropped in favour of the caller's fallback.
 */
const TECHNICAL_MESSAGE_PATTERNS: RegExp[] = [
  // The HTTP client's own wording. Only this exact shape: "The status code
  // field is required." is a sentence a person can act on.
  /request failed with status code/i,
  /traceback/i,
  /\[object object\]/i,
  /^[A-Za-z_]*(Error|Exception)\b/,
  /\b(KeyError|TypeError|ValueError|AttributeError|NoneType|undefined is not)\b/,
  /\b[0-9a-f]{24}\b/i, // Mongo ObjectId
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i, // UUID
  /\b(kafka|redis|mongodb|qdrant|arangodb|neo4j|etcd)\b/i,
  /\b(connector service|indexing service|query service|ai service|backend service)\b/i,
  /ECONNREFUSED|ECONNRESET|ENOTFOUND|socket hang up/i,
];

function looksTechnical(message: string): boolean {
  return TECHNICAL_MESSAGE_PATTERNS.some((pattern) => pattern.test(message));
}

/**
 * The message to show a person for a failed request: the server's own words
 * when they were written for a reader, otherwise the caller's fallback. Never
 * axios's "Request failed with status code 500".
 */
function messageOf(error: unknown): string {
  // Axios first: an AxiosError is also an Error, so reading `.message` here
  // would give "Request failed with status code 403" and throw away the
  // server's own words in `response.data`.
  if (isAxiosError(error)) return processError(error).message;
  if (isProcessedError(error)) return error.message;
  const loose = (error as { message?: unknown })?.message;
  return typeof loose === 'string' ? loose : '';
}

export function getUserFacingErrorMessage(error: unknown, fallback: string): string {
  // A caught value can be anything, including `{ message: null }`; this is the
  // worst place to throw, so nothing here assumes a string.
  const text = messageOf(error).trim();
  if (!text || looksTechnical(text)) return fallback;
  return text;
}

// Type guard to check if an error is a ProcessedError
export function isProcessedError(error: unknown): error is ProcessedError {
  return (
    typeof error === 'object' &&
    error !== null &&
    'type' in error &&
    // A string, not merely present: the type promises one, and callers read it
    // without checking.
    typeof (error as { message?: unknown }).message === 'string' &&
    Object.values(ErrorType).includes((error as ProcessedError).type)
  );
}

/** Retrieval `Status.ACCESSIBLE_RECORDS_NOT_FOUND` — search ran but no docs in scope. */
export const SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS = 'accessible_records_not_found';

/** Fallback if response body omits `status` (message copy may change). */
export const SEARCH_NO_ACCESSIBLE_DOCUMENTS_FRAGMENT = 'No accessible documents found';

export function isRequestCancelledError(error: unknown): boolean {
  return isProcessedError(error) && error.type === ErrorType.REQUEST_CANCELLED;
}

/** Empty search results (not a failure): API reports no docs for current scope. */
export function isSearchNoAccessibleDocumentsNotFound(error: unknown): boolean {
  if (!isProcessedError(error) || error.type !== ErrorType.NOT_FOUND) return false;
  const apiStatus = error.details?.apiStatus;
  if (apiStatus === SEARCH_ACCESSIBLE_RECORDS_NOT_FOUND_STATUS) return true;
  return (error.message || '').includes(SEARCH_NO_ACCESSIBLE_DOCUMENTS_FRAGMENT);
}

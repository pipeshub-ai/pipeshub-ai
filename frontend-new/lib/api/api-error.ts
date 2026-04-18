import { AxiosError } from 'axios';

export enum ErrorType {
  AUTHENTICATION_ERROR = 'AUTHENTICATION_ERROR',
  AUTHORIZATION_ERROR = 'AUTHORIZATION_ERROR',
  VALIDATION_ERROR = 'VALIDATION_ERROR',
  NOT_FOUND = 'NOT_FOUND',
  NETWORK_ERROR = 'NETWORK_ERROR',
  SERVER_ERROR = 'SERVER_ERROR',
  TIMEOUT_ERROR = 'TIMEOUT_ERROR',
  UNKNOWN_ERROR = 'UNKNOWN_ERROR',
}

export interface ProcessedError {
  type: ErrorType;
  message: string;
  statusCode?: number;
  details?: Record<string, unknown>;
  originalError?: Error;
}

interface ApiErrorResponse {
  message?: string;
  error?: string | { code?: string; message?: string };
  errors?: Record<string, string[]>;
  details?: Record<string, unknown>;
  /** FastAPI / similar */
  detail?: string | Array<string | { msg?: string }>;
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

export function processError(error: AxiosError<ApiErrorResponse>): ProcessedError {
  // Network error - no response received
  if (!error.response) {
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
  // Handle nested error objects: { error: { code: '...', message: '...' } }
  const errorField =
    typeof data?.error === 'string'
      ? data.error
      : (data?.error as Record<string, string> | undefined)?.message;
  const message = data?.message || errorField || error.message || 'An error occurred';

  // Map HTTP status codes to error types
  switch (status) {
    case 401:
      return {
        type: ErrorType.AUTHENTICATION_ERROR,
        message: 'Session expired. Please sign in again.',
        statusCode: status,
        details: data?.details,
        originalError: error,
      };

    case 403:
      return {
        type: ErrorType.AUTHORIZATION_ERROR,
        message: 'You do not have permission to perform this action.',
        statusCode: status,
        details: data?.details,
        originalError: error,
      };

    case 404:
      return {
        type: ErrorType.NOT_FOUND,
        message: message || 'The requested resource was not found.',
        statusCode: status,
        details: data?.details,
        originalError: error,
      };

    case 400:
    case 422:
      return {
        type: ErrorType.VALIDATION_ERROR,
        message: message || 'Invalid request. Please check your input.',
        statusCode: status,
        details: data?.errors ? { errors: data.errors } : data?.details,
        originalError: error,
      };

    case 500:
    case 502:
    case 503:
    case 504:
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
}

// Type guard to check if an error is a ProcessedError
export function isProcessedError(error: unknown): error is ProcessedError {
  return (
    typeof error === 'object' &&
    error !== null &&
    'type' in error &&
    'message' in error &&
    Object.values(ErrorType).includes((error as ProcessedError).type)
  );
}

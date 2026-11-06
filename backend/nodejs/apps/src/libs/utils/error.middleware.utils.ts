import { Response } from 'express';
import axios, { AxiosError } from 'axios';
import { BaseError } from '../errors/base.error';
import { Logger } from '../services/logger.service';
import {
  redactSecretsInText,
  redactSensitiveQueryParams,
} from './log-redaction.utils';

/**
 * 'warn' for a 4xx of ours: the caller's request answered as intended (a taken
 * name, a wrong password, a refused permission, a 402). 'error' for every 5xx
 * and for any error that is not ours.
 */
export const logLevelFor = (error: unknown): 'warn' | 'error' =>
  error instanceof BaseError && error.statusCode < 500 ? 'warn' : 'error';

// The standard OAuth error codes (RFC 6749 §4.1.2.1 and §5.2, RFC 6750 §3.1,
// RFC 8628 §3.5). Any other value in that field could be an echoed credential.
const OAUTH_ERROR_CODES: ReadonlySet<string> = new Set([
  'invalid_request',
  'invalid_client',
  'invalid_grant',
  'unauthorized_client',
  'unsupported_grant_type',
  'invalid_scope',
  'access_denied',
  'unsupported_response_type',
  'server_error',
  'temporarily_unavailable',
  'invalid_token',
  'insufficient_scope',
  'authorization_pending',
  'slow_down',
  'expired_token',
]);

const upstreamErrorCode = (data: unknown): string | undefined => {
  const code = (data as { error?: unknown } | null | undefined)?.error;
  return typeof code === 'string' && OAUTH_ERROR_CODES.has(code)
    ? code
    : undefined;
};

/**
 * A failed axios call, logged by what helps debugging. Never its config: that
 * holds the request headers and the body, which axios has already turned into
 * a string that key-based redaction cannot see into (an OAuth body carries
 * client_secret, refresh_token or the authorization code).
 */
const axiosErrorData = (
  error: AxiosError,
  includeStack: boolean,
): Record<string, unknown> => {
  const upstreamError = upstreamErrorCode(error.response?.data);
  const url = error.config?.url;
  return {
    name: error.name,
    // `new AxiosError()` has no message; redacting it would throw and lose the rest.
    message:
      typeof error.message === 'string'
        ? redactSecretsInText(error.message)
        : undefined,
    code: error.code,
    status: error.response?.status,
    method: error.config?.method,
    url: url === undefined ? undefined : redactSensitiveQueryParams(url),
    ...(upstreamError !== undefined && { upstreamError }),
    ...(includeStack &&
      error.stack !== undefined && { stack: redactSecretsInText(error.stack) }),
  };
};

/**
 * Extract error data without circular references
 * Note: Stack traces are included here for logging purposes only, not for client responses
 */
export const extractErrorData = (error: any, includeStack = true): any => {
  if (!error) return null;

  try {
    // Before toJSON: an AxiosError's toJSON serialises the request it carried.
    if (axios.isAxiosError(error)) return axiosErrorData(error, includeStack);

    // For errors with a toJSON method (like BaseError)
    // Include stack trace for logging purposes only (not for client responses)
    if (typeof error.toJSON === 'function') return error.toJSON(includeStack);

    // Another HTTP client's error that carries the upstream answer (e.g. GaxiosError)
    if (error.response?.data) {
      const data = error.response.data;
      return {
        detail: data.detail || data.reason || data.message || 'Unknown error',
        status: error.response.status,
        statusText: error.response.statusText,
        // Stack traces included for server-side logging only
        ...(includeStack && { stack: error.stack }),
      };
    }

    // Handle other error types
    return {
      message: error.message || 'Unknown error',
      code: error.code,
      name: error.name,
      // Stack traces included for server-side logging only
      ...(includeStack && { stack: error.stack }),
    };
  } catch (extractionError) {
    return {
      message: 'Error processing error data',
      originalError: String(error),
    };
  }
};

/**
 * JSON response helper with circular reference protection
 */
export const jsonResponse = (
  res: Response,
  statusCode: number,
  data: any,
): void => {
  try {
    res.status(statusCode).json(data);
  } catch (jsonError) {
    // If JSON serialization fails, send a fallback error response
    console.error('JSON serialization failed:', jsonError);
    try {
      res.status(500).json({
        error: {
          code: 'SERIALIZATION_ERROR',
          message: 'Internal server error - response serialization failed',
        },
      });
    } catch (fallbackError) {
      // Last resort - send plain text
      res.status(500).send('Internal server error');
    }
  }
};

/**
 * Error logging with circular reference protection
 */
export const logError = (
  logger: Logger,
  message: string,
  error: any,
  context?: any,
  level: 'error' | 'warn' = 'error',
): void => {
  try {
    // A warning is an answer that went as intended; where it was thrown is noise.
    const errorData = extractErrorData(error, level === 'error');
    logger[level](message, {
      error: errorData,
      context,
    });
  } catch (logError) {
    // If logging fails, use console.error as fallback
    console.error('Error logging failed:', logError);
    console.error(message, {
      error: String(error),
      context,
    });
  }
};

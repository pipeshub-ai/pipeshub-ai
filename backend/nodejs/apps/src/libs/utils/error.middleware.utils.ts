import { Response } from 'express';
import { BaseError } from '../errors/base.error';
import { Logger } from '../services/logger.service';

/**
 * 'warn' for a 4xx of ours: the caller's request answered as intended (a taken
 * name, a wrong password, a refused permission, a 402). 'error' for every 5xx
 * and for any error that is not ours.
 */
export const logLevelFor = (error: unknown): 'warn' | 'error' =>
  error instanceof BaseError && error.statusCode < 500 ? 'warn' : 'error';

/**
 * Extract error data without circular references
 * Note: Stack traces are included here for logging purposes only, not for client responses
 */
export const extractErrorData = (error: any, includeStack = true): any => {
  if (!error) return null;

  try {
    // For errors with a toJSON method (like BaseError)
    // Include stack trace for logging purposes only (not for client responses)
    if (typeof error.toJSON === 'function') {
      const json: unknown = error.toJSON(includeStack);
      // An AxiosError's toJSON ignores the argument and always adds the stack.
      if (includeStack || !json || typeof json !== 'object') return json;
      const { stack: _stack, ...withoutStack } = json as Record<
        string,
        unknown
      >;
      return withoutStack;
    }

    // Handle Axios error response data
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

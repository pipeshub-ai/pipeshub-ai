import { Response } from 'express';
import { Logger } from '../services/logger.service';

/**
 * Extract error data without circular references
 */
export const extractErrorData = (error: any): any => {
  if (!error) return null;

  try {
      // For errors with a toJSON method (like BaseError)
      if (typeof error.toJSON === 'function') {
          return error.toJSON();
      }

      // Handle Axios error response data
      if (error.response?.data) {
      const data = error.response.data;
      return {
          detail: data.detail || data.reason || data.message || 'Unknown error',
          status: error.response.status,
          statusText: error.response.statusText,
          stack: error.stack,
      };
      }

      // Handle other error types
      return {
      message: error.message || 'Unknown error',
      code: error.code,
      name: error.name,
      stack: error.stack,
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
): void => {
  try {
    const errorData = extractErrorData(error);
    logger.error(message, {
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

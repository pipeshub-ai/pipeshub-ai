import { Response } from 'express';
import { classifyError, levelFor } from './error-class';

/** The level a caught error is logged at, by the shared policy (error-class.ts). */
export const logLevelFor = (error: unknown): 'error' | 'warn' | 'info' =>
  levelFor(classifyError(error));

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

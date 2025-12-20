import { Request, Response, NextFunction } from 'express';
import { Logger } from '../services/logger.service';
import { BaseError } from '../errors/base.error';
import { jsonResponse, logError } from '../utils/error.middleware.utils';

export class ErrorMiddleware {
  private static logger = Logger.getInstance();


  private static sanitizeErrorResponse(errorResponse: any): any {
    if (!errorResponse || typeof errorResponse !== 'object') {
      return errorResponse;
    }

    const cloneAndSanitize = (obj: any, seen = new WeakSet()): any => {
      if (obj === null || typeof obj !== 'object') {
        return obj;
      }

      if (seen.has(obj)) {
        return '[Circular]';
      }
      seen.add(obj);

      if (Array.isArray(obj)) {
        return obj.map(item => cloneAndSanitize(item, seen));
      }

      const newObj: { [key: string]: any } = {};
      for (const key in obj) {
        if (Object.prototype.hasOwnProperty.call(obj, key)) {
          if (key !== 'stack' && key !== 'stackTrace') {
            newObj[key] = cloneAndSanitize(obj[key], seen);
          }
        }
      }
      return newObj;
    };

    return cloneAndSanitize(errorResponse);
  }

  static handleError() {
    return (error: Error, req: Request, res: Response, _next: NextFunction) => {
      // Check if response has already been sent
      if (res.headersSent) {
        return;
      }

      try {
        if (error instanceof BaseError) {
          this.handleBaseError(error, req, res);
        } else {
          this.handleUnknownError(error, req, res);
        }
      } catch (middlewareError) {
        // If even the error middleware fails, send a basic error response
        console.error('Error in error middleware:', middlewareError);
        jsonResponse(res, 500, {
          error: {
            code: 'MIDDLEWARE_ERROR',
            message: 'An unexpected error occurred while processing the request'
          }
        });
      }
    };
  }

  private static handleBaseError(
    error: BaseError,
    req: Request,
    res: Response,
  ) {
    logError(this.logger, 'Application error', error, {
      request: this.getRequestContext(req),
    });

    // Never expose stack traces to clients - security best practice
    const isDevelopment = process.env.NODE_ENV === 'development' || process.env.NODE_ENV === 'dev';
    
    const errorResponse = {
      error: {
        code: error.code,
        message: error.message,
        // Only include metadata in development, never stack traces
        ...(isDevelopment && {
          metadata: error.metadata,
        }),
        // Stack traces should NEVER be exposed to clients, even in development
        // They are logged server-side only for debugging
      },
    };

    // Ensure no stack traces are included (defense in depth)
    const sanitizedResponse = this.sanitizeErrorResponse(errorResponse);
    jsonResponse(res, error.statusCode, sanitizedResponse);
  }

  private static handleUnknownError(error: Error, req: Request, res: Response) {
    logError(this.logger, 'Unhandled error', error, {
      request: this.getRequestContext(req),
    });

    const errorResponse = {
      error: {
        code: 'INTERNAL_ERROR',
        message:
          process.env.NODE_ENV === 'production'
            ? 'An unexpected error occurred'
            : error.message,
      },
    };

    // Ensure no stack traces are included (defense in depth)
    const sanitizedResponse = this.sanitizeErrorResponse(errorResponse);
    jsonResponse(res, 500, sanitizedResponse);
  }

  private static getRequestContext(req: Request) {
    return {
      method: req.method,
      path: req.path,
      query: req.query,
      params: req.params,
      ip: req.ip,
      headers: this.sanitizeHeaders(req.headers),
    };
  }

  private static sanitizeHeaders(headers: any) {
    const sanitized = { ...headers };
    delete sanitized.authorization;
    delete sanitized.cookie;
    return sanitized;
  }
}


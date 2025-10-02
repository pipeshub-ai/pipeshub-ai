import { Request, Response, NextFunction } from 'express';
import { Logger } from '../services/logger.service';
import { BaseError } from '../errors/base.error';
import { jsonResponse, logError } from '../utils/error.middleware.utils';

export class ErrorMiddleware {
  private static logger = Logger.getInstance();

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

    const errorResponse = {
      error: {
        code: error.code,
        message: error.message,
        ...(process.env.NODE_ENV !== 'production' && {
          metadata: error.metadata,
          stack: error.stack,
        }),
      },
    };

    jsonResponse(res, error.statusCode, errorResponse);
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

    jsonResponse(res, 500, errorResponse);
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


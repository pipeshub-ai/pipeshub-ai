import { Request, Response, NextFunction } from 'express';
import { Logger } from '../services/logger.service';
import { BaseError } from '../errors/base.error';

export class ErrorMiddleware {
  private static logger = Logger.getInstance();

  static handleError() {
    return (error: Error, req: Request, res: Response, _next: NextFunction) => {
      if (error instanceof BaseError) {
        this.handleBaseError(error, req, res);
      } else {
        this.handleUnknownError(error, req, res);
      }
    };
  }

  private static handleBaseError(
    error: BaseError,
    req: Request,
    res: Response,
  ) {
    const logMethod = 'error';

    this.logger[logMethod]('Application error', {
      error: error.toJSON(),
      request: this.getRequestContext(req),
    });

    res.status(error.statusCode).json({
      error: {
        code: error.code,
        message: error.message,
        ...(process.env.NODE_ENV !== 'production' && {
          metadata: error.metadata,
          stack: error.stack,
        }),
      },
    });
  }

  private static handleUnknownError(error: Error, req: Request, res: Response) {
    this.logger.error('Unhandled error', {
      error: {
        name: error.name,
        message: error.message,
        stack: error.stack,
      },
      request: this.getRequestContext(req),
    });

    res.status(500).json({
      error: {
        code: 'INTERNAL_ERROR',
        message:
          process.env.NODE_ENV === 'production'
            ? 'An unexpected error occurred'
            : error.message,
      },
    });
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

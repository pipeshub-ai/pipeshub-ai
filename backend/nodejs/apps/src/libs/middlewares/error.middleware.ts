import { Request, Response, NextFunction } from 'express';
import { Logger } from '../services/logger.service';
import { BaseError } from '../errors/base.error';
import { HttpError } from '../errors/http.errors';
import { isClientSafeError, isReaderWritten } from '../errors/reader-friendly';
import { jsonResponse, logError } from '../utils/error.middleware.utils';

/**
 * What a reader is told when the failure is in PipesHub's own plumbing (a
 * broker, cache or database). Those errors describe the machine, so they stay
 * in the log. The request id rides alongside as `requestId` rather than inside
 * this sentence: a client that quotes it can show it, and one that filters
 * technical-looking text can still trust the words.
 */
const INFRASTRUCTURE_FAILURE_MESSAGE =
  "Something went wrong on PipesHub's side. Please try again; if it keeps happening, ask your admin for help.";

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
        return obj.map((item) => cloneAndSanitize(item, seen));
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
            message:
              'An unexpected error occurred while processing the request',
          },
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
    const isDevelopment =
      process.env.NODE_ENV === 'development' || process.env.NODE_ENV === 'dev';

    // Infrastructure errors (Kafka, Redis, Mongo, etcd, serialization) are
    // BaseErrors too, and their messages name internals. Anything that is not
    // an HttpError we deliberately raised, and failed on our side, is replaced.
    // A 5xx message written by this process is kept; one that describes our
    // plumbing is replaced. Infrastructure failures (Kafka, Redis, Mongo, etcd,
    // serialization) are BaseErrors but not HttpErrors, and their messages name
    // the machine, so they go.
    //
    // Upstream text is judged where it arrives, in libs/errors/backend-error:
    // that mapper never puts another service's words in a 5xx unless they are
    // on its allowlist, so an HttpError reaching here carries wording we chose.
    //
    // `isClientSafeError` is the explicit signal and the direction of travel;
    // `instanceof HttpError` is the provisional one, kept until every 5xx
    // constructor in the codebase is marked. Gating on the flag alone today
    // would flatten the messages other modules already write for readers.
    const isOurOwnWording =
      isClientSafeError(error) ||
      isReaderWritten(error.message) ||
      error instanceof HttpError;
    const isInternalPlumbing = error.statusCode >= 500 && !isOurOwnWording;
    const requestId = req.context?.requestId;

    const errorResponse = {
      error: {
        code: isInternalPlumbing ? 'INTERNAL_ERROR' : error.code,
        message: isInternalPlumbing
          ? INFRASTRUCTURE_FAILURE_MESSAGE
          : error.message,
        ...(requestId && { requestId }),
        // Only include metadata in development, and never for a failure whose
        // details describe our internals.
        ...(isDevelopment &&
          !isInternalPlumbing && {
            metadata: error.metadata,
          }),
        // Stack traces should NEVER be exposed to clients, even in development
        // They are logged server-side only for debugging
      },
    };

    const retryAfter = error.metadata?.retryAfter;
    if (typeof retryAfter === 'string' && this.isValidRetryAfter(retryAfter)) {
      res.setHeader('Retry-After', retryAfter);
    }

    // Ensure no stack traces are included (defense in depth)
    const sanitizedResponse = this.sanitizeErrorResponse(errorResponse);
    jsonResponse(res, error.statusCode, sanitizedResponse);
  }

  // delay-seconds or an HTTP-date (RFC 9110 §10.2.3); anything else is dropped
  // rather than echoed, since it originates upstream.
  private static isValidRetryAfter(value: string): boolean {
    if (/^\d{1,9}$/.test(value)) return true;
    return (
      /^[A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(
        value,
      ) && !Number.isNaN(Date.parse(value))
    );
  }

  private static handleUnknownError(error: Error, req: Request, res: Response) {
    logError(this.logger, 'Unhandled error', error, {
      request: this.getRequestContext(req),
    });

    // The raw message is logged just above. It never goes to the client, not
    // even outside production: the integration and compose setups run with
    // NODE_ENV=development, so that branch is reachable in real deployments.
    const requestId = req.context?.requestId;
    const errorResponse = {
      error: {
        code: 'INTERNAL_ERROR',
        message: INFRASTRUCTURE_FAILURE_MESSAGE,
        ...(requestId && { requestId }),
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

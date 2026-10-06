import { NextFunction, Response } from 'express';

/**
 * Routes a rejection or synchronous throw from an async handler to `next`, so the error middleware
 * answers instead of the request hanging. The returned promise always resolves, so callers that
 * `await` the handler still see it finish. Without a `next` (a direct call) the error propagates.
 */
export const catchAsync =
  <Req>(
    handler: (req: Req, res: Response, next?: NextFunction) => Promise<void>,
  ) =>
  async (req: Req, res: Response, next?: NextFunction): Promise<void> => {
    try {
      await handler(req, res, next);
    } catch (error) {
      if (!next) throw error;
      next(error);
      // The error middleware skips a response whose headers are out (an open SSE stream); end it so the client does not hang.
      if (res.headersSent && !res.writableEnded) res.end();
    }
  };

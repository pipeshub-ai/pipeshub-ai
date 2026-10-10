import { NextFunction, RequestHandler, Response } from 'express';
import { NotFoundError } from '../errors/http.errors';
import { AuthenticatedUserRequest } from './types';

export interface FlagReader {
  /** Never throws. */
  isEnabled(key: string): Promise<boolean>;
}

/** Answers 404 while the platform flag is off, so a gated route looks unmounted. Mount it before authentication and validation. */
export function requireFlag(flags: FlagReader, key: string): RequestHandler {
  return async (
    _req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      if (await flags.isEnabled(key)) {
        next();
        return;
      }
      next(new NotFoundError('Not found'));
    } catch (error) {
      next(error);
    }
  };
}

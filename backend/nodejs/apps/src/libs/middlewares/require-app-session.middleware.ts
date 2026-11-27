import { NextFunction, Response } from 'express';
import { DomainHttpError } from '../errors/domain-http.error';
import { UnauthorizedError } from '../errors/http.errors';
import { HEADER_MCP_RELAY } from '../context/request-context';
import { isAppSession } from '../services/token-class';
import { AuthenticatedUserRequest } from './types';

export const APP_SESSION_REQUIRED = 'APP_SESSION_REQUIRED';

export class AppSessionRequiredError extends DomainHttpError {
  constructor() {
    super(
      APP_SESSION_REQUIRED,
      'This can be done only in the PipesHub app',
      403,
    );
  }
}

/** Whether the call comes from the app's own interactive session, and not from a tool's `/mcp` request made with it. */
export const fromTheApp = (req: AuthenticatedUserRequest): boolean =>
  isAppSession(req.user) && req.headers[HEADER_MCP_RELAY] === undefined;

/**
 * Admits only the app's own interactive session: refuses OAuth clients and personal access tokens, the tokens
 * handed to runs, tokens minted any other way, and calls an /mcp request makes for a tool. For routes that need the
 * person to have seen the app's dialog, such as consent to show an answer to more people. Runs after `authenticate`.
 */
export function requireAppSession(
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): void {
  if (!req.user) {
    next(new UnauthorizedError('Authentication required'));
    return;
  }
  if (!fromTheApp(req)) {
    next(new AppSessionRequiredError());
    return;
  }
  next();
}

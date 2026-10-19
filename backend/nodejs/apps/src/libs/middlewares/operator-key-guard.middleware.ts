import { NextFunction, Request, Response } from 'express';
import { ValidationError } from '../errors/validation.error';

/**
 * Refuses a query string whose parameter names could act as MongoDB operators
 * or field paths. The extended query parser turns `?email[$ne]=x` into
 * `{ email: { $ne: 'x' } }`, and `?a.b=1` into a dotted key, so a handler that
 * passes `req.query` into a filter without a strict schema would run them.
 * Only names are checked: dots in values, such as file names, are ordinary.
 *
 * Bodies are left to each route's Zod schema, because they legitimately carry
 * JSON Schema keys (`$schema`, `$ref`, `$defs`) and dotted names. Path params
 * need no check here: their names come from the route definition.
 *
 * No route reads a `$`-prefixed or dotted query parameter today (no OData
 * `$filter` or `$top` passthrough reaches Node), so there is no allowlist.
 */

export const OPERATOR_KEY_MESSAGE =
  "Query parameter names can't start with '$' or contain '.'. Rename the parameter and try again.";

// Echoed back only when it is a plain name, as the XSS guard does for fields.
const SAFE_KEY_PATH = /^[A-Za-z0-9_$.[\]-]{1,200}$/;

const isOperatorKey = (key: string): boolean =>
  key.startsWith('$') || key.includes('.');

const findOperatorKey = (value: unknown, path: string): string | null => {
  if (value === null || typeof value !== 'object') {
    return null;
  }
  for (const [key, child] of Object.entries(value)) {
    const childPath = path === '' ? key : `${path}[${key}]`;
    if (isOperatorKey(key)) {
      return childPath;
    }
    const nested = findOperatorKey(child, childPath);
    if (nested !== null) {
      return nested;
    }
  }
  return null;
};

// Only the API surface reads Mongo; the SPA and static files are left alone so
// an outside redirect to a page with an unusual parameter still loads it.
// Express matches routes case-insensitively, so /API/v1/... reaches the same
// handlers and has to be guarded too.
const isGuardedPath = (path: string): boolean => {
  const lower = path.toLowerCase();
  return (
    lower.startsWith('/api/') || lower === '/mcp' || lower.startsWith('/mcp/')
  );
};

export const operatorKeyGuardMiddleware = (
  req: Request,
  _res: Response,
  next: NextFunction,
): void => {
  if (!isGuardedPath(req.path)) {
    next();
    return;
  }
  const offending = findOperatorKey(req.query, '');
  if (offending === null) {
    next();
    return;
  }
  next(
    new ValidationError(OPERATOR_KEY_MESSAGE, [
      {
        field: SAFE_KEY_PATH.test(offending) ? `query.${offending}` : 'query',
        message: OPERATOR_KEY_MESSAGE,
        code: 'invalid_key',
      },
    ]),
  );
};

import { Response, NextFunction } from 'express';
import { ForbiddenError, UnauthorizedError } from '../errors/http.errors';
import { AuthenticatedUserRequest } from './types';

/**
 * Rejects callers authenticated with an OAuth access token or a personal
 * access token (both set `req.user.isOAuth`). Must run after
 * `authMiddleware.authenticate`.
 *
 * Use on endpoints that represent the user acting in person, such as OAuth
 * client management and the consent step that issues authorization codes.
 * If those accepted a bearer token a client already holds, that client could
 * register itself for wider scopes and self-consent, turning any issued
 * token into org-wide access without the user.
 */
export function requireSessionAuth(
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): void {
  if (!req.user) {
    next(new UnauthorizedError('Authentication required'));
    return;
  }
  if (req.user.isOAuth === true) {
    next(
      new ForbiddenError(
        'This endpoint requires an interactive user session; OAuth access tokens and personal access tokens are not accepted',
      ),
    );
    return;
  }
  next();
}

/**
 * Whether a body value for `role` names admin once normalised the way the
 * controllers do it (trim, lower-case). Arrays are admin if any element is;
 * anything that is neither a string nor an array counts as admin, so a shape
 * the controllers might read differently is refused rather than guessed at.
 */
export function isAdminRole(value: unknown): boolean {
  if (typeof value === 'string') return value.trim().toLowerCase() === 'admin';
  if (Array.isArray(value)) return value.some(isAdminRole);
  return value !== null && value !== undefined;
}

export type DelegatedFieldRule = true | ((value: unknown) => boolean);

type SessionGuard = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => void;

export const SESSION_ONLY_MESSAGE =
  'This endpoint requires an interactive user session; OAuth access tokens and personal access tokens are not accepted';

/**
 * Like `requireSessionAuth`, but only for requests whose body sets one of the
 * named fields. A route that mixes ordinary profile fields with fields that
 * decide how someone signs in, or who holds admin, stays open to tokens for the
 * ordinary ones. `true` refuses the field with any value; a function refuses
 * the values it accepts. Must run after `authMiddleware.authenticate`.
 */
export function requireSessionAuthForFields(
  fields: Record<string, DelegatedFieldRule>,
): SessionGuard & { sessionOnlyFields: string[] } {
  const guard = (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): void => {
    if (!req.user) {
      next(new UnauthorizedError('Authentication required'));
      return;
    }
    if (req.user.isOAuth !== true) {
      next();
      return;
    }
    const body = (req.body ?? {}) as Record<string, unknown>;
    const refused = Object.entries(fields).some(([name, rule]) => {
      if (!(name in body) || body[name] === undefined) return false;
      return rule === true || rule(body[name]);
    });
    next(refused ? new ForbiddenError(SESSION_ONLY_MESSAGE) : undefined);
  };
  return Object.assign(guard, { sessionOnlyFields: Object.keys(fields) });
}

/**
 * Whether a body value sets anything: `null`, `undefined`, `{}` and `[]` do not; an object or
 * array with any entry (a `null` entry included, since a handler may read it as "clear this")
 * does, and so does any scalar, an empty string or `0` among them. The key names inside are
 * not looked at.
 */
export function isSetValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

/**
 * Keys a delegated token may send on a route: `true` takes any value, a list of names takes an
 * object whose own keys are among them.
 */
export type DelegatedAllowlist = Record<string, true | readonly string[]>;

/** A body, or a nested object, built by anything but `JSON.parse` has no business here. */
const hasForeignPrototype = (value: unknown): boolean => {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    return false;
  const proto: unknown = Object.getPrototypeOf(value);
  return proto !== Object.prototype && proto !== null;
};

const MAX_DEPTH = 32;

/**
 * Whether `value`, or anything nested in it, has a foreign prototype. Used for values under a key
 * that takes any value (`sync`, `filters`, a name), which are otherwise not looked into. Nesting
 * deeper than it walks counts as foreign, so a shape it cannot inspect is refused.
 */
const hasForeignPrototypeDeep = (value: unknown, depth = 0): boolean => {
  if (value === null || typeof value !== 'object') return false;
  if (depth > MAX_DEPTH) return true;
  if (hasForeignPrototype(value)) return true;
  const children = Array.isArray(value) ? value : Object.values(value);
  return children.some((child) => hasForeignPrototypeDeep(child, depth + 1));
};

/** Empty for the guard's purposes: sets nothing, and carries nothing inherited. */
const isEmptyValue = (value: unknown): boolean =>
  !isSetValue(value) && !hasForeignPrototype(value);

/**
 * Whether `body` has a key that is not allowed. A key off the list is refused whenever it is
 * present, whatever its value, unless its dotted path is in `emptyAllowed` and the value is empty.
 */
const refusesOutside = (
  body: unknown,
  allowed: DelegatedAllowlist,
  emptyAllowed: ReadonlySet<string>,
  prefix = '',
): boolean => {
  if (hasForeignPrototype(body)) return true;
  if (!isSetValue(body)) return false;
  if (typeof body !== 'object' || Array.isArray(body)) return true;
  return Object.entries(body as Record<string, unknown>).some(
    ([key, value]) => {
      if (value === undefined) return false;
      const rule = Object.prototype.hasOwnProperty.call(allowed, key)
        ? allowed[key]
        : undefined;
      if (rule === undefined)
        return !(emptyAllowed.has(`${prefix}${key}`) && isEmptyValue(value));
      if (rule === true) return hasForeignPrototypeDeep(value);
      return refusesOutside(
        value,
        Object.fromEntries(rule.map((k) => [k, true as const])),
        emptyAllowed,
        `${prefix}${key}.`,
      );
    },
  );
};

export interface AllowlistOptions {
  /**
   * Dotted paths of keys that are off the allowlist but may be sent with an empty value
   * (`{}`, `[]`, `null`) because a real caller does. Name that caller in a comment at the route.
   */
  emptyAllowed?: readonly string[];
}

/**
 * Like `requireSessionAuth`, but a token may still send the keys in `allowed`. Any other key is
 * refused whenever it is present, even with an empty value, so a field the controllers read but
 * nobody listed here (a credential, an OAuth app link, a host) can never reach them from a
 * token; `options.emptyAllowed` names the few keys that may be present but empty. A body, or a
 * nested object, whose prototype is not `Object.prototype` or `null` is refused too: its
 * inherited properties are invisible to a check of own keys but visible to the readers after it.
 * An allowlist is used instead of a list of names to refuse because no such list is complete.
 * Must run after `authMiddleware.authenticate`.
 */
export function requireSessionAuthExcept(
  allowed: DelegatedAllowlist,
  options: AllowlistOptions = {},
): SessionGuard & {
  sessionOnlyAllowlist: DelegatedAllowlist;
  sessionOnlyEmptyAllowed: string[];
} {
  const emptyAllowed: ReadonlySet<string> = new Set(options.emptyAllowed ?? []);
  const guard = (
    req: AuthenticatedUserRequest,
    _res: Response,
    next: NextFunction,
  ): void => {
    if (!req.user) {
      next(new UnauthorizedError('Authentication required'));
      return;
    }
    if (req.user.isOAuth !== true) {
      next();
      return;
    }
    next(
      refusesOutside(req.body ?? {}, allowed, emptyAllowed)
        ? new ForbiddenError(SESSION_ONLY_MESSAGE)
        : undefined,
    );
  };
  return Object.assign(guard, {
    sessionOnlyAllowlist: allowed,
    sessionOnlyEmptyAllowed: [...emptyAllowed],
  });
}

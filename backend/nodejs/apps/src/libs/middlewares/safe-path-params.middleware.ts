import { NextFunction, Request, Response, Router } from 'express';
import { BadRequestError } from '../errors/http.errors';

// Express has already percent-decoded path params. Pasted into another
// service's URL, `/` and `\` add path segments, `?` and `#` end the path,
// `.` and `..` are resolved away by URL parsing, and `%` would be decoded a
// second time by the receiving service. Spaces are fine: connector types such
// as "SHAREPOINT ONLINE" contain them.
// eslint-disable-next-line no-control-regex
const UNSAFE_CHARACTERS = /[/\\?#%\u0000-\u001f\u007f]/;

export const INVALID_PATH_SEGMENT_MESSAGE =
  "This address contains an ID that isn't valid. Check the link you followed and try again.";

// Checked on the trimmed value too: this runs before validation, and several
// param schemas trim, which would turn " .. " into a dot segment afterwards.
const DOT_SEGMENTS = new Set(['', '.', '..']);

export const isSafePathSegment = (value: unknown): value is string =>
  typeof value === 'string' &&
  !DOT_SEGMENTS.has(value) &&
  !DOT_SEGMENTS.has(value.trim()) &&
  !UNSAFE_CHARACTERS.test(value);

/**
 * Refuses a request whose named path params could not stand as one segment of
 * another service's URL. Call once per router, for every param the router's
 * handlers paste into a PipesHub service URL.
 */
export const guardPathParams = (router: Router, ...names: string[]): void => {
  for (const name of names) {
    router.param(
      name,
      (_req: Request, _res: Response, next: NextFunction, value: unknown) => {
        if (isSafePathSegment(value)) {
          next();
          return;
        }
        next(new BadRequestError(INVALID_PATH_SEGMENT_MESSAGE));
      },
    );
  }
};

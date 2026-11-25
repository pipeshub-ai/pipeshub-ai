import type { NextFunction, Response } from 'express';
import { BadRequestError } from '../../../libs/errors/http.errors';
import { ControlPlaneRevisionConflictError } from '../../control-plane';

const ETAG = /^(?:W\/)?"?([0-9]{1,15})"?$/;

/**
 * The org policy revision an admin save is made from (QA-9): `If-Match:
 * "<revision>"`. Absent (or `*`): the save is last-writer-wins, as before
 * CPD-4, because today's editors send none.
 */
export function ifMatchOf(header: string | undefined): number | undefined {
  if (header === undefined || header.trim() === '' || header.trim() === '*')
    return undefined;
  const match = ETAG.exec(header.trim());
  if (match === null)
    throw new BadRequestError(
      'If-Match must name one policy revision, as the ETag gave it',
    );
  return Number(match[1]);
}

/**
 * Answers the policy revision the response reflects; nothing when the
 * settings have none (policy store off). The tag names the org's policy, not
 * the whole body (which also carries release data), so the response is never
 * stored: a cache must not answer a later GET with 304 from it.
 */
export function setPolicyETag(res: Response, revision: number | null): void {
  if (revision === null) return;
  res.setHeader('ETag', `"${String(revision)}"`);
  res.setHeader('Cache-Control', 'no-store');
}

/** A save from a stale revision: 409 with the current revision as the `ETag` to reload from. */
export function passConflict(
  res: Response,
  next: NextFunction,
  error: unknown,
  map: (error: unknown) => unknown,
): void {
  if (
    error instanceof ControlPlaneRevisionConflictError &&
    error.current !== null
  ) {
    res.setHeader('ETag', `"${error.current}"`);
  }
  next(map(error));
}

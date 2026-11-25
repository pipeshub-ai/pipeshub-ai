/**
 * The organisation a harness store call runs as is the one its entry point bound: the request's authentication, the
 * attempt token's claims, the claimed run, the job row or a sweep's own cross-org enumeration (`forOrg`). A store
 * never binds the org its arguments name, so a wiring fault is refused here instead of being served as that org.
 */
import { getCurrentOrgId } from '../../../context/orgContext';
import { activeUnit } from '../../../db';
import { InternalServerError } from '../../../errors/http.errors';

/** A store call made with no organisation bound: its entry point did not bind one. */
export class UnboundOrgError extends InternalServerError {
  constructor() {
    super(
      'a harness store call ran with no organisation bound: its entry point must bind the organisation it works for',
    );
    this.name = 'UnboundOrgError';
  }
}

/** A store call made while another organisation is bound. */
export class OtherOrgBoundError extends InternalServerError {
  constructor() {
    super('the organisation of the call differs from the one bound to it');
    this.name = 'OtherOrgBoundError';
  }
}

/** Refuses the call unless `orgId` is the organisation bound in this async context. */
export function requireBoundOrg(orgId: string): void {
  const bound = getCurrentOrgId();
  if (bound === undefined) throw new UnboundOrgError();
  if (bound !== orgId) throw new OtherOrgBoundError();
}

/** `fn`, once `requireBoundOrg(orgId)` passes. */
export async function inBoundOrg<T>(
  orgId: string,
  fn: () => Promise<T>,
): Promise<T> {
  requireBoundOrg(orgId);
  return fn();
}

/**
 * A call into main's repositories as the bound organisation: in the unit already open (main's repository checks the
 * unit's org), else once `requireBoundOrg(orgId)` passes.
 */
export function inBoundOrgUnit<T>(
  orgId: string,
  fn: () => Promise<T>,
): Promise<T> {
  return activeUnit() ? fn() : inBoundOrg(orgId, fn);
}

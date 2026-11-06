import { BaseError } from './base.error';
import { markClientSafe } from './reader-friendly';

/** JSON values only: the response sanitizer turns a `Date` into `{}`, so callers pass ISO strings. */
export type PublicDetailValue =
  | string
  | number
  | boolean
  | null
  | readonly PublicDetailValue[]
  | { readonly [k: string]: PublicDetailValue };

/**
 * A domain failure whose code is sent as-is (no `HTTP_` prefix) and whose
 * `publicDetails` are sent to the client in every environment. Keep secrets and
 * internals out of both: use `metadata` for anything dev-only.
 */
export abstract class DomainHttpError extends BaseError {
  readonly publicDetails?: Readonly<Record<string, PublicDetailValue>>;

  protected constructor(
    code: string,
    message: string,
    statusCode: number,
    publicDetails?: Readonly<Record<string, PublicDetailValue>>,
  ) {
    super(code, message, statusCode);
    this.publicDetails = publicDetails;
    // Without this a 5xx domain error is rewritten to INTERNAL_ERROR.
    markClientSafe(this);
  }
}

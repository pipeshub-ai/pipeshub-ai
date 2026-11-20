import { verify, type KeyObject } from 'crypto';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import { HttpError } from '../../../libs/errors/http.errors';
import { trustedLicenseKeys } from './license-keys';
import {
  graceEndFor,
  ISSUED_AT_LEEWAY_MS,
  DEFAULT_GRACE_DAYS,
  MAX_LICENSE_BYTES,
} from './license-policy';
import {
  LICENSE_FORMAT,
  LICENSE_VERSION,
  licensePayloadSchema,
  LONE_SURROGATE,
  signedLicenseSchema,
  type LicenseErrorReason,
  type LicensePayload,
  type SignedLicense,
  type VerifiedLicense,
} from './license.types';

/** Domain separation: a signature made for anything else can never pass as a license. */
export const LICENSE_SIGNING_PREFIX = 'pipeshub-license/v1\n';

const MESSAGES: Record<LicenseErrorReason, string> = {
  MALFORMED: 'This is not a valid license file',
  UNSUPPORTED_FORMAT:
    'This license file uses a format this version does not support. Upgrade the installation or ask for a new license file.',
  NO_TRUSTED_KEY:
    'This installation has no license public key configured. Set PIPESHUB_LICENSE_PUBLIC_KEY and restart.',
  BAD_SIGNATURE:
    'The license signature does not match: the file was changed, or it was not signed with a key this installation trusts.',
  ISSUED_IN_FUTURE:
    "This license was issued after the server's current date. Check the server clock.",
  INVALID_DATES: 'The license end date must be after its start date.',
  EXPIRED: 'This license has already ended.',
  NEVER_IN_FORCE:
    'This license would never take effect: licenses issued after it already cover its entire term.',
  ADDRESS_REQUIRED:
    'This Pro license does not name the web address it is for. Ask PipesHub for a license issued for the address your users open.',
  WRONG_ADDRESS:
    "This license is for a different web address than this server's",
};

/** A 400 whose code (`HTTP_LICENSE_<REASON>`) names the reason, so a client can word it in its own language. */
export class LicenseError extends HttpError {
  constructor(
    readonly reason: LicenseErrorReason,
    detail?: string,
  ) {
    super(
      `LICENSE_${reason}`,
      detail ? `${MESSAGES[reason]}: ${detail}` : MESSAGES[reason],
      HTTP_STATUS.BAD_REQUEST,
      { reason },
    );
  }
}

/**
 * RFC 8785 (JCS) for the values a license can hold: object keys sorted by
 * UTF-16 code unit (what `Array.prototype.sort` does), strings and integers
 * serialised by `JSON.stringify`. Anything JCS would serialise differently
 * (non-integers, NaN, undefined) is refused rather than guessed at.
 */
export function canonicalJson(value: unknown): string {
  if (typeof value === 'string') {
    // Stored, it would be rewritten and its copy would never verify again.
    if (LONE_SURROGATE.test(value))
      throw new TypeError('only well-formed text is canonicalised');
    return JSON.stringify(value);
  }
  if (value === null || typeof value === 'boolean') {
    return JSON.stringify(value);
  }
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value))
      throw new TypeError('only safe integers are canonicalised');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(',')}}`;
  }
  throw new TypeError(`cannot canonicalise a ${typeof value}`);
}

export const licenseSigningInput = (license: LicensePayload): Buffer =>
  Buffer.from(LICENSE_SIGNING_PREFIX + canonicalJson(license), 'utf8');

const firstIssue = (error: {
  issues: Array<{ path: PropertyKey[]; message: string }>;
}): string => {
  const issue = error.issues[0];
  if (!issue) return 'unexpected content';
  const where =
    issue.path.length > 0 ? issue.path.map(String).join('.') : 'file';
  return `${where} ${issue.message}`;
};

/** Shape only: the file is a license of a supported format and version. */
export function parseLicenseInput(input: unknown): SignedLicense {
  if (input === null || typeof input !== 'object' || Array.isArray(input)) {
    throw new LicenseError('MALFORMED');
  }
  let size: number;
  try {
    size = Buffer.byteLength(JSON.stringify(input), 'utf8');
  } catch {
    throw new LicenseError('MALFORMED');
  }
  if (size > MAX_LICENSE_BYTES)
    throw new LicenseError('MALFORMED', 'the file is too large');

  const outer = signedLicenseSchema.safeParse(input);
  if (!outer.success)
    throw new LicenseError('MALFORMED', firstIssue(outer.error));
  if (
    outer.data.format !== LICENSE_FORMAT ||
    outer.data.license.version !== LICENSE_VERSION
  ) {
    throw new LicenseError('UNSUPPORTED_FORMAT');
  }
  const payload = licensePayloadSchema.safeParse(outer.data.license);
  if (!payload.success) {
    throw new LicenseError('MALFORMED', `license.${firstIssue(payload.error)}`);
  }
  try {
    canonicalJson(payload.data);
  } catch (error) {
    throw new LicenseError(
      'MALFORMED',
      `license ${error instanceof Error ? error.message : 'cannot be read'}`,
    );
  }
  return {
    format: LICENSE_FORMAT,
    keyId: outer.data.keyId,
    license: payload.data,
    signature: outer.data.signature,
  };
}

/** Any trusted key may match (`keyId` is only the issuer's label); a garbled signature is a mismatch, never a throw. */
export function verifyLicenseSignature(
  file: SignedLicense,
): 'ok' | 'NO_TRUSTED_KEY' | 'BAD_SIGNATURE' {
  const keys = trustedLicenseKeys();
  if (keys.length === 0) return 'NO_TRUSTED_KEY';
  if (!/^[A-Za-z0-9_-]+$/.test(file.signature)) return 'BAD_SIGNATURE';
  const input = licenseSigningInput(file.license);
  const signature = Buffer.from(file.signature, 'base64url');
  const matches = (key: KeyObject): boolean => {
    try {
      return verify(null, input, key, signature);
    } catch {
      return false;
    }
  };
  return keys.some(matches) ? 'ok' : 'BAD_SIGNATURE';
}

export function toVerifiedLicense(file: SignedLicense): VerifiedLicense {
  const graceDays = file.license.graceDays ?? DEFAULT_GRACE_DAYS;
  const endDate = new Date(file.license.endDate);
  return {
    file,
    payload: file.license,
    licenseId: file.license.licenseId,
    startDate: new Date(file.license.startDate),
    endDate,
    issuedAt: new Date(file.license.issuedAt),
    graceDays,
    graceEndsAt: graceEndFor(endDate, graceDays),
  };
}

/**
 * Everything a license must pass. `install` (the default) also refuses one
 * issued ahead of the clock or already ended; `stored` re-checks a license
 * read back from the database, where an ended one still counts for history
 * and expiry.
 */
export function checkLicense(
  input: unknown,
  now: Date,
  { purpose = 'install' }: { purpose?: 'install' | 'stored' } = {},
): VerifiedLicense {
  const file = parseLicenseInput(input);
  const signature = verifyLicenseSignature(file);
  if (signature !== 'ok') throw new LicenseError(signature);

  const verified = toVerifiedLicense(file);
  if (verified.endDate.getTime() <= verified.startDate.getTime()) {
    throw new LicenseError('INVALID_DATES');
  }
  if (purpose === 'install') {
    if (verified.issuedAt.getTime() > now.getTime() + ISSUED_AT_LEEWAY_MS) {
      throw new LicenseError('ISSUED_IN_FUTURE');
    }
    if (verified.endDate.getTime() <= now.getTime())
      throw new LicenseError('EXPIRED');
  }
  return verified;
}

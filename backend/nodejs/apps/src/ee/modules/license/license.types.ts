import { z } from 'zod';
import {
  MAX_GRACE_DAYS,
  MAX_LICENSE_SEATS,
  UNLIMITED_SEATS,
} from './license-policy';

export const LICENSE_FORMAT = 'pipeshub-license';
export const LICENSE_VERSION = 1;

export const licenseTypes = ['trial', 'pro'] as const;
export type LicenseType = (typeof licenseTypes)[number];

export const licenseStatuses = [
  'none',
  'pending',
  'active',
  'expiring',
  'grace',
  'expired',
] as const;
export type LicenseStatus = (typeof licenseStatuses)[number];

export type LicenseSeats = number | typeof UNLIMITED_SEATS;

export const isUnlimitedSeats = (
  seats: LicenseSeats,
): seats is typeof UNLIMITED_SEATS => seats === UNLIMITED_SEATS;

// An ISO 8601 instant with an explicit zone; a bare date would depend on the reader's zone.
const ISO_INSTANT =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$/;

const isoInstant = z
  .string()
  .max(40)
  .refine(
    (value) => ISO_INSTANT.test(value) && !Number.isNaN(Date.parse(value)),
    {
      message: 'must be an ISO 8601 date and time with a zone',
    },
  );

/** Scheme, host and port of an http(s) address, or null: what a license's address and the server's must share. */
export function webOrigin(value: string | undefined): string | null {
  try {
    const url = new URL(value?.trim() ?? '');
    return url.protocol === 'https:' || url.protocol === 'http:'
      ? url.origin
      : null;
  } catch {
    return null;
  }
}

export const LONE_SURROGATE =
  /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;

// No transforms anywhere: the parsed object must canonicalize to exactly what
// was signed, and a file whose parsed copy differs from its content is refused.
export const licensePayloadSchema = z
  .object({
    version: z.literal(LICENSE_VERSION),
    licenseId: z
      .string()
      .regex(
        /^[A-Za-z0-9._-]{1,100}$/,
        'letters, digits, dot, dash or underscore, at most 100',
      ),
    licensee: z
      .string()
      .max(200)
      .refine((value) => value.trim().length > 0, {
        message: 'must not be empty',
      })
      // A lone surrogate verifies here but is rewritten when stored, so the stored copy never would.
      .refine((value) => !LONE_SURROGATE.test(value), {
        message: 'must be well-formed text',
      }),
    type: z.enum(licenseTypes),
    seats: z.union([
      z.number().int().min(1).max(MAX_LICENSE_SEATS),
      z.literal(UNLIMITED_SEATS),
    ]),
    startDate: isoInstant,
    endDate: isoInstant,
    issuedAt: isoInstant,
    graceDays: z.number().int().min(0).max(MAX_GRACE_DAYS).optional(),
    customerId: z
      .string()
      .regex(
        /^[A-Za-z0-9._-]{1,100}$/,
        'letters, digits, dot, dash or underscore, at most 100',
      )
      .optional(),
    /** The web address users open; the license runs only where FRONTEND_PUBLIC_URL is that address. */
    url: z
      .string()
      .max(200)
      .refine((value) => webOrigin(value) !== null, {
        message: 'must be an http or https web address',
      })
      .optional(),
  })
  // A field a newer issuer adds is kept, so the signature still covers it, and otherwise ignored.
  .loose();

export type LicensePayload = z.infer<typeof licensePayloadSchema>;

export const signedLicenseSchema = z
  .object({
    format: z.string().max(100),
    keyId: z.string().min(1).max(100),
    license: z.record(z.string(), z.unknown()),
    signature: z.string().max(200),
  })
  .strict();

export interface SignedLicense {
  format: typeof LICENSE_FORMAT;
  keyId: string;
  license: LicensePayload;
  signature: string;
}

export interface VerifiedLicense {
  file: SignedLicense;
  payload: LicensePayload;
  licenseId: string;
  startDate: Date;
  endDate: Date;
  issuedAt: Date;
  graceDays: number;
  graceEndsAt: Date;
}

export const licenseErrorReasons = [
  'MALFORMED',
  'UNSUPPORTED_FORMAT',
  'NO_TRUSTED_KEY',
  'BAD_SIGNATURE',
  'ISSUED_IN_FUTURE',
  'INVALID_DATES',
  'EXPIRED',
  'NEVER_IN_FORCE',
  'ADDRESS_REQUIRED',
  'WRONG_ADDRESS',
] as const;
export type LicenseErrorReason = (typeof licenseErrorReasons)[number];

export interface LicenseEvaluation {
  status: LicenseStatus;
  /** The license in force now; set for active, expiring and grace. */
  effective: VerifiedLicense | null;
  /** The next license to start, when one is installed ahead of time. */
  upcoming: VerifiedLicense | null;
  /** The most recently ended license, for an expired workspace. */
  latest: VerifiedLicense | null;
}

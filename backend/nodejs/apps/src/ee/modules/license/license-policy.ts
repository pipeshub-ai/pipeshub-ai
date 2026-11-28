// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

// Values a license is checked against. Constants, not env: anything the
// operator could set here would be a way around the license.

const DAY_MS = 24 * 60 * 60 * 1000;

/** The grace period of a license that does not name its own `graceDays`; 0 is honored as no grace. */
export const DEFAULT_GRACE_DAYS = 3;
/** The most `graceDays` a file may name; a file with more is refused as malformed. */
export const MAX_GRACE_DAYS = 5;

/** Admins are warned this long before the license ends. */
export const EXPIRY_WARNING_DAYS = 7;
export const EXPIRY_WARNING_MS = EXPIRY_WARNING_DAYS * DAY_MS;

/** Clock skew tolerated between the issuer and the install when checking `issuedAt`. */
export const ISSUED_AT_LEEWAY_MS = DAY_MS;

/** A clock further behind the last license check than this counts as set back: license time then counts on from that check. */
export const CLOCK_ROLLBACK_TOLERANCE_MS = 60 * 60 * 1000;

export const MAX_LICENSE_SEATS = 100_000;
/** The `seats` value of a license with no seat cap. */
export const UNLIMITED_SEATS = 'unlimited' as const;
/** A real license file is well under 1 KB. */
export const MAX_LICENSE_BYTES = 16 * 1024;

export const graceEndFor = (endDate: Date, graceDays: number): Date =>
  new Date(endDate.getTime() + graceDays * DAY_MS);

/** The paid feature that copies who can see what from a connector's source. */
export const PERMISSION_SYNC_FEATURE = 'permissionSync';

/**
 * What a license without a `features` list grants: every paid feature that
 * existed before the field did, so a license already issued keeps what it
 * bought. A feature added later is granted only by a license that names it.
 */
export const LEGACY_FEATURES: readonly string[] = [PERMISSION_SYNC_FEATURE];

/** Whether the license turns on `feature`. */
export const licenseGrants = (
  payload: { features?: readonly string[] },
  feature: string,
): boolean => (payload.features ?? LEGACY_FEATURES).includes(feature);

/**
 * Whether an install with no license in force is held to the free edition.
 * Off until the release that turns the license check on: until then such an
 * install keeps every feature, as before licenses existed. Read in one place,
 * `publishPermissionSync`.
 */
export const LICENSE_ENFORCED: boolean = false;

/** Once enforcement is on, an install already using paid features keeps them this long from its first start on that version. */
export const UPGRADE_GRACE_DAYS = 21;
export const UPGRADE_GRACE_MS = UPGRADE_GRACE_DAYS * DAY_MS;

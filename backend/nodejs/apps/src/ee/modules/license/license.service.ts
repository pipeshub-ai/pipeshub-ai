import { ConflictError } from '../../../libs/errors/http.errors';
import { licenseAddressProblem } from './license-binding';
import { checkLicense, LicenseError } from './license-codec';
import {
  LEGACY_FEATURES,
  LICENSE_ENFORCED,
  licenseGrants,
  PERMISSION_SYNC_FEATURE,
  UPGRADE_GRACE_MS,
} from './license-policy';
import { evaluateLicenses, licenseTime, neverInForce } from './license-state';
import {
  type LicenseEvaluation,
  type LicenseStatus,
  type SignedLicense,
  type VerifiedLicense,
} from './license.types';

/** Every license file installed here, as signed: `{ licenses: SignedLicense[] }`. */
export const LICENSE_FILES_KEY = '/services/licence/files';
/** Read by the Python connectors (`permission_sync_gate.py`); they act on `allowed` and a passed `until`. */
export const PERMISSION_SYNC_LICENCE_KEY = '/services/licence/permissionSync';
/** When enforcement first ran here, and whether the install was using paid features then: an `UpgradeRecord`. */
export const UPGRADE_KEY = '/services/licence/upgrade';
/** Written by the Python connectors at start-up (`permission_sync_gate.install_affected`): `{ affected: boolean, ... }`. */
export const INSTALL_AFFECTED_KEY = '/services/licence/affected';

/**
 * Saved at the first start with enforcement on. `affected` is the first
 * answer the connectors give after that, kept from then on: an install that
 * was not using paid features at the upgrade gets no grace by adding them later.
 */
export interface UpgradeRecord {
  upgradedAt: string;
  /** Null until the connectors have reported; an install not known to be unaffected gets the grace. */
  affected: boolean | null;
}

/** The configuration store as this module uses it: one JSON value per key. */
export interface LicenseStore {
  read(key: string): Promise<unknown>;
  write(key: string, value: unknown): Promise<void>;
}

/**
 * What the connectors read. `allowed` is the whole decision; `until` is when
 * the answer may next turn to not allowed, null when not allowed. Outside the
 * upgrade grace it is when the license in force ends its grace period (a
 * renewal already installed may carry it further). During the upgrade grace it
 * is the later of that and the upgrade grace end, so a missing, shorter or
 * lapsed license never shortens it.
 */
export interface PermissionSyncAnswer {
  allowed: boolean;
  until: string | null;
  status: LicenseStatus;
}

export interface LicenseView {
  licenseId: string;
  licensee: string;
  type: string;
  seats: number | string;
  /** What the license turns on; a license without the field shows LEGACY_FEATURES. */
  features: string[];
  url: string | null;
  startDate: string;
  endDate: string;
  graceEndsAt: string;
}

export interface LicenseStatusView {
  status: LicenseStatus;
  /** The license in force, or for an expired install the one that ended last. */
  license: LicenseView | null;
  upcoming: LicenseView | null;
  /** Null while no license has ever started here: the connectors then behave as before licenses existed. */
  permissionSync: PermissionSyncAnswer | null;
  /** Installed files that do not count here: the signature, the trusted key or the web address does not match. */
  ignored: number;
  /** Set once enforcement is on: when the upgrade grace ends (null when none is given), and whether the install needed it. */
  upgradeGrace: { endsAt: string | null; affected: boolean | null } | null;
}

/** A stored entry as read back: written by this module, but the store is not trusted to keep it whole. */
type StoredFile = Partial<SignedLicense> | null;

interface Loaded {
  files: StoredFile[];
  valid: VerifiedLicense[];
}

async function loadLicenses(store: LicenseStore, now: Date): Promise<Loaded> {
  const raw = (await store.read(LICENSE_FILES_KEY)) as {
    licenses?: unknown;
  } | null;
  const files = Array.isArray(raw?.licenses)
    ? (raw.licenses as StoredFile[])
    : [];
  const valid = files.flatMap((file) => {
    try {
      const verified = checkLicense(file, now, { purpose: 'stored' });
      return licenseAddressProblem(verified.payload) ? [] : [verified];
    } catch {
      return [];
    }
  });
  return { files, valid };
}

const evaluate = (valid: VerifiedLicense[], now: Date): LicenseEvaluation =>
  // shortcut: no saved clock mark, so a clock set back counts only down to the newest issue date; add one if that matters.
  evaluateLicenses(valid, licenseTime(now, null, valid));

/** When the upgrade grace ends, or null for an install known not to need one. */
export function upgradeGraceEnd(upgrade: UpgradeRecord): Date | null {
  if (upgrade.affected === false) return null;
  return new Date(Date.parse(upgrade.upgradedAt) + UPGRADE_GRACE_MS);
}

/** What the licenses alone decide, or null while none has ever started here (none installed, or only one dated ahead). */
function licenseAnswer(
  evaluation: LicenseEvaluation,
  installed: number,
): PermissionSyncAnswer | null {
  if (installed === 0 || evaluation.status === 'pending') return null;
  const license = evaluation.effective;
  if (!license || !licenseGrants(license.payload, PERMISSION_SYNC_FEATURE)) {
    return { allowed: false, until: null, status: evaluation.status };
  }
  return {
    allowed: true,
    until: license.graceEndsAt.toISOString(),
    status: evaluation.status,
  };
}

/**
 * The answer for the connectors, or null to write nothing.
 * - Enforcement off (`upgrade` null): the licenses decide once one has
 *   started; before that the key stays missing, which the connectors read as
 *   licensed, as before licenses existed.
 * - Enforcement on: through the upgrade grace, allowed whatever the licenses
 *   say, until the later of the grace end and the licenses' own `until`: a
 *   license (a trial, say) only ever extends access, never shortens the grace.
 *   After the grace, the licenses decide, and no license means not allowed.
 */
export function permissionSyncAnswer(
  evaluation: LicenseEvaluation,
  installed: number,
  upgrade: UpgradeRecord | null = null,
  now: Date = new Date(),
): PermissionSyncAnswer | null {
  const byLicense = licenseAnswer(evaluation, installed);
  if (!upgrade) return byLicense;
  const graceEnd = upgradeGraceEnd(upgrade);
  if (!graceEnd || now.getTime() >= graceEnd.getTime()) {
    return (
      byLicense ?? { allowed: false, until: null, status: evaluation.status }
    );
  }
  const licenseUntil = byLicense?.allowed ? byLicense.until : null;
  const until =
    licenseUntil !== null && Date.parse(licenseUntil) > graceEnd.getTime()
      ? licenseUntil
      : graceEnd.toISOString();
  return { allowed: true, until, status: evaluation.status };
}

const viewOf = (license: VerifiedLicense | null): LicenseView | null =>
  license && {
    licenseId: license.licenseId,
    licensee: license.payload.licensee,
    type: license.payload.type,
    seats: license.payload.seats,
    features: [...(license.payload.features ?? LEGACY_FEATURES)],
    url: license.payload.url ?? null,
    startDate: license.startDate.toISOString(),
    endDate: license.endDate.toISOString(),
    graceEndsAt: license.graceEndsAt.toISOString(),
  };

const readUpgrade = async (
  store: LicenseStore,
): Promise<UpgradeRecord | null> => {
  const stored = (await store.read(UPGRADE_KEY)) as UpgradeRecord | null;
  return typeof stored?.upgradedAt === 'string' ? stored : null;
};

/**
 * How long before the upgrade record a connectors' report may be dated and still count as this boot's: the
 * connectors and this service start together, and either may finish first.
 */
export const AFFECTED_REPORT_SKEW_MS = 5 * 60 * 1000;

/**
 * The upgrade record, made at the first check with enforcement on. Its
 * `affected` is filled in once, from the first answer the connectors give for
 * this boot. `true` (the grace stands) is taken from any report; `false` only
 * from one checked at or after the upgrade, so a report left from an earlier
 * boot, before connectors were added, cannot take the grace away.
 */
async function upgradeRecord(
  store: LicenseStore,
  now: Date,
): Promise<UpgradeRecord> {
  const stored = await readUpgrade(store);
  const record: UpgradeRecord = stored ?? {
    upgradedAt: now.toISOString(),
    affected: null,
  };
  let changed = stored === null;
  if (record.affected === null) {
    const reported = (await store.read(INSTALL_AFFECTED_KEY)) as {
      affected?: unknown;
      checkedAt?: unknown;
    } | null;
    const checkedAt =
      typeof reported?.checkedAt === 'string'
        ? Date.parse(reported.checkedAt)
        : Number.NaN;
    const thisBoot =
      checkedAt >= Date.parse(record.upgradedAt) - AFFECTED_REPORT_SKEW_MS;
    if (
      reported?.affected === true ||
      (reported?.affected === false && thisBoot)
    ) {
      record.affected = reported.affected;
      changed = true;
    }
  }
  if (changed) await store.write(UPGRADE_KEY, record);
  return record;
}

export async function licenseStatus(
  store: LicenseStore,
  now: Date = new Date(),
): Promise<LicenseStatusView> {
  const { files, valid } = await loadLicenses(store, now);
  const evaluation = evaluate(valid, now);
  // Only enforcement writes the record, so with it off this is null.
  const upgrade = await readUpgrade(store);
  return {
    status: evaluation.status,
    license: viewOf(evaluation.effective ?? evaluation.latest),
    upcoming: viewOf(evaluation.upcoming),
    permissionSync: permissionSyncAnswer(
      evaluation,
      files.length,
      upgrade,
      now,
    ),
    ignored: files.length - valid.length,
    upgradeGrace: upgrade && {
      endsAt: upgradeGraceEnd(upgrade)?.toISOString() ?? null,
      affected: upgrade.affected,
    },
  };
}

const sameAnswer = (stored: unknown, answer: PermissionSyncAnswer): boolean =>
  JSON.stringify(stored) === JSON.stringify(answer);

let publishing: Promise<unknown> = Promise.resolve();

/**
 * Computes the connectors' answer and writes it when it changed. One pass at a
 * time in this process, so a periodic pass that read the files before an
 * install never writes its older answer after the install's.
 */
export function publishPermissionSync(
  store: LicenseStore,
  now: () => Date = () => new Date(),
  enforced: boolean = LICENSE_ENFORCED,
): Promise<PermissionSyncAnswer | null> {
  const pass = publishing.then(async () => {
    const at = now();
    const { files, valid } = await loadLicenses(store, at);
    const upgrade = enforced ? await upgradeRecord(store, at) : null;
    const answer = permissionSyncAnswer(
      evaluate(valid, at),
      files.length,
      upgrade,
      at,
    );
    if (
      answer &&
      !sameAnswer(await store.read(PERMISSION_SYNC_LICENCE_KEY), answer)
    ) {
      await store.write(PERMISSION_SYNC_LICENCE_KEY, answer);
    }
    return answer;
  });
  publishing = pass.catch(() => undefined);
  return pass;
}

export interface InstallResult {
  installed: boolean;
  alreadyInstalled: boolean;
  status: LicenseStatusView;
}

/**
 * Installs a license file next to those already here; the newest issued among
 * those running is the one in force, so a later file replaces an earlier one,
 * and a renewal dated ahead takes over when it starts. Refuses a file that
 * would never be in force. Publishes the connectors' answer at once.
 */
export async function installLicense(
  store: LicenseStore,
  input: unknown,
  now: Date = new Date(),
): Promise<InstallResult> {
  const candidate = checkLicense(input, now, { purpose: 'stored' });
  const addressProblem = licenseAddressProblem(candidate.payload);
  if (addressProblem) throw addressProblem;

  const { files, valid } = await loadLicenses(store, now);
  const existing = files.find(
    (file) => file?.license?.licenseId === candidate.licenseId,
  );
  const alreadyInstalled =
    existing?.signature === candidate.file.signature &&
    valid.some((license) => license.licenseId === candidate.licenseId);
  if (!alreadyInstalled) {
    const others = valid.filter(
      (license) => license.licenseId !== candidate.licenseId,
    );
    if (existing && others.length < valid.length) {
      throw new ConflictError(
        `A different license with the ID ${candidate.licenseId} is already installed.`,
      );
    }
    // The install-only rules (issued ahead of the clock, already ended) at license time, not the candidate's own dates.
    const at = licenseTime(now, null, others);
    checkLicense(input, at);
    if (neverInForce(candidate, others, at))
      throw new LicenseError('NEVER_IN_FORCE');
    // shortcut: read then write, so two installs at the same moment can lose one; installs are rare admin actions.
    await store.write(LICENSE_FILES_KEY, {
      licenses: [...files.filter((file) => file !== existing), candidate.file],
    });
  }
  await publishPermissionSync(store, () => now);
  return {
    installed: !alreadyInstalled,
    alreadyInstalled,
    status: await licenseStatus(store, now),
  };
}

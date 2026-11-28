// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

import { ConflictError } from '../../../libs/errors/http.errors';
import { licenseAddressProblem } from './license-binding';
import { checkLicense, LicenseError } from './license-codec';
import {
  LEGACY_FEATURES,
  licenseGrants,
  PERMISSION_SYNC_FEATURE,
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
/** Read by the Python connectors (`permission_sync_gate.py`); they act on `allowed` alone. */
export const PERMISSION_SYNC_LICENCE_KEY = '/services/licence/permissionSync';

/** The configuration store as this module uses it: one JSON value per key. */
export interface LicenseStore {
  read(key: string): Promise<unknown>;
  write(key: string, value: unknown): Promise<void>;
}

/**
 * What the connectors read. `allowed` is the whole decision; `until` is when
 * the license in force ends its grace period (a renewal already installed may
 * carry it further), null when not allowed.
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

/**
 * The answer for the connectors, or null to write nothing. Until the release
 * that turns the check on, an install where no license has ever started (none
 * installed, or only one dated ahead) keeps its key missing, which the
 * connectors read as licensed. Once one has started, the license decides.
 */
export function permissionSyncAnswer(
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

export async function licenseStatus(
  store: LicenseStore,
  now: Date = new Date(),
): Promise<LicenseStatusView> {
  const { files, valid } = await loadLicenses(store, now);
  const evaluation = evaluate(valid, now);
  return {
    status: evaluation.status,
    license: viewOf(evaluation.effective ?? evaluation.latest),
    upcoming: viewOf(evaluation.upcoming),
    permissionSync: permissionSyncAnswer(evaluation, files.length),
    ignored: files.length - valid.length,
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
): Promise<PermissionSyncAnswer | null> {
  const pass = publishing.then(async () => {
    const at = now();
    const { files, valid } = await loadLicenses(store, at);
    const answer = permissionSyncAnswer(evaluate(valid, at), files.length);
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

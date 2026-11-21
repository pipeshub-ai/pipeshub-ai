import { expect } from 'chai';
import { generateKeyPairSync, sign, type KeyObject } from 'crypto';
import {
  licenseAddressProblem,
  PUBLIC_URL_ENV,
} from '../../../../src/ee/modules/license/license-binding';
import {
  checkLicense,
  LicenseError,
  licenseSigningInput,
} from '../../../../src/ee/modules/license/license-codec';
import {
  LICENSE_PUBLIC_KEY_ENV,
  resolveLicenseKeys,
  useLicenseKeysForTesting,
} from '../../../../src/ee/modules/license/license-keys';
import {
  CLOCK_ROLLBACK_TOLERANCE_MS,
  DEFAULT_GRACE_DAYS,
} from '../../../../src/ee/modules/license/license-policy';
import {
  evaluateLicenses,
  licenseTime,
} from '../../../../src/ee/modules/license/license-state';
import {
  LICENSE_FORMAT,
  type LicensePayload,
  type SignedLicense,
} from '../../../../src/ee/modules/license/license.types';

const DAY_MS = 24 * 60 * 60 * 1000;
const NOW = new Date('2026-11-20T12:00:00.000Z');
const ADDRESS = 'https://pipeshub.license.test';
const iso = (offsetMs: number): string =>
  new Date(NOW.getTime() + offsetMs).toISOString();

const payload = (overrides: Partial<LicensePayload> = {}): LicensePayload => ({
  version: 1,
  licenseId: 'lic_core_test',
  licensee: 'Example Org',
  type: 'pro',
  seats: 100,
  startDate: iso(-DAY_MS),
  endDate: iso(30 * DAY_MS),
  issuedAt: iso(-DAY_MS),
  url: ADDRESS,
  ...overrides,
});

const reasonOf = (run: () => unknown): string => {
  try {
    run();
  } catch (error) {
    if (error instanceof LicenseError) return error.reason;
    throw error;
  }
  return 'accepted';
};

describe('license core', () => {
  // Keys are made for this run: no real key is ever in a test.
  let privateKey: KeyObject;
  let restoreKeys: () => void;

  const signed = (
    license: LicensePayload,
    key: KeyObject = privateKey,
  ): SignedLicense => ({
    format: LICENSE_FORMAT,
    keyId: 'test-key',
    license,
    signature: sign(null, licenseSigningInput(license), key).toString(
      'base64url',
    ),
  });

  before(() => {
    const pair = generateKeyPairSync('ed25519');
    privateKey = pair.privateKey;
    restoreKeys = useLicenseKeysForTesting([
      pair.publicKey.export({ format: 'pem', type: 'spki' }),
    ]);
  });

  after(() => {
    restoreKeys();
  });

  it('accepts a file signed with a trusted key and gives it the default grace', () => {
    const verified = checkLicense(signed(payload()), NOW);
    expect(verified.licenseId).to.equal('lic_core_test');
    expect(verified.graceDays).to.equal(DEFAULT_GRACE_DAYS);
    expect(verified.graceEndsAt.getTime()).to.equal(
      verified.endDate.getTime() + DEFAULT_GRACE_DAYS * DAY_MS,
    );
  });

  it('refuses a file signed with a key this build does not trust', () => {
    const stranger = generateKeyPairSync('ed25519').privateKey;
    expect(
      reasonOf(() => checkLicense(signed(payload(), stranger), NOW)),
    ).to.equal('BAD_SIGNATURE');
  });

  it('refuses a file whose content changed after it was signed', () => {
    const file = signed(payload());
    const tampered = { ...file, license: { ...file.license, seats: 5000 } };
    expect(reasonOf(() => checkLicense(tampered, NOW))).to.equal(
      'BAD_SIGNATURE',
    );
  });

  it('refuses every file when no key is trusted', () => {
    const file = signed(payload());
    const restore = useLicenseKeysForTesting([]);
    try {
      expect(reasonOf(() => checkLicense(file, NOW))).to.equal(
        'NO_TRUSTED_KEY',
      );
    } finally {
      restore();
    }
  });

  it('refuses to install an ended license but still reads a stored one', () => {
    const ended = signed(
      payload({ startDate: iso(-60 * DAY_MS), endDate: iso(-10 * DAY_MS) }),
    );
    expect(reasonOf(() => checkLicense(ended, NOW))).to.equal('EXPIRED');
    expect(checkLicense(ended, NOW, { purpose: 'stored' }).licenseId).to.equal(
      'lic_core_test',
    );
  });

  it('keeps a license in force through its grace days and ends it after them', () => {
    const verified = checkLicense(signed(payload({ graceDays: 2 })), NOW);
    const afterEnd = (days: number): Date =>
      new Date(verified.endDate.getTime() + days * DAY_MS);

    const inGrace = evaluateLicenses([verified], afterEnd(1));
    expect(inGrace.status).to.equal('grace');
    expect(inGrace.effective?.licenseId).to.equal('lic_core_test');

    const pastGrace = evaluateLicenses([verified], afterEnd(3));
    expect(pastGrace.status).to.equal('expired');
    expect(pastGrace.effective).to.equal(null);
    expect(pastGrace.latest?.licenseId).to.equal('lic_core_test');
  });

  it('does not let a clock set back move license time back', () => {
    const lastCheck = { at: NOW, clock: NOW };
    const setBack = new Date(NOW.getTime() - 2 * CLOCK_ROLLBACK_TOLERANCE_MS);
    expect(licenseTime(setBack, lastCheck, []).getTime()).to.equal(
      NOW.getTime(),
    );
    // Time the process measured since the last check still counts.
    expect(licenseTime(setBack, lastCheck, [], 5 * 60_000).getTime()).to.equal(
      NOW.getTime() + 5 * 60_000,
    );
  });

  it('runs a license only at the web address it names', () => {
    const license = payload();
    expect(
      licenseAddressProblem(license, { [PUBLIC_URL_ENV]: ADDRESS }),
    ).to.equal(null);
    expect(
      licenseAddressProblem(license, {
        [PUBLIC_URL_ENV]: 'https://elsewhere.test',
      })?.reason,
    ).to.equal('WRONG_ADDRESS');
    expect(
      licenseAddressProblem(
        { type: 'pro', url: undefined },
        { [PUBLIC_URL_ENV]: ADDRESS },
      )?.reason,
    ).to.equal('ADDRESS_REQUIRED');
  });

  it('keeps a field it does not know, and the signature covers it', () => {
    const file = signed(payload({ addedLater: ['some-value'] }));
    expect(checkLicense(file, NOW).payload.addedLater).to.deep.equal([
      'some-value',
    ]);
    const withoutIt = { ...file.license };
    delete withoutIt.addedLater;
    expect(
      reasonOf(() => checkLicense({ ...file, license: withoutIt }, NOW)),
    ).to.equal('BAD_SIGNATURE');
  });

  it('refuses a file carrying a `__proto__` field the signature does not cover', () => {
    const text = JSON.stringify(signed(payload())).replace(
      '"license":{',
      '"license":{"__proto__":{"x":1},',
    );
    expect(text).to.include('__proto__');
    expect(reasonOf(() => checkLicense(JSON.parse(text), NOW))).to.equal(
      'MALFORMED',
    );
  });

  it('hands back exactly the value the signature was checked over', () => {
    const file = signed(payload({ addedLater: { nested: [1, 'two'] } }));
    const verified = checkLicense(JSON.parse(JSON.stringify(file)), NOW);
    expect(verified.payload).to.deep.equal(file.license);
    expect(Object.getPrototypeOf(verified.payload)).to.equal(Object.prototype);
    expect(
      sign(null, licenseSigningInput(verified.payload), privateKey).toString(
        'base64url',
      ),
    ).to.equal(file.signature);
  });

  it('trusts only the built-in keys when the build has any, whatever env says', () => {
    const pem = (): string =>
      generateKeyPairSync('ed25519').publicKey.export({
        format: 'pem',
        type: 'spki',
      });
    const env = { [LICENSE_PUBLIC_KEY_ENV]: pem() };

    const fromEnv = resolveLicenseKeys(env, [], true);
    expect(fromEnv.source).to.equal('env');
    expect(fromEnv.keys).to.have.length(1);

    const builtIn = resolveLicenseKeys(env, [pem()], true);
    expect(builtIn.source).to.equal('built-in');
    expect(builtIn.keys).to.have.length(1);
    expect(builtIn.problems.join(' ')).to.include('is ignored');

    expect(resolveLicenseKeys(env, [], false).keys).to.have.length(0);
  });
});

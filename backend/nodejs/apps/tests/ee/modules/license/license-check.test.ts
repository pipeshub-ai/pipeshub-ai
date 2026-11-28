// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

import 'reflect-metadata';
import { expect } from 'chai';
import { generateKeyPairSync, sign, type KeyObject } from 'crypto';
import express from 'express';
import { Container } from 'inversify';
import type { AddressInfo } from 'net';
import sinon from 'sinon';
import { PUBLIC_URL_ENV } from '../../../../src/ee/modules/license/license-binding';
import {
  createLicenseRouter,
  encryptedLicenseStore,
} from '../../../../src/ee/modules/license/license-boot';
import {
  checkLicense,
  LicenseError,
  licenseSigningInput,
} from '../../../../src/ee/modules/license/license-codec';
import { useLicenseKeysForTesting } from '../../../../src/ee/modules/license/license-keys';
import {
  DEFAULT_GRACE_DAYS,
  DEVELOPMENT_SEATS,
  LICENSE_ENFORCED,
  licenseGrants,
  PERMISSION_SYNC_FEATURE,
  UPGRADE_GRACE_DAYS,
} from '../../../../src/ee/modules/license/license-policy';
import {
  AFFECTED_REPORT_SKEW_MS,
  INSTALL_AFFECTED_KEY,
  installLicense,
  LICENSE_FILES_KEY,
  licenseStatus,
  PERMISSION_SYNC_LICENCE_KEY,
  publishPermissionSync,
  UPGRADE_KEY,
  type LicenseStore,
  type UpgradeRecord,
} from '../../../../src/ee/modules/license/license.service';
import {
  LICENSE_FORMAT,
  type LicensePayload,
  type SignedLicense,
} from '../../../../src/ee/modules/license/license.types';
import { EncryptionService } from '../../../../src/libs/encryptor/encryptor';
import { BaseError } from '../../../../src/libs/errors/base.error';
import { xssSanitizationMiddleware } from '../../../../src/libs/middlewares/xss-sanitization.middleware';
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service';

const DAY_MS = 24 * 60 * 60 * 1000;
const NOW = new Date('2026-11-20T12:00:00.000Z');
const END = new Date(NOW.getTime() + 30 * DAY_MS);
const GRACE_END = new Date(END.getTime() + DEFAULT_GRACE_DAYS * DAY_MS);
const ADDRESS = 'https://pipeshub.license.test';
const at = (date: Date, offsetMs = 0): Date =>
  new Date(date.getTime() + offsetMs);

const payload = (overrides: Partial<LicensePayload> = {}): LicensePayload => ({
  version: 1,
  licenseId: 'lic_check_test',
  licensee: 'Example Org',
  type: 'pro',
  seats: 100,
  startDate: at(NOW, -DAY_MS).toISOString(),
  endDate: END.toISOString(),
  issuedAt: at(NOW, -DAY_MS).toISOString(),
  url: ADDRESS,
  ...overrides,
});

/** A configuration store in memory that keeps values as JSON text, like the real one. */
class MemoryStore implements LicenseStore {
  data = new Map<string, string>();
  writes: string[] = [];
  async read(key: string): Promise<unknown> {
    const value = this.data.get(key);
    return value === undefined ? null : JSON.parse(value);
  }
  async write(key: string, value: unknown): Promise<void> {
    this.writes.push(key);
    this.data.set(key, JSON.stringify(value));
  }
  answer(): unknown {
    return this.data.has(PERMISSION_SYNC_LICENCE_KEY)
      ? JSON.parse(this.data.get(PERMISSION_SYNC_LICENCE_KEY)!)
      : undefined;
  }
}

const reasonOf = async (run: () => Promise<unknown>): Promise<string> => {
  try {
    await run();
  } catch (error) {
    if (error instanceof LicenseError) return error.reason;
    return (error as Error).constructor.name;
  }
  return 'accepted';
};

// Four parameters, or Express does not treat it as the error handler.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const sendError: express.ErrorRequestHandler = (err, _req, res, _next) => {
  res
    .status(err instanceof BaseError ? err.statusCode : 500)
    .json({ code: (err as BaseError).code, error: (err as Error).message });
};

describe('license check: features, install and the permission sync answer', () => {
  // Keys are made for this run: no real key is ever in a test.
  let privateKey: KeyObject;
  let restoreKeys: () => void;
  let previousAddress: string | undefined;
  let store: MemoryStore;

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

  const publishAt = (when: Date) => publishPermissionSync(store, () => when);

  before(() => {
    const pair = generateKeyPairSync('ed25519');
    privateKey = pair.privateKey;
    restoreKeys = useLicenseKeysForTesting([
      pair.publicKey.export({ format: 'pem', type: 'spki' }),
    ]);
    previousAddress = process.env[PUBLIC_URL_ENV];
    process.env[PUBLIC_URL_ENV] = ADDRESS;
  });

  after(() => {
    restoreKeys();
    if (previousAddress === undefined) delete process.env[PUBLIC_URL_ENV];
    else process.env[PUBLIC_URL_ENV] = previousAddress;
  });

  beforeEach(() => {
    store = new MemoryStore();
  });

  describe('features (K2)', () => {
    it('keeps a features list under the signature', () => {
      const verified = checkLicense(
        signed(payload({ features: ['permissionSync', 'laterFeature'] })),
        NOW,
      );
      expect(verified.payload.features).to.deep.equal([
        'permissionSync',
        'laterFeature',
      ]);
      const file = signed(payload({ features: ['laterFeature'] }));
      const widened = {
        ...file,
        license: { ...file.license, features: ['permissionSync'] },
      };
      expect(() => checkLicense(widened, NOW)).to.throw(LicenseError);
    });

    it('still accepts a license issued before the field existed', () => {
      expect(checkLicense(signed(payload()), NOW).payload.features).to.equal(
        undefined,
      );
    });

    it('refuses a features value that is not a list of names', () => {
      for (const features of ['permissionSync', [''], ['two words'], [1]]) {
        const file = signed(payload({ features: features as never }));
        expect(() => checkLicense(file, NOW)).to.throw(LicenseError);
      }
    });

    it('grants permission sync to a license without a list, and otherwise only when named', () => {
      expect(licenseGrants({}, PERMISSION_SYNC_FEATURE)).to.equal(true);
      expect(licenseGrants({}, 'laterFeature')).to.equal(false);
      expect(licenseGrants({ features: [] }, PERMISSION_SYNC_FEATURE)).to.equal(
        false,
      );
      expect(
        licenseGrants(
          { features: ['permissionSync'] },
          PERMISSION_SYNC_FEATURE,
        ),
      ).to.equal(true);
    });
  });

  describe('the permission sync answer (C1, C3)', () => {
    it("writes nothing while no key was ever installed, so the connectors keep today's behaviour", async () => {
      expect(await publishAt(NOW)).to.equal(null);
      expect(store.writes).to.deep.equal([]);
      const status = await licenseStatus(store, NOW);
      expect(status).to.deep.include({
        status: 'none',
        license: null,
        permissionSync: null,
      });
    });

    it('writes nothing for a key that has not started yet', async () => {
      await installLicense(
        store,
        signed(
          payload({
            startDate: at(NOW, DAY_MS).toISOString(),
            endDate: at(NOW, 40 * DAY_MS).toISOString(),
          }),
        ),
        NOW,
      );
      expect(store.answer()).to.equal(undefined);
      expect((await licenseStatus(store, NOW)).status).to.equal('pending');
      // Once it starts, it decides.
      expect(await publishAt(at(NOW, DAY_MS))).to.deep.include({
        allowed: true,
      });
    });

    it('allows sync with a valid key, through grace, and stops at the end of grace', async () => {
      const result = await installLicense(store, signed(payload()), NOW);
      expect(result).to.deep.include({
        installed: true,
        alreadyInstalled: false,
      });
      const allowed = {
        allowed: true,
        until: GRACE_END.toISOString(),
      };
      // Exactly the fields the connectors read, and nothing secret.
      expect(store.answer()).to.deep.equal({ ...allowed, status: 'active' });

      expect(await publishAt(at(END, -1))).to.deep.equal({
        ...allowed,
        status: 'expiring',
      });
      expect(await publishAt(END)).to.deep.equal({
        ...allowed,
        status: 'grace',
      });
      const inGrace = await licenseStatus(store, at(END, DAY_MS));
      expect(inGrace.status).to.equal('grace');
      expect(inGrace.license?.graceEndsAt).to.equal(GRACE_END.toISOString());
      expect(await publishAt(at(GRACE_END, -1))).to.deep.equal({
        ...allowed,
        status: 'grace',
      });

      const lapsed = { allowed: false, until: null, status: 'expired' };
      expect(await publishAt(GRACE_END)).to.deep.equal(lapsed);
      expect(store.answer()).to.deep.equal(lapsed);
      expect(
        (await licenseStatus(store, GRACE_END)).license?.licenseId,
      ).to.equal('lic_check_test');
    });

    it('allows sync again when a new key is entered after a lapse', async () => {
      await installLicense(store, signed(payload()), NOW);
      const lapse = at(GRACE_END, DAY_MS);
      expect(await publishAt(lapse)).to.deep.include({ allowed: false });
      const renewal = payload({
        licenseId: 'lic_check_renewal',
        startDate: lapse.toISOString(),
        endDate: at(lapse, 365 * DAY_MS).toISOString(),
        issuedAt: lapse.toISOString(),
      });
      await installLicense(store, signed(renewal), lapse);
      expect(store.answer()).to.deep.include({
        allowed: true,
        status: 'active',
      });
    });

    it('does not allow sync under a license whose features leave it out', async () => {
      await installLicense(store, signed(payload({ features: [] })), NOW);
      expect(store.answer()).to.deep.equal({
        allowed: false,
        until: null,
        status: 'active',
      });
    });

    it('stops allowing sync when the installed file no longer verifies here', async () => {
      await installLicense(store, signed(payload()), NOW);
      const restore = useLicenseKeysForTesting([
        generateKeyPairSync('ed25519').publicKey.export({
          format: 'pem',
          type: 'spki',
        }),
      ]);
      try {
        expect(await publishAt(NOW)).to.deep.include({ allowed: false });
        expect((await licenseStatus(store, NOW)).ignored).to.equal(1);
      } finally {
        restore();
      }
    });

    it('writes the answer only when it changes', async () => {
      await installLicense(store, signed(payload()), NOW);
      const before = store.writes.length;
      await publishAt(at(NOW, 1000));
      await publishAt(at(NOW, 2000));
      expect(store.writes.length).to.equal(before);
    });
  });

  describe('installing a key', () => {
    it('reports a file installed twice as already installed', async () => {
      const file = signed(payload());
      await installLicense(store, file, NOW);
      expect(await installLicense(store, file, NOW)).to.deep.include({
        installed: false,
        alreadyInstalled: true,
      });
    });

    it('replaces the license in force with a newer one', async () => {
      await installLicense(store, signed(payload({ seats: 10 })), NOW);
      await installLicense(
        store,
        signed(
          payload({
            licenseId: 'lic_check_upgrade',
            seats: 50,
            issuedAt: NOW.toISOString(),
          }),
        ),
        NOW,
      );
      const status = await licenseStatus(store, NOW);
      expect(status.license).to.deep.include({
        licenseId: 'lic_check_upgrade',
        seats: 50,
      });
    });

    it('refuses a bad file, a different file under an installed ID, an ended one and one for another address', async () => {
      await installLicense(store, signed(payload()), NOW);
      expect(
        await reasonOf(() => installLicense(store, { nope: 1 }, NOW)),
      ).to.equal('MALFORMED');
      expect(
        await reasonOf(() =>
          installLicense(store, signed(payload({ seats: 7 })), NOW),
        ),
      ).to.equal('ConflictError');
      expect(
        await reasonOf(() =>
          installLicense(
            store,
            signed(
              payload({
                licenseId: 'lic_check_ended',
                endDate: at(NOW, -1).toISOString(),
                startDate: at(NOW, -2 * DAY_MS).toISOString(),
                issuedAt: at(NOW, -2 * DAY_MS).toISOString(),
              }),
            ),
            NOW,
          ),
        ),
      ).to.equal('EXPIRED');
      expect(
        await reasonOf(() =>
          installLicense(
            store,
            signed(
              payload({ licenseId: 'lic_other', url: 'https://other.test' }),
            ),
            NOW,
          ),
        ),
      ).to.equal('WRONG_ADDRESS');
      const tampered = signed(payload({ licenseId: 'lic_tampered' }));
      tampered.license = { ...tampered.license, seats: 5000 };
      expect(
        await reasonOf(() => installLicense(store, tampered, NOW)),
      ).to.equal('BAD_SIGNATURE');
      // Nothing refused was stored.
      const files = (await store.read(LICENSE_FILES_KEY)) as {
        licenses: SignedLicense[];
      };
      expect(
        files.licenses.map((file) => file.license.licenseId),
      ).to.deep.equal(['lic_check_test']);
    });
  });

  describe('the stored values', () => {
    it('are encrypted the way the Python store reads every /services key', async () => {
      const kv = new Map<string, string>();
      const encryption = EncryptionService.getInstance(
        'aes-256-gcm',
        'a'.repeat(64),
      );
      const encrypted = encryptedLicenseStore(
        {
          get: async (key: string) => kv.get(key) ?? null,
          set: async (key: string, value: string) => {
            kv.set(key, value);
          },
        } as never,
        encryption,
      );
      await installLicense(encrypted, signed(payload()), NOW);
      const raw = kv.get(PERMISSION_SYNC_LICENCE_KEY)!;
      expect(raw).to.not.include('allowed');
      expect(JSON.parse(encryption.decrypt(raw))).to.deep.include({
        allowed: true,
      });
      expect(await encrypted.read('/services/licence/missing')).to.equal(null);
    });
  });

  describe('the upgrade grace (N1, N2)', () => {
    const GRACE_END = at(NOW, UPGRADE_GRACE_DAYS * DAY_MS);
    const enforcedAt = (when: Date) =>
      publishPermissionSync(store, () => when, true);
    const upgrade = () => store.read(UPGRADE_KEY) as Promise<UpgradeRecord>;
    // Checked at NOW unless said otherwise: a report of this boot.
    const reportAffected = (affected: boolean, checkedAt: Date | null = NOW) =>
      store.write(INSTALL_AFFECTED_KEY, {
        affected,
        teamConnectors: 1,
        ...(checkedAt ? { checkedAt: checkedAt.toISOString() } : {}),
      });

    it('is off in this build: an install without a key writes nothing, not even the upgrade time', async () => {
      expect(LICENSE_ENFORCED).to.equal(false);
      await reportAffected(true);
      expect(await publishAt(NOW)).to.equal(null);
      expect(store.answer()).to.equal(undefined);
      expect(await store.read(UPGRADE_KEY)).to.equal(null);
      expect((await licenseStatus(store, NOW)).upgradeGrace).to.equal(null);
    });

    it('when on, keeps an install not known to be unaffected licensed for 21 days from its first start', async () => {
      expect(await enforcedAt(NOW)).to.deep.equal({
        allowed: true,
        until: GRACE_END.toISOString(),
        status: 'none',
      });
      expect(await upgrade()).to.deep.equal({
        upgradedAt: NOW.toISOString(),
        affected: null,
      });
      // A later start keeps the first upgrade time.
      expect(await enforcedAt(at(GRACE_END, -1))).to.deep.include({
        allowed: true,
      });
      expect((await upgrade()).upgradedAt).to.equal(NOW.toISOString());
      const status = await licenseStatus(store, at(NOW, DAY_MS));
      expect(status.upgradeGrace).to.deep.equal({
        endsAt: GRACE_END.toISOString(),
        affected: null,
      });
      expect(await enforcedAt(GRACE_END)).to.deep.equal({
        allowed: false,
        until: null,
        status: 'none',
      });
    });

    it('when on, gives no grace to an install the connectors report as unaffected', async () => {
      await reportAffected(false);
      expect(await enforcedAt(NOW)).to.deep.equal({
        allowed: false,
        until: null,
        status: 'none',
      });
      expect((await licenseStatus(store, NOW)).upgradeGrace).to.deep.equal({
        endsAt: null,
        affected: false,
      });
    });

    it('when on, takes "unaffected" only from a report of this boot, never one left from before', async () => {
      // Left by an earlier boot: before connectors were added, or before a count that then failed.
      for (const stale of [at(NOW, -DAY_MS), null]) {
        await reportAffected(false, stale);
        expect(await enforcedAt(NOW)).to.deep.include({ allowed: true });
        expect((await upgrade()).affected).to.equal(null);
      }
      // Written just before this service's first check (the connectors finished first): this boot's.
      await reportAffected(false, at(NOW, -AFFECTED_REPORT_SKEW_MS));
      expect(await enforcedAt(at(NOW, 1000))).to.deep.include({
        allowed: false,
      });
      expect((await upgrade()).affected).to.equal(false);
    });

    it('when on, takes "affected" from any report: it only keeps the grace', async () => {
      await reportAffected(true, at(NOW, -DAY_MS));
      await enforcedAt(NOW);
      expect((await upgrade()).affected).to.equal(true);
    });

    it('when on, keeps the first report: paid features added after the upgrade earn no grace', async () => {
      await enforcedAt(NOW);
      await reportAffected(false);
      await enforcedAt(at(NOW, 1000));
      expect((await upgrade()).affected).to.equal(false);
      await reportAffected(true);
      expect(await enforcedAt(at(NOW, 2000))).to.deep.include({
        allowed: false,
      });
      expect((await upgrade()).affected).to.equal(false);
    });

    it('when on, lets a key running past the grace end carry access to its own end, then gives no new grace', async () => {
      await reportAffected(true);
      await enforcedAt(NOW);
      await installLicense(store, signed(payload()), NOW);
      // The key's grace ends after the upgrade grace: the later date wins.
      expect(await enforcedAt(NOW)).to.deep.equal({
        allowed: true,
        until: at(END, DEFAULT_GRACE_DAYS * DAY_MS).toISOString(),
        status: 'active',
      });
      const lapsed = await enforcedAt(
        at(END, (DEFAULT_GRACE_DAYS + 1) * DAY_MS),
      );
      expect(lapsed).to.deep.equal({
        allowed: false,
        until: null,
        status: 'expired',
      });
    });

    const trial = (startOffsetMs: number) =>
      signed(
        payload({
          licenseId: `lic_trial_${String(startOffsetMs)}`,
          type: 'trial',
          startDate: at(NOW, startOffsetMs).toISOString(),
          issuedAt: at(NOW, startOffsetMs).toISOString(),
          endDate: at(NOW, startOffsetMs + 14 * DAY_MS).toISOString(),
        }),
      );

    it('when on, keeps the upgrade grace when a trial installed early in it ends first', async () => {
      await reportAffected(true);
      await enforcedAt(NOW);
      await installLicense(store, trial(DAY_MS), at(NOW, DAY_MS));
      // The trial (14 days and its grace) ends before the 21-day upgrade grace: the grace end wins.
      expect(await enforcedAt(at(NOW, DAY_MS))).to.deep.equal({
        allowed: true,
        until: GRACE_END.toISOString(),
        status: 'active',
      });
      // The trial has lapsed inside the upgrade grace: still allowed until the grace end.
      const afterTrial = at(NOW, (1 + 14 + DEFAULT_GRACE_DAYS + 1) * DAY_MS);
      expect(afterTrial.getTime()).to.be.lessThan(GRACE_END.getTime());
      expect(await enforcedAt(afterTrial)).to.deep.equal({
        allowed: true,
        until: GRACE_END.toISOString(),
        status: 'expired',
      });
      expect(await enforcedAt(GRACE_END)).to.deep.equal({
        allowed: false,
        until: null,
        status: 'expired',
      });
    });

    it('when on, lets a trial that runs past the grace end carry access to its own end', async () => {
      await reportAffected(true);
      await enforcedAt(NOW);
      const start = 15 * DAY_MS;
      await installLicense(store, trial(start), at(NOW, start));
      const trialGraceEnd = at(
        NOW,
        start + (14 + DEFAULT_GRACE_DAYS) * DAY_MS,
      ).toISOString();
      expect(await enforcedAt(at(NOW, start))).to.deep.include({
        allowed: true,
        until: trialGraceEnd,
      });
      // Past the upgrade grace, the running trial decides.
      expect(await enforcedAt(at(GRACE_END, DAY_MS))).to.deep.include({
        allowed: true,
        until: trialGraceEnd,
      });
    });

    it('when off, a lapsed trial is not allowed: there is no upgrade grace', async () => {
      await reportAffected(true);
      await installLicense(store, trial(0), NOW);
      expect(
        await publishAt(at(NOW, (14 + DEFAULT_GRACE_DAYS) * DAY_MS)),
      ).to.deep.equal({ allowed: false, until: null, status: 'expired' });
      expect(await store.read(UPGRADE_KEY)).to.equal(null);
    });

    it('when on, treats a key that has not started yet like no key', async () => {
      await reportAffected(true);
      await installLicense(
        store,
        signed(
          payload({
            startDate: at(NOW, 40 * DAY_MS).toISOString(),
            endDate: at(NOW, 80 * DAY_MS).toISOString(),
          }),
        ),
        NOW,
      );
      expect(await enforcedAt(NOW)).to.deep.equal({
        allowed: true,
        until: GRACE_END.toISOString(),
        status: 'pending',
      });
      expect(await enforcedAt(GRACE_END)).to.deep.include({ allowed: false });
      expect(await enforcedAt(at(NOW, 40 * DAY_MS))).to.deep.include({
        allowed: true,
        status: 'active',
      });
    });
  });

  describe('the development key', () => {
    const development = (
      overrides: Partial<LicensePayload> = {},
    ): LicensePayload => {
      const license = payload({
        licenseId: 'lic_dev',
        type: 'development',
        seats: DEVELOPMENT_SEATS,
        endDate: at(NOW, 100 * 365 * DAY_MS).toISOString(),
        ...overrides,
      });
      delete license.url;
      return license;
    };

    it('installs without a web address, allows permission sync and says it is not for production', async () => {
      await installLicense(store, signed(development()), NOW);
      expect(store.answer()).to.deep.include({
        allowed: true,
        status: 'active',
      });
      const status = await licenseStatus(store, NOW);
      expect(status.license).to.deep.include({
        type: 'development',
        seats: DEVELOPMENT_SEATS,
        url: null,
        notForProduction: true,
        features: ['permissionSync'],
      });
    });

    it('holds at most the development seat count', () => {
      for (const seats of [DEVELOPMENT_SEATS + 1, 'unlimited' as const]) {
        expect(() =>
          checkLicense(signed(development({ seats })), NOW),
        ).to.throw(LicenseError, /development license holds at most/);
      }
    });

    it('leaves pro and trial licenses marked for production', async () => {
      await installLicense(store, signed(payload()), NOW);
      expect(
        (await licenseStatus(store, NOW)).license?.notForProduction,
      ).to.equal(false);
    });
  });

  describe('routes', () => {
    let caller: Record<string, unknown>;

    const serve = async <T>(fn: (url: string) => Promise<T>): Promise<T> => {
      const app = express();
      app.use(express.json());
      // As app.ts mounts it: the signed file must get past body sanitizing intact.
      app.use(xssSanitizationMiddleware);
      const container = new Container();
      container.bind('AuthMiddleware').toConstantValue({
        authenticate(req: any, _res: unknown, next: () => void) {
          req.user = caller;
          next();
        },
      } as never);
      app.use('/api/v1/license', createLicenseRouter(container, store));
      app.use(sendError);
      const server = app.listen(0);
      try {
        const { port } = server.address() as AddressInfo;
        return await fn(`http://127.0.0.1:${port}/api/v1/license`);
      } finally {
        server.close();
      }
    };
    const post = (url: string, body: unknown) =>
      fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });

    beforeEach(() => {
      caller = { userId: 'admin', orgId: 'org' };
      sinon
        .stub(userAdminService, 'isUserOrgAdmin')
        .callsFake(async (userId: string) => userId === 'admin');
    });

    afterEach(() => sinon.restore());

    it('installs a key and reads the status back without the signature', async () => {
      // The route runs on the real clock: a file that starts now and runs a year.
      const now = new Date();
      const file = signed(
        payload({
          startDate: now.toISOString(),
          issuedAt: now.toISOString(),
          endDate: at(now, 365 * DAY_MS).toISOString(),
        }),
      );
      await serve(async (url) => {
        const installed = await post(url, { license: file });
        expect(installed.status).to.equal(200);
        expect(await installed.json()).to.deep.include({ installed: true });
        const read = await fetch(url);
        const text = await read.text();
        expect(text).to.not.include(file.signature);
        expect(JSON.parse(text)).to.deep.include({ status: 'active' });
        expect(JSON.parse(text).permissionSync).to.deep.include({
          allowed: true,
        });
      });
    });

    it('installs a file whose text body sanitizing would rewrite, and reads back only what was signed', async () => {
      // `/`, `&`, quotes and `>` are what the XSS middleware encodes; the signature covers the exact text.
      const now = new Date();
      const licensee = `Smith & O'Brien "Labs" > R&D`;
      const file = signed(
        payload({
          licensee,
          startDate: now.toISOString(),
          issuedAt: now.toISOString(),
          endDate: at(now, 365 * DAY_MS).toISOString(),
        }),
      );
      await serve(async (url) => {
        const installed = await post(url, { license: file });
        expect(installed.status).to.equal(200);
        const status = (await (await fetch(url)).json()) as {
          license: Record<string, unknown>;
        };
        expect(status.license).to.deep.include({ licensee, url: ADDRESS });
        // Exactly the view's fields, all from the verified file: nothing else from the request is echoed.
        expect(Object.keys(status.license).sort()).to.deep.equal(
          [
            'endDate',
            'features',
            'graceEndsAt',
            'licenseId',
            'licensee',
            'notForProduction',
            'seats',
            'startDate',
            'type',
            'url',
          ].sort(),
        );
      });
    });

    it('answers a refused file with its reason', async () => {
      await serve(async (url) => {
        const response = await post(url, { license: { nope: 1 } });
        expect(response.status).to.equal(400);
        expect(await response.json()).to.deep.include({
          code: 'HTTP_LICENSE_MALFORMED',
        });
      });
    });

    it('refuses anyone who is not an admin', async () => {
      caller = { userId: 'member', orgId: 'org' };
      await serve(async (url) => {
        expect((await fetch(url)).status).to.equal(403);
        expect(
          (await post(url, { license: signed(payload()) })).status,
        ).to.equal(403);
      });
      expect(store.writes).to.deep.equal([]);
    });
  });
});

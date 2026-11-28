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
import { createLicenseRouter } from '../../../../src/ee/modules/license/license-boot';
import { licenseSigningInput } from '../../../../src/ee/modules/license/license-codec';
import { useLicenseKeysForTesting } from '../../../../src/ee/modules/license/license-keys';
import {
  LICENSE_CONNECTORS_KEY,
  licenseNotice,
  startupWarnings,
  type NoticeViewer,
} from '../../../../src/ee/modules/license/license-notice';
import { UPGRADE_GRACE_DAYS } from '../../../../src/ee/modules/license/license-policy';
import {
  LICENSE_FILES_KEY,
  licenseStatus,
  UPGRADE_KEY,
  type LicenseStore,
  type UpgradeRecord,
} from '../../../../src/ee/modules/license/license.service';
import {
  LICENSE_FORMAT,
  type LicensePayload,
  type SignedLicense,
} from '../../../../src/ee/modules/license/license.types';
import { setIdentityPort } from '../../../../src/libs/repositories/identity';
import {
  setUserRepository,
  type UserRepository,
} from '../../../../src/libs/repositories/user';
import * as userAdminService from '../../../../src/modules/user_management/services/user-admin.service';
import { operatorKeyGuardMiddleware } from '../../../../src/libs/middlewares/operator-key-guard.middleware';
import { xssSanitizationMiddleware } from '../../../../src/libs/middlewares/xss-sanitization.middleware';

const DAY_MS = 24 * 60 * 60 * 1000;
const NOW = new Date('2026-11-20T12:00:00.000Z');
const at = (offsetDays: number): string =>
  new Date(NOW.getTime() + offsetDays * DAY_MS).toISOString();

class MemoryStore implements LicenseStore {
  data = new Map<string, unknown>();
  async read(key: string): Promise<unknown> {
    return this.data.has(key) ? structuredClone(this.data.get(key)) : null;
  }
  async write(key: string, value: unknown): Promise<void> {
    this.data.set(key, structuredClone(value));
  }
}

const ADMIN: NoticeViewer = { userId: 'admin', orgId: 'org', isAdmin: true };
const MEMBER: NoticeViewer = { userId: 'member', orgId: 'org', isAdmin: false };

/** As the Python connectors write it. */
const connector = (
  id: string,
  over: Record<string, unknown> = {},
): Record<string, unknown> => ({
  id,
  orgId: 'org',
  name: `Drive ${id}`,
  type: 'DRIVE WORKSPACE',
  createdBy: 'admin',
  accessLimit: null,
  status: 'IDLE',
  ...over,
});

describe('license notice: what every signed-in user may read', () => {
  let privateKey: KeyObject;
  let restoreKeys: () => void;
  let store: MemoryStore;

  const install = (over: Partial<LicensePayload>) => {
    const license: LicensePayload = {
      version: 1,
      licenseId: 'lic_notice',
      licensee: 'Example Org',
      type: 'trial',
      seats: 10,
      startDate: at(-1),
      endDate: at(30),
      issuedAt: at(-1),
      ...over,
    };
    const file: SignedLicense = {
      format: LICENSE_FORMAT,
      keyId: 'test-key',
      license,
      signature: sign(null, licenseSigningInput(license), privateKey).toString(
        'base64url',
      ),
    };
    store.data.set(LICENSE_FILES_KEY, { licenses: [file] });
  };
  const upgradedDaysAgo = (days: number, affected: boolean | null = true) =>
    store.data.set(UPGRADE_KEY, {
      upgradedAt: at(-days),
      affected,
    } satisfies UpgradeRecord);
  const connectors = (...list: Record<string, unknown>[]) =>
    store.data.set(LICENSE_CONNECTORS_KEY, { connectors: list });

  before(() => {
    const pair = generateKeyPairSync('ed25519');
    privateKey = pair.privateKey;
    restoreKeys = useLicenseKeysForTesting([
      pair.publicKey.export({ format: 'pem', type: 'spki' }),
    ]);
  });
  after(() => restoreKeys());
  beforeEach(() => {
    store = new MemoryStore();
  });

  describe('enforcement off, no key (the release today)', () => {
    it('says nothing: permission sync on, no grace, no connectors', async () => {
      connectors(connector('a'));
      const notice = await licenseNotice(store, ADMIN, NOW);
      expect(notice).to.deep.equal({
        upgradeGrace: null,
        permissionSync: true,
        notForProduction: false,
        connectors: { limited: 0, restoring: 0, items: [] },
      });
    });
  });

  describe('enforcement on, no key', () => {
    it('during the grace: its end, and the connectors it will limit, to admins only', async () => {
      upgradedDaysAgo(UPGRADE_GRACE_DAYS - 5);
      connectors(connector('a'));
      const admin = await licenseNotice(store, ADMIN, NOW);
      expect(admin.upgradeGrace).to.deep.equal({
        endsAt: at(5),
        key: 'missing',
        keyDate: null,
      });
      expect(admin.permissionSync).to.equal(true);
      expect(admin.connectors.items).to.deep.equal([
        {
          id: 'a',
          name: 'Drive a',
          type: 'DRIVE WORKSPACE',
          state: 'affected',
          status: 'IDLE',
        },
      ]);
      const member = await licenseNotice(store, MEMBER, NOW);
      expect(member.connectors).to.deep.equal({
        limited: 0,
        restoring: 0,
        items: null,
      });
    });

    it('keeps the grace running when a trial ends inside it', async () => {
      upgradedDaysAgo(5);
      install({ startDate: at(-30), issuedAt: at(-30), endDate: at(-10) });
      connectors(connector('a'));
      const admin = await licenseNotice(store, ADMIN, NOW);
      expect(admin.upgradeGrace).to.deep.equal({
        endsAt: at(UPGRADE_GRACE_DAYS - 5),
        key: 'ended',
        keyDate: at(-10),
      });
      expect(admin.permissionSync).to.equal(true);
      expect(admin.connectors.items?.map((item) => item.state)).to.deep.equal([
        'affected',
      ]);
    });

    it('gives no grace to an install known not to need it', async () => {
      upgradedDaysAgo(1, false);
      expect((await licenseNotice(store, ADMIN, NOW)).upgradeGrace).to.equal(
        null,
      );
    });

    it('after the grace: limited connectors; a member gets a count, never a name', async () => {
      upgradedDaysAgo(UPGRADE_GRACE_DAYS + 1);
      connectors(
        connector('a', { accessLimit: 'CREATOR' }),
        connector('b', { accessLimit: 'CREATOR', createdBy: 'member' }),
        connector('c', { accessLimit: 'CREATOR', orgId: 'other-org' }),
      );
      const admin = await licenseNotice(store, ADMIN, NOW);
      expect(admin.upgradeGrace).to.equal(null);
      expect(admin.permissionSync).to.equal(false);
      expect(admin.connectors.limited).to.equal(2);
      expect(admin.connectors.items?.map((c) => [c.id, c.state])).to.deep.equal(
        [
          ['a', 'limited'],
          ['b', 'limited'],
        ],
      );
      const member = await licenseNotice(store, MEMBER, NOW);
      // Their own connector still shows them its content.
      expect(member.connectors).to.deep.equal({
        limited: 1,
        restoring: 0,
        items: null,
      });
      expect(JSON.stringify(member)).not.to.contain('Drive');
    });
  });

  describe('with a key', () => {
    it('a limited connector is restoring once a key allows permission sync', async () => {
      upgradedDaysAgo(UPGRADE_GRACE_DAYS + 1);
      install({ type: 'trial' });
      connectors(
        connector('a', { accessLimit: 'CREATOR', status: 'FULL_SYNCING' }),
        connector('b'),
      );
      const admin = await licenseNotice(store, ADMIN, NOW);
      expect(admin.permissionSync).to.equal(true);
      expect(admin.upgradeGrace).to.equal(null);
      expect(admin.connectors.items).to.deep.equal([
        {
          id: 'a',
          name: 'Drive a',
          type: 'DRIVE WORKSPACE',
          state: 'restoring',
          status: 'FULL_SYNCING',
        },
      ]);
      expect(
        (await licenseNotice(store, MEMBER, NOW)).connectors,
      ).to.deep.equal({ limited: 0, restoring: 1, items: null });
    });

    it('a development key is not for production, with enforcement on or off', async () => {
      install({ type: 'development' });
      expect(
        (await licenseNotice(store, MEMBER, NOW)).notForProduction,
      ).to.equal(true);
      upgradedDaysAgo(1);
      expect(
        (await licenseNotice(store, MEMBER, NOW)).notForProduction,
      ).to.equal(true);
    });

    it('an ended development key no longer says so', async () => {
      install({
        type: 'development',
        startDate: at(-60),
        issuedAt: at(-60),
        endDate: at(-30),
      });
      expect(
        (await licenseNotice(store, MEMBER, NOW)).notForProduction,
      ).to.equal(false);
    });

    it('never carries the license file, its signature or the licensee', async () => {
      install({ type: 'trial' });
      const text = JSON.stringify(await licenseNotice(store, ADMIN, NOW));
      expect(text).not.to.contain('signature');
      expect(text).not.to.contain('Example Org');
      expect(text).not.to.contain('lic_notice');
    });
  });

  describe('what keeps the grace in force', () => {
    it('a key dated ahead: the grace runs and the key starts later; no "no key" anywhere', async () => {
      upgradedDaysAgo(5);
      install({ startDate: at(3), issuedAt: at(-1), endDate: at(60) });
      const view = await licenseStatus(store, NOW);
      expect(view.status).to.equal('pending');
      expect(
        (await licenseNotice(store, ADMIN, NOW)).upgradeGrace,
      ).to.deep.equal({
        endsAt: at(UPGRADE_GRACE_DAYS - 5),
        key: 'startsLater',
        keyDate: at(3),
      });
      const [warning] = startupWarnings(view, NOW);
      expect(warning).to.contain(`starts at ${at(3)}`);
      expect(warning).to.contain(at(UPGRADE_GRACE_DAYS - 5));
      expect(warning).not.to.contain('No license key is installed');
    });

    it('a key dated ahead: connectors are marked affected only if the grace ends before it starts', async () => {
      upgradedDaysAgo(5);
      connectors(connector('a'));
      install({ startDate: at(3), issuedAt: at(-1), endDate: at(60) });
      expect((await licenseNotice(store, ADMIN, NOW)).connectors.items).to.deep.equal([]);
      install({ startDate: at(UPGRADE_GRACE_DAYS), issuedAt: at(-1), endDate: at(60) });
      const [item] = (await licenseNotice(store, ADMIN, NOW)).connectors.items ?? [];
      expect(item?.state).to.equal('affected');
      install({ startDate: at(3), issuedAt: at(-1), endDate: at(60), features: [] });
      const [noSync] = (await licenseNotice(store, ADMIN, NOW)).connectors.items ?? [];
      expect(noSync?.state).to.equal('affected');
    });

    it('a trial that ended inside the grace: the warning says the key ended', async () => {
      upgradedDaysAgo(5);
      install({ startDate: at(-30), issuedAt: at(-30), endDate: at(-10) });
      const [warning] = startupWarnings(await licenseStatus(store, NOW), NOW);
      expect(warning).to.contain(`ended at ${at(-10)}`);
      expect(warning).to.contain(at(UPGRADE_GRACE_DAYS - 5));
      expect(warning).not.to.contain('No license key is installed');
    });
  });

  describe('startup warnings', () => {
    it('warns of a development key and of the upgrade grace, and of nothing without them', async () => {
      expect(
        startupWarnings(await licenseStatus(store, NOW), NOW),
      ).to.deep.equal([]);
      upgradedDaysAgo(1);
      const grace = startupWarnings(await licenseStatus(store, NOW), NOW);
      expect(grace).to.have.length(1);
      expect(grace[0]).to.contain(at(UPGRADE_GRACE_DAYS - 1));
      expect(grace[0]).to.contain('No license key is installed');
      install({ type: 'development' });
      expect(
        startupWarnings(await licenseStatus(store, NOW), NOW),
      ).to.deep.equal([
        'A development license key is in force: this installation is not for production use.',
      ]);
    });
  });

  describe('GET /api/v1/license/notice', () => {
    let caller: Record<string, unknown>;
    const serve = async <T>(fn: (url: string) => Promise<T>): Promise<T> => {
      const app = express();
      const container = new Container();
      container.bind('AuthMiddleware').toConstantValue({
        authenticate(req: any, _res: unknown, next: () => void) {
          req.user = caller;
          next();
        },
      } as never);
      app.use('/api/v1/license', createLicenseRouter(container, store));
      // Four parameters, or Express does not treat it as the error handler; answers the status without logging.
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      app.use(((err, _req, res, _next) => {
        res.status((err as { statusCode?: number }).statusCode ?? 500).end();
      }) as express.ErrorRequestHandler);
      const server = app.listen(0);
      try {
        const { port } = server.address() as AddressInfo;
        return await fn(`http://127.0.0.1:${port}/api/v1/license`);
      } finally {
        server.close();
      }
    };

    beforeEach(() => {
      sinon
        .stub(userAdminService, 'isUserOrgAdmin')
        .callsFake(async (userId: string) => userId === 'admin');
      upgradedDaysAgo(UPGRADE_GRACE_DAYS + 1);
      connectors(connector('a', { accessLimit: 'CREATOR' }));
    });
    afterEach(() => sinon.restore());

    it('answers a member with counts and no names, while the full status stays admin-only', async () => {
      caller = { userId: 'member', orgId: 'org' };
      await serve(async (url) => {
        const response = await fetch(`${url}/notice`);
        expect(response.status).to.equal(200);
        const body = await response.json();
        expect(body.connectors).to.deep.equal({
          limited: 1,
          restoring: 0,
          items: null,
        });
        expect((await fetch(url)).status).to.equal(403);
      });
    });

    it('answers an admin with the names', async () => {
      caller = { userId: 'admin', orgId: 'org' };
      await serve(async (url) => {
        const body = await (await fetch(`${url}/notice`)).json();
        expect(body.connectors.items[0].name).to.equal('Drive a');
      });
    });

    describe('the seat limit, for the admins\' banner', () => {
      let people: number | Error;
      beforeEach(() => {
        people = 12;
        install({ seats: 10 });
        setIdentityPort({ oldestOrgId: async () => 'org' } as never);
        setUserRepository({
          countLive: async () => {
            if (people instanceof Error) throw people;
            return people;
          },
        } as unknown as UserRepository);
      });
      afterEach(() => {
        setIdentityPort();
        setUserRepository();
      });

      it('an admin gets the seats, the count and when new users start being refused', async () => {
        caller = { userId: 'admin', orgId: 'org' };
        await serve(async (url) => {
          const body = await (await fetch(`${url}/notice`)).json();
          expect(body.seatLimit).to.deep.include({
            seats: 10,
            used: 12,
            over: 2,
            refusing: false,
          });
          expect(body.seatLimit.refuseFrom).to.be.a('string');
        });
      });

      it('a member never gets it', async () => {
        caller = { userId: 'member', orgId: 'org' };
        await serve(async (url) => {
          const body = await (await fetch(`${url}/notice`)).json();
          expect(body.seatLimit).to.equal(null);
        });
      });

      it('a count that fails leaves it null and the other notices standing', async () => {
        people = new Error('count failed');
        caller = { userId: 'admin', orgId: 'org' };
        await serve(async (url) => {
          const response = await fetch(`${url}/notice`);
          expect(response.status).to.equal(200);
          const body = await response.json();
          expect(body.seatLimit).to.equal(null);
          expect(body.connectors.items[0].name).to.equal('Drive a');
        });
      });
    });
  });

  describe('GET /api/v1/license/grace (public, for the procurement site)', () => {
    let clock: sinon.SinonFakeTimers;
    /** Mounted as app.ts mounts it, behind the body sanitizer and the query guard, with no AuthMiddleware bound. */
    const graceOf = async (): Promise<{ status: number; body: unknown }> => {
      const app = express();
      app.use(express.json());
      app.use(xssSanitizationMiddleware);
      app.use(operatorKeyGuardMiddleware);
      app.use('/api/v1/license', createLicenseRouter(new Container(), store));
      const server = app.listen(0);
      try {
        const { port } = server.address() as AddressInfo;
        const response = await fetch(
          `http://127.0.0.1:${port}/api/v1/license/grace`,
        );
        return { status: response.status, body: await response.json() };
      } finally {
        server.close();
      }
    };

    // Only Date: the server and fetch keep real timers.
    beforeEach(() => {
      clock = sinon.useFakeTimers({ now: NOW, toFake: ['Date'] });
    });
    afterEach(() => clock.restore());

    it('in the grace with no key: its end, and nothing else, with no sign-in', async () => {
      upgradedDaysAgo(UPGRADE_GRACE_DAYS - 5);
      expect(await graceOf()).to.deep.equal({
        status: 200,
        body: { graceEndsAt: at(5) },
      });
    });

    it('a trial that ended inside the grace keeps it running', async () => {
      upgradedDaysAgo(5);
      install({ startDate: at(-30), issuedAt: at(-30), endDate: at(-10) });
      expect((await graceOf()).body).to.deep.equal({
        graceEndsAt: at(UPGRADE_GRACE_DAYS - 5),
      });
    });

    it('licensed: null, and no licence detail', async () => {
      upgradedDaysAgo(UPGRADE_GRACE_DAYS - 5);
      install({ type: 'trial' });
      expect(await graceOf()).to.deep.equal({
        status: 200,
        body: { graceEndsAt: null },
      });
    });

    it('before the grace (enforcement not started, or not needed): null', async () => {
      expect((await graceOf()).body).to.deep.equal({ graceEndsAt: null });
      upgradedDaysAgo(1, false);
      expect((await graceOf()).body).to.deep.equal({ graceEndsAt: null });
    });

    it('after the grace: null', async () => {
      upgradedDaysAgo(UPGRADE_GRACE_DAYS + 1);
      expect((await graceOf()).body).to.deep.equal({ graceEndsAt: null });
    });
  });
});

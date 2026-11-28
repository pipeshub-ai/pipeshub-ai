// Copyright (c) 2026 PipesHub Inc.
// Licensed under the PipesHub Enterprise License. See the LICENSE file in this
// ee folder. Free for development, testing and evaluation. Production use needs
// a PipesHub licence key.

import 'reflect-metadata';
import { expect } from 'chai';
import { generateKeyPairSync, sign, type KeyObject } from 'crypto';
import sinon from 'sinon';
import {
  encryptedClaims,
  encryptedLicenseStore,
} from '../../../../src/ee/modules/license/license-boot';
import { licenseSigningInput } from '../../../../src/ee/modules/license/license-codec';
import {
  dueReminder,
  GRACE_REMINDERS_KEY,
  reminderOutcome,
  resetGraceReminderLog,
  sendGraceReminder,
  type GraceReminderDeps,
  type ReminderOutcome,
} from '../../../../src/ee/modules/license/license-grace-reminders';
import { useLicenseKeysForTesting } from '../../../../src/ee/modules/license/license-keys';
import {
  PERMISSION_SYNC_FEATURE,
  UPGRADE_GRACE_DAYS,
} from '../../../../src/ee/modules/license/license-policy';
import {
  LICENSE_FILES_KEY,
  UPGRADE_KEY,
} from '../../../../src/ee/modules/license/license.service';
import {
  LICENSE_FORMAT,
  type LicensePayload,
} from '../../../../src/ee/modules/license/license.types';
import { getEmailContent } from '../../../../src/modules/mail/utils/email-content';

const DAY_MS = 24 * 60 * 60 * 1000;
const UPGRADED = new Date('2026-11-01T09:00:00.000Z');
const ENDS = new Date(UPGRADED.getTime() + UPGRADE_GRACE_DAYS * DAY_MS);
/** A moment with `days` whole days (rounded up) left in the grace. */
const left = (days: number): Date =>
  new Date(ENDS.getTime() - days * DAY_MS + 60_000);

/** The configuration store as the providers behave: create-if-absent is atomic. */
class MemoryKv {
  data = new Map<string, string>();
  async get<T>(key: string): Promise<T | null> {
    return (this.data.get(key) ?? null) as T | null;
  }
  async set(key: string, value: unknown): Promise<void> {
    this.data.set(key, value as string);
  }
  async compareAndSet<T>(
    key: string,
    expected: T | null,
    next: T,
  ): Promise<boolean> {
    if ((this.data.get(key) ?? null) !== expected) return false;
    this.data.set(key, next as string);
    return true;
  }
  async delete(key: string): Promise<void> {
    this.data.delete(key);
  }
}

const encryption = {
  encrypt: (text: string) => `enc:${text}`,
  decrypt: (text: string) => text.slice('enc:'.length),
};

interface Sent {
  orgId: string;
  to: string[];
  daysLeft: number;
  endsAt: string;
  key: string;
  keyDate: string | null;
}

describe('license grace reminders: admins mailed at 14, 7, 3 and 1 days', () => {
  let privateKey: KeyObject;
  let restoreKeys: () => void;
  let kv: MemoryKv;
  let sent: Sent[];

  /** One process (a replica, or the same one after a restart) on the shared store. */
  const replica = (
    over: Partial<GraceReminderDeps> = {},
  ): GraceReminderDeps & {
    log: { info: sinon.SinonSpy; warn: sinon.SinonSpy };
  } => ({
    store: encryptedLicenseStore(kv, encryption),
    ...encryptedClaims(kv, encryption),
    admins: async () => ({
      orgId: 'org',
      emails: ['admin@example.com', 'second@example.com'],
    }),
    mailConfigured: async () => true,
    send: async (mail) => {
      sent.push(mail);
      return 'sent';
    },
    ...over,
    log: { info: sinon.spy(), warn: sinon.spy() },
  });

  const install = async (over: Partial<LicensePayload>): Promise<void> => {
    const license: LicensePayload = {
      version: 1,
      licenseId: 'lic_reminder',
      licensee: 'Example Org',
      type: 'trial',
      seats: 10,
      startDate: UPGRADED.toISOString(),
      endDate: new Date(ENDS.getTime() + 60 * DAY_MS).toISOString(),
      issuedAt: UPGRADED.toISOString(),
      ...over,
    };
    await replica().store.write(LICENSE_FILES_KEY, {
      licenses: [
        {
          format: LICENSE_FORMAT,
          keyId: 'test-key',
          license,
          signature: sign(
            null,
            licenseSigningInput(license),
            privateKey,
          ).toString('base64url'),
        },
      ],
    });
  };

  before(() => {
    const pair = generateKeyPairSync('ed25519');
    privateKey = pair.privateKey;
    restoreKeys = useLicenseKeysForTesting([
      pair.publicKey.export({ format: 'pem', type: 'spki' }),
    ]);
  });
  after(() => restoreKeys());
  beforeEach(async () => {
    kv = new MemoryKv();
    sent = [];
    resetGraceReminderLog();
    await replica().store.write(UPGRADE_KEY, {
      upgradedAt: UPGRADED.toISOString(),
      affected: true,
    });
  });

  it('picks the nearest threshold reached', () => {
    expect([21, 15, 14, 8, 7, 4, 3, 2, 1].map(dueReminder)).to.deep.equal([
      null,
      null,
      14,
      14,
      7,
      7,
      3,
      3,
      1,
    ]);
  });

  it('sends each threshold once, to the admins, through the whole grace', async () => {
    const one = replica();
    const sentAt: number[] = [];
    // Every 6 hours from 21 days left to the end, with a second check at each moment.
    for (let t = UPGRADED.getTime(); t < ENDS.getTime(); t += DAY_MS / 4) {
      for (let check = 0; check < 2; check += 1) {
        const days = await sendGraceReminder(one, new Date(t));
        if (days !== null) sentAt.push(days);
      }
    }
    expect(sentAt).to.deep.equal([14, 7, 3, 1]);
    expect(sent.map((mail) => mail.daysLeft)).to.deep.equal([14, 7, 3, 1]);
    expect(sent[0]).to.deep.include({
      orgId: 'org',
      to: ['admin@example.com', 'second@example.com'],
      endsAt: ENDS.toISOString(),
    });
    for (const days of [14, 7, 3, 1]) {
      expect(kv.data.has(`${GRACE_REMINDERS_KEY}/${String(days)}`)).to.equal(
        true,
      );
    }
  });

  it('a threshold passed while down is not sent late once a nearer one is due', async () => {
    expect(await sendGraceReminder(replica(), left(5))).to.equal(7);
    expect(await sendGraceReminder(replica(), left(2))).to.equal(3);
    expect(sent.map((mail) => mail.daysLeft)).to.deep.equal([5, 2]);
  });

  it('sends nothing while a licence key is in force', async () => {
    await install({});
    for (const days of [14, 7, 3, 1]) {
      expect(await sendGraceReminder(replica(), left(days))).to.equal(null);
    }
    expect(sent).to.deep.equal([]);
  });

  it('sends nothing when a key dated ahead turns permission sync on before the grace ends', async () => {
    await install({
      startDate: new Date(ENDS.getTime() - DAY_MS).toISOString(),
      features: [PERMISSION_SYNC_FEATURE],
    });
    for (const days of [14, 7, 3, 1]) {
      expect(await sendGraceReminder(replica(), left(days))).to.equal(null);
    }
    expect(sent).to.deep.equal([]);
  });

  it('names a key dated past the grace, one without permission sync, and one that ended', async () => {
    const after = new Date(ENDS.getTime() + 5 * DAY_MS);
    const before = new Date(ENDS.getTime() - DAY_MS);
    const cases: Array<[Partial<LicensePayload>, string, string]> = [
      [{ startDate: after.toISOString() }, 'startsLater', after.toISOString()],
      [
        { startDate: before.toISOString(), features: [] },
        'startsLater',
        before.toISOString(),
      ],
      [
        { endDate: new Date(UPGRADED.getTime() + DAY_MS).toISOString() },
        'ended',
        new Date(UPGRADED.getTime() + DAY_MS).toISOString(),
      ],
    ];
    for (const [over, key, keyDate] of cases) {
      kv = new MemoryKv();
      sent = [];
      await replica().store.write(UPGRADE_KEY, {
        upgradedAt: UPGRADED.toISOString(),
        affected: true,
      });
      await install(over);
      expect(await sendGraceReminder(replica(), left(7))).to.equal(7);
      expect(sent[0], key).to.deep.include({ key, keyDate });
    }
  });

  it('sends nothing before the first threshold, after the grace, or with no grace', async () => {
    expect(await sendGraceReminder(replica(), left(15))).to.equal(null);
    expect(
      await sendGraceReminder(replica(), new Date(ENDS.getTime() + 60_000)),
    ).to.equal(null);
    await replica().store.write(UPGRADE_KEY, {
      upgradedAt: UPGRADED.toISOString(),
      affected: false,
    });
    expect(await sendGraceReminder(replica(), left(7))).to.equal(null);
    expect(sent).to.deep.equal([]);
  });

  it('two replicas at the same moment, and a restart after, send it once', async () => {
    const results = await Promise.all([
      sendGraceReminder(replica(), left(7)),
      sendGraceReminder(replica(), left(7)),
    ]);
    expect(results.filter((days) => days === 7)).to.have.length(1);
    resetGraceReminderLog(); // a restart forgets everything it held in memory
    expect(await sendGraceReminder(replica(), left(6))).to.equal(null);
    expect(sent).to.have.length(1);
  });

  it('with no mail set up: skips quietly, logs once at info, and sends once mail is set up', async () => {
    const noMail = replica({ mailConfigured: async () => false });
    expect(await sendGraceReminder(noMail, left(7))).to.equal(null);
    expect(await sendGraceReminder(noMail, left(7))).to.equal(null);
    expect(noMail.log.info.callCount).to.equal(1);
    expect(noMail.log.warn.callCount).to.equal(0);
    expect(sent).to.deep.equal([]);
    expect(await sendGraceReminder(replica(), left(6))).to.equal(7);
  });

  it('a send worth retrying gives the claim back; one that may have gone out keeps it', async () => {
    const outcome = (result: ReminderOutcome) =>
      replica({
        send: async (mail) => {
          sent.push(mail);
          return result;
        },
      });
    expect(await sendGraceReminder(outcome('retry'), left(7))).to.equal(null);
    expect(await sendGraceReminder(replica(), left(7))).to.equal(7);
    expect(await sendGraceReminder(outcome('failed'), left(3))).to.equal(null);
    expect(await sendGraceReminder(replica(), left(3))).to.equal(null);
    expect(sent.map((mail) => mail.daysLeft)).to.deep.equal([7, 7, 3]);
  });

  it('a mail result that fails every time keeps the claim; only one that may pass gives it back', () => {
    expect(reminderOutcome('sent')).to.equal('sent');
    expect(reminderOutcome('transient')).to.equal('retry');
    expect(reminderOutcome('unavailable')).to.equal('retry');
    // A refused login retried every check could lock the SMTP account.
    expect(reminderOutcome('permanent')).to.equal('failed');
    expect(reminderOutcome('indeterminate')).to.equal('failed');
  });

  it('a send that throws gives the claim back, so the next check sends it', async () => {
    const throwing = replica({
      send: async () => {
        throw new Error('stored mail settings could not be read');
      },
    });
    let error: unknown;
    try {
      await sendGraceReminder(throwing, left(7));
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(Error);
    expect(await sendGraceReminder(replica(), left(7))).to.equal(7);
    expect(sent.map((mail) => mail.daysLeft)).to.deep.equal([7]);
  });

  it('a store that cannot be read fails the reminder, not the caller of the check', async () => {
    const broken = replica();
    sinon.stub(broken.store, 'read').rejects(new Error('store down'));
    let error: unknown;
    try {
      await sendGraceReminder(broken, left(7));
    } catch (caught) {
      error = caught;
    }
    // license-boot's remindAdmins logs this at warn and carries on.
    expect(error).to.be.instanceOf(Error);
    expect(sent).to.deep.equal([]);
  });

  it('the mail names the days left, the end date and the licence page', () => {
    const html = getEmailContent('licenseGraceReminder', {
      daysLeft: 3,
      oneDay: false,
      endsOn: 'November 22, 2026',
      link: 'https://pipeshub.example/workspace/license/',
    });
    expect(html).to.contain('3 more days');
    expect(html).to.contain('November 22, 2026');
    expect(html).to.contain(
      'href="https://pipeshub.example/workspace/license/"',
    );
    expect(
      getEmailContent('licenseGraceReminder', {
        daysLeft: 1,
        oneDay: true,
        endsOn: 'x',
        link: 'y',
      }),
    ).to.contain('1 more day</strong>');
  });

  it('the mail says which key there is, and what is limited when the grace ends', () => {
    const base = {
      daysLeft: 3,
      oneDay: false,
      endsOn: 'November 22, 2026',
      link: 'y',
      keyOn: 'December 1, 2026',
    };
    const text = (data: Record<string, unknown>): string =>
      getEmailContent('licenseGraceReminder', { ...base, ...data }).replace(
        /\s+/g,
        ' ',
      );
    expect(text({ keyMissing: true })).to.contain('No license key is in force');
    const later = text({ keyStartsLater: true, keyStartsAfterGrace: true });
    expect(later).to.contain(
      'starts on <strong>December 1, 2026</strong>, after the upgrade grace ends',
    );
    expect(later).to.contain(
      'until a key that includes permission sync is in force',
    );
    expect(later).to.not.contain('No license key is in force');
    expect(
      text({ keyStartsLater: true, keyStartsAfterGrace: false }),
    ).to.contain('does not include permission sync');
    const ended = text({ keyEnded: true });
    expect(ended).to.contain(
      'ended on <strong>December 1, 2026</strong>; a new one is required',
    );
    expect(ended).to.not.contain('No license key is in force');
  });
});

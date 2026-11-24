import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  MAX_ALERTS_PER_TYPE_PER_HOUR,
  MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR,
  PlatformAlertHandler,
  logIfPlatformAlertsBlocked,
  platformAlertsBlockedByEnvelope,
} from '../../../../src/modules/notification/service/platform-alert.handler';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { setNotificationRepository } from '../../../../src/libs/repositories/notification';
import { setUserRepository } from '../../../../src/libs/repositories/user';
import { DocStoreReadOnlyError } from '../../../../src/libs/repositories/errors';
import {
  SANDBOX_ALERT_DOCS_URL,
  alertMessage,
  parseSandboxCapacityAlert,
} from '../../../../src/modules/notification/utils/sandbox-capacity-alert';

const id = () => new mongoose.Types.ObjectId().toString();
const HOUR = 3600_000;

describe('platform alerts: only structured load alerts are delivered', () => {
  const orgA = id();
  let admins: string[];
  let logger: any;
  let mailProducer: { publishEvent: sinon.SinonStub };
  let resolveRecipients: sinon.SinonStub;
  let insertMany: sinon.SinonStub;
  let handler: PlatformAlertHandler;
  let seq = 0;

  const event = (overrides: Record<string, unknown> = {}, payload: Record<string, unknown> = {}) => ({
    audience: 'platformAdmins',
    type: 'SANDBOX_CAPACITY_CRITICAL',
    email: true,
    payload: {
      v: 1,
      alertId: `alert-${(seq += 1)}`,
      level: 'critical',
      firedAt: new Date().toISOString(),
      expiresAt: new Date(Date.now() + 6 * HOUR).toISOString(),
      host: 'pipeshub-7d9c',
      sandboxMode: 'compose',
      memorySource: 'host',
      windowMinutes: 30,
      runs: 210,
      waited: 40,
      refused: 14,
      busyPercent: 50,
      refusedPercent: 6.7,
      longestWaitSeconds: 118,
      slotWaitSeconds: 120,
      emailCooldownSeconds: 86400,
      reason: 'slots',
      reasonCounts: { slots: 40, memory: 0, cpu: 0 },
      indexingSlowed: false,
      indexingSlowedSince: null,
      limit: { current: 6, ceiling: 6, floor: 2 },
      hostMemoryTotalGb: 16,
      hostMemoryFreeGb: 1.1,
      ...payload,
    },
    ...overrides,
  });
  const forged = {
    title: 'Action required: your admin session expired',
    message: 'Sign in again at http://evil.example/login',
    redirectLink: 'http://evil.example/login',
  };
  const forgedPayload = {
    reasonText: 'Re-verify at http://evil.example/login now.',
    indexingText: 'Visit http://evil.example',
  };

  beforeEach(() => {
    admins = [id(), id()];
    logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
    mailProducer = { publishEvent: sinon.stub().resolves() };
    resolveRecipients = sinon.stub().callsFake(async () => [{ orgId: orgA, userIds: admins }]);
    insertMany = sinon.stub().callsFake(async (docs: any[]) => docs.map((doc, i) => ({ ...doc, _id: `n${i}` })));
    setNotificationRepository({ insertMany } as never);
    setUserRepository({
      listLiveByIds: sinon.stub().callsFake(async (_org: string, ids: string[]) =>
        ids.map((userId) => ({ _id: userId, email: `${userId}@example.com` })),
      ),
      serviceAccountIdsAmong: sinon.stub().resolves([]),
    } as never);
    handler = new PlatformAlertHandler(
      logger,
      sinon.createStubInstance(NotificationService) as unknown as NotificationService,
      mailProducer as never,
      resolveRecipients,
    );
  });

  afterEach(() => {
    setNotificationRepository();
    setUserRepository();
    sinon.restore();
  });

  const storedDoc = () => insertMany.lastCall.args[0][0];

  describe('a forged event of another type', () => {
    it('is dropped with one warning, and later ones are logged at debug', async () => {
      const phishing = {
        audience: 'platformAdmins',
        type: 'SECURITY_NOTICE',
        ...forged,
        payload: { v: 1, alertId: 'x', level: 'critical', ...forgedPayload },
      };
      await handler.handle(phishing);
      await handler.handle(phishing);
      expect(resolveRecipients.called).to.be.false;
      expect(insertMany.called).to.be.false;
      expect(mailProducer.publishEvent.called).to.be.false;
      const warnings = logger.warn.getCalls().filter((c: any) => c.args[0] === 'Platform alert skipped: invalid event');
      expect(warnings).to.have.length(1);
      expect(warnings[0].args[1]).to.deep.equal({ reason: 'unknown_type', type: 'SECURITY_NOTICE' });
      expect(logger.debug.calledWithMatch('Platform alert skipped: invalid event')).to.be.true;
    });

    it('never logs the raw event', async () => {
      await handler.handle({ audience: 'platformAdmins', type: 'X', payload: { secret: 'y'.repeat(100) } });
      expect(JSON.stringify(logger.warn.args)).to.not.contain('yyyy');
    });

    it('an org notification type sent to the admins is dropped too', async () => {
      await handler.handle(event({ type: 'CONNECTOR_SYNC_ERROR' }));
      expect(insertMany.called).to.be.false;
    });
  });

  describe('a load alert carrying its own text', () => {
    it('is stored with the fixed title, the built message and the guide link', async () => {
      await handler.handle(event(forged, forgedPayload));
      const doc = storedDoc();
      expect(doc.title).to.equal('Code runs are being refused');
      expect(doc.message).to.equal(
        '14 of 210 code runs in the last 30 minutes (6.7%) were refused after waiting 120 s for a free sandbox. ' +
          'All 6 code-run slots were in use. The host still had free memory and CPU, so the slot limit was the bottleneck.',
      );
      expect(doc.redirectLink).to.equal(SANDBOX_ALERT_DOCS_URL);
      expect(JSON.stringify(doc)).to.not.contain('evil.example');
    });

    it('emails the built text with the guide link only', async () => {
      await handler.handle(event(forged, forgedPayload));
      const data = mailProducer.publishEvent.firstCall.args[0].payload.mail.templateData;
      expect(JSON.stringify(data)).to.not.contain('evil.example');
      expect(data.docsUrl).to.equal(SANDBOX_ALERT_DOCS_URL);
      expect(data.heading).to.equal('Code runs are being refused');
    });

    it('stores the validated payload only, with no unknown keys or free text', async () => {
      await handler.handle(event({}, { ...forgedPayload, junk: 'z'.repeat(1000), nested: { a: 1 } }));
      const payload = storedDoc().payload;
      expect(payload).to.not.have.any.keys('reasonText', 'indexingText', 'junk', 'nested');
      expect(payload).to.include({ v: 1, level: 'critical', runs: 210, refused: 14, host: 'pipeshub-7d9c' });
      expect(storedDoc()).to.include({ severity: 'critical', originService: 'AI Service', status: 'unread' });
    });
  });

  describe('the in-app text', () => {
    const message = (type: string, payload: Record<string, unknown>) => {
      const parsed = parseSandboxCapacityAlert(event({ type }, payload));
      if (!parsed.ok) throw new Error(parsed.reason);
      return alertMessage(parsed.alert);
    };

    it('words a warning on memory with indexing slowed', () => {
      expect(
        message('SANDBOX_CAPACITY_WARNING', {
          level: 'warning', reason: 'memory', busyPercent: 72, waited: 57, indexingSlowedSince: '2026-11-16T10:13:40Z',
        }),
      ).to.equal(
        'For 72% of the last 30 minutes, code runs in chat waited for a free sandbox (57 runs waited). ' +
          'Free memory on the host was low, so new runs were held back to protect the databases and the app. ' +
          'Indexing was already slowed down from 10:13 UTC to give chat priority.',
      );
    });

    it('words a resolved notice', () => {
      expect(
        message('SANDBOX_CAPACITY_RESOLVED', {
          level: 'critical', clearSince: '2026-11-16T10:52:00Z', alertFiredAt: '2026-11-16T10:42:00Z',
        }),
      ).to.equal(
        'Since 10:52 UTC, code runs in chat on pipeshub-7d9c have had enough capacity for 30 minutes. ' +
          'The critical alert from 10:42 UTC is resolved.',
      );
    });

    it('words a resolved notice from an older publisher without the times or host', () => {
      expect(message('SANDBOX_CAPACITY_RESOLVED', { level: 'warning', host: undefined })).to.equal(
        'Code runs in chat have had enough capacity for 30 minutes. The warning alert is resolved.',
      );
    });
  });

  describe('field checks', () => {
    it('drops a host with bidi controls or markup and keeps the rest', async () => {
      for (const host of ['evil‮txt.exe', '<b>h</b>', 'a b', '', '-x', 'x'.repeat(254)]) {
        await handler.handle(event({}, { host }));
        expect(storedDoc().payload.host).to.equal(null);
        expect(storedDoc().message).to.not.contain('‮');
      }
    });

    it('drops numbers that are negative, too large, fractional counts or not finite', async () => {
      await handler.handle(event({}, {
        runs: 1e21, refused: -5, refusedPercent: 1e9, waited: 1.5, windowMinutes: 0, slotWaitSeconds: Infinity,
        limit: { current: -1, ceiling: 1e9, floor: 'x' }, hostMemoryFreeGb: -1,
      }));
      const p = storedDoc().payload;
      expect([p.runs, p.refused, p.refusedPercent, p.waited, p.windowMinutes, p.slotWaitSeconds]).to.deep.equal([
        null, null, null, null, null, null,
      ]);
      expect(p.limit).to.deep.equal({ current: null, ceiling: null, floor: null });
      expect(p.hostMemoryFreeGb).to.equal(null);
      expect(storedDoc().message).to.equal(
        '0 of 0 code runs in the last 30 minutes (0%) were refused for a free sandbox. ' +
          'All code-run slots were in use. The host still had free memory and CPU, so the slot limit was the bottleneck.',
      );
    });

    it('drops an event larger than 16 KB', async () => {
      await handler.handle(event({}, { pad: 'x'.repeat(17 * 1024) }));
      expect(insertMany.called).to.be.false;
      expect(logger.warn.calledWithMatch('Platform alert skipped: invalid event', { reason: 'too_large' })).to.be.true;
    });
  });

  describe('expiry', () => {
    it('expires an event with no expiry six hours after it fired', async () => {
      await handler.handle(event({}, { expiresAt: undefined, firedAt: new Date(Date.now() - 7 * HOUR).toISOString() }));
      expect(insertMany.called).to.be.false;
      expect(logger.warn.calledWithMatch('Platform alert skipped: expired')).to.be.true;
    });

    it('treats an unreadable expiry the same way', async () => {
      await handler.handle(event({}, { expiresAt: 'soon', firedAt: new Date(Date.now() - 7 * HOUR).toISOString() }));
      expect(insertMany.called).to.be.false;
    });

    it('delivers an event with neither time, as fired on receipt', async () => {
      await handler.handle(event({}, { expiresAt: undefined, firedAt: undefined }));
      expect(insertMany.calledOnce).to.be.true;
    });

    it('caps a far-future expiry at a day after it fired', async () => {
      await handler.handle(event({}, {
        firedAt: new Date(Date.now() - 25 * HOUR).toISOString(),
        expiresAt: new Date(Date.now() + 365 * 24 * HOUR).toISOString(),
      }));
      expect(insertMany.called).to.be.false;
    });

    it('drops an event that says it fired in the future', async () => {
      await handler.handle(event({}, { firedAt: new Date(Date.now() + HOUR).toISOString() }));
      expect(insertMany.called).to.be.false;
    });
  });

  describe('dedup and rate limits', () => {
    it('delivers an event again after a store error, then never a third time', async () => {
      const first = event();
      insertMany.onFirstCall().rejects(new DocStoreReadOnlyError());
      let thrown: unknown;
      try {
        await handler.handle(first);
      } catch (error) {
        thrown = error;
      }
      expect(thrown).to.be.instanceOf(DocStoreReadOnlyError);
      await handler.handle(first);
      await handler.handle(first);
      expect(insertMany.callCount).to.equal(2);
      expect(mailProducer.publishEvent.callCount).to.equal(admins.length);
    });

    it(`emails at most ${MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR} alerts of a level an hour, and keeps notifying in-app`, async () => {
      const n = MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR + 2;
      for (let i = 0; i < n; i += 1) await handler.handle(event());
      expect(insertMany.callCount).to.equal(n);
      expect(mailProducer.publishEvent.callCount).to.equal(MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR * admins.length);
      const capped = logger.warn.getCalls().filter((c: any) => c.args[0] === 'Platform alert email skipped: too many this hour');
      expect(capped).to.have.length(1);
    });

    it('counts each level on its own', async () => {
      for (let i = 0; i < MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR; i += 1) await handler.handle(event());
      await handler.handle(event({ type: 'SANDBOX_CAPACITY_WARNING' }, { level: 'warning' }));
      expect(mailProducer.publishEvent.callCount).to.equal((MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR + 1) * admins.length);
    });

    it('allows emails again an hour later', async () => {
      const clock = sinon.useFakeTimers({ now: Date.now(), toFake: ['Date'] });
      try {
        for (let i = 0; i <= MAX_ALERT_EMAILS_PER_LEVEL_PER_HOUR; i += 1) await handler.handle(event());
        const before = mailProducer.publishEvent.callCount;
        clock.tick(HOUR + 1);
        await handler.handle(event());
        expect(mailProducer.publishEvent.callCount).to.equal(before + admins.length);
      } finally {
        clock.restore();
      }
    });

    it(`delivers at most ${MAX_ALERTS_PER_TYPE_PER_HOUR} alerts of a type an hour`, async () => {
      for (let i = 0; i < MAX_ALERTS_PER_TYPE_PER_HOUR + 3; i += 1) {
        await handler.handle(event({ email: false }));
      }
      expect(insertMany.callCount).to.equal(MAX_ALERTS_PER_TYPE_PER_HOUR);
      expect(logger.warn.calledWithMatch('Platform alert skipped: too many this hour')).to.be.true;
    });
  });
});

describe('platform alerts under envelope enforcement', () => {
  const keys = ['ENVELOPE_CTX_REQUIRED', 'ENVELOPE_SIGN', 'ORG_CONTEXT_MODE'] as const;
  let saved: Record<string, string | undefined>;

  beforeEach(() => {
    saved = Object.fromEntries(keys.map((k) => [k, process.env[k]]));
    for (const k of keys) delete process.env[k];
  });

  afterEach(() => {
    for (const k of keys) {
      if (saved[k] === undefined) delete process.env[k];
      else process.env[k] = saved[k];
    }
  });

  it('are delivered by default and with signing in shadow mode', () => {
    expect(platformAlertsBlockedByEnvelope()).to.equal(null);
    process.env.ENVELOPE_SIGN = 'true';
    process.env.ORG_CONTEXT_MODE = 'shadow';
    expect(platformAlertsBlockedByEnvelope()).to.equal(null);
  });

  it('are named as dropped when signing is on and the mode is left at its default (enforce)', () => {
    process.env.ENVELOPE_SIGN = 'true';
    expect(platformAlertsBlockedByEnvelope()).to.contain('ORG_CONTEXT_MODE=enforce');
  });

  it('are named as dropped when a signed context is required', () => {
    process.env.ENVELOPE_CTX_REQUIRED = 'true';
    expect(platformAlertsBlockedByEnvelope()).to.contain('ENVELOPE_CTX_REQUIRED');
  });

  it('are named as dropped when signing is enforced', () => {
    process.env.ENVELOPE_SIGN = 'true';
    process.env.ORG_CONTEXT_MODE = 'enforce';
    expect(platformAlertsBlockedByEnvelope()).to.contain('ORG_CONTEXT_MODE=enforce');
  });

  it('an invalid mode does not throw here', () => {
    process.env.ENVELOPE_SIGN = 'true';
    process.env.ORG_CONTEXT_MODE = 'bogus';
    expect(platformAlertsBlockedByEnvelope()).to.equal(null);
  });

  it('logs one error at startup only when they are dropped', () => {
    const logger = { error: sinon.stub() };
    logIfPlatformAlertsBlocked(logger as never);
    expect(logger.error.called).to.be.false;
    process.env.ENVELOPE_CTX_REQUIRED = 'true';
    logIfPlatformAlertsBlocked(logger as never);
    expect(logger.error.calledOnce).to.be.true;
    expect(logger.error.firstCall.args[0]).to.contain('will not be delivered');
  });
});

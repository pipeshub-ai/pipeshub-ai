import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { PlatformAlertHandler } from '../../../../src/modules/notification/service/platform-alert.handler';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { setNotificationRepository } from '../../../../src/libs/repositories/notification';
import { setUserRepository } from '../../../../src/libs/repositories/user';
import { DocStoreReadOnlyError } from '../../../../src/libs/repositories/errors';
import { getEmailContent } from '../../../../src/modules/mail/utils/email-content';
import { buildSandboxCapacityMail } from '../../../../src/modules/notification/utils/sandbox-capacity-mail';
import { parseSandboxCapacityAlert } from '../../../../src/modules/notification/utils/sandbox-capacity-alert';

const id = () => new mongoose.Types.ObjectId().toString();

describe('notification/service/platform-alert.handler edge cases', () => {
  const orgA = id();
  let admins: string[];
  let logger: any;
  let notificationService: sinon.SinonStubbedInstance<NotificationService>;
  let mailProducer: { publishEvent: sinon.SinonStub };
  let resolveRecipients: sinon.SinonStub;
  let insertMany: sinon.SinonStub;
  let listLiveByIds: sinon.SinonStub;
  let handler: PlatformAlertHandler;

  const event = (overrides: Record<string, unknown> = {}, payload: Record<string, unknown> = {}) => ({
    audience: 'platformAdmins',
    type: 'SANDBOX_CAPACITY_CRITICAL',
    severity: 'critical',
    status: 'unread',
    originService: 'AI Service',
    title: 'Code runs are being refused',
    message: '14 of 210 code runs in the last 30 minutes (6.7%) were refused.',
    redirectLink: 'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts',
    email: true,
    isDeleted: false,
    payload: {
      v: 1,
      alertId: 'a1',
      level: 'critical',
      expiresAt: new Date(Date.now() + 6 * 3600_000).toISOString(),
      host: 'h1',
      memorySource: 'host',
      windowMinutes: 30,
      runs: 210,
      waited: 40,
      refused: 14,
      busyPercent: 50,
      refusedPercent: 6.7,
      slotWaitSeconds: 120,
      reason: 'slots',
      reasonText: 'All 6 code-run slots were in use.',
      indexingText: null,
      limit: { current: 6, ceiling: 6, floor: 2 },
      hostMemoryTotalGb: null,
      hostMemoryFreeGb: null,
      ...payload,
    },
    ...overrides,
  });

  beforeEach(() => {
    admins = [id(), id()];
    logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
    notificationService = sinon.createStubInstance(NotificationService);
    mailProducer = { publishEvent: sinon.stub().resolves() };
    resolveRecipients = sinon.stub().callsFake(async () => [{ orgId: orgA, userIds: admins }]);
    insertMany = sinon.stub().callsFake(async (docs: any[]) => docs.map((doc, i) => ({ ...doc, _id: `n${i}` })));
    setNotificationRepository({ insertMany } as never);
    listLiveByIds = sinon.stub().callsFake(async (_org: string, ids: string[]) =>
      ids.map((userId) => ({ _id: userId, email: `${userId}@example.com` })),
    );
    setUserRepository({
      listLiveByIds,
      serviceAccountIdsAmong: sinon.stub().resolves([]),
    } as never);
    handler = new PlatformAlertHandler(
      logger,
      notificationService as unknown as NotificationService,
      mailProducer as never,
      resolveRecipients,
    );
  });

  afterEach(() => {
    setNotificationRepository();
    setUserRepository();
    sinon.restore();
  });

  describe('malformed events are acknowledged without delivering anything', () => {
    const cases: Array<[string, unknown]> = [
      ['null', null],
      ['a string', 'platformAdmins'],
      ['no type', { ...event(), type: undefined }],
      ['an empty type', event({ type: '' })],
      ['a type that is not a load alert', event({ type: 'SECURITY_NOTICE' })],
      ['a type in another case', event({ type: 'sandbox_capacity_critical' })],
      ['a level that does not match the type', event({}, { level: 'warning' })],
      ['a level that is not a level', event({ type: 'SANDBOX_CAPACITY_RESOLVED' }, { level: 'resolved' })],
      ['a reason that is not a reason', event({}, { reason: 'please sign in' })],
      ['an alertId with markup', event({}, { alertId: '<b>x</b>' })],
      ['a firedAt an hour in the future', event({}, { firedAt: new Date(Date.now() + 3600_000).toISOString() })],
      ['an event over 16 KB', event({}, { junk: 'x'.repeat(20_000) })],
      ['no payload', event({ payload: null })],
      ['a payload that is a string', event({ payload: 'x' })],
      ['a version given as text', event({}, { v: '1' })],
      ['an empty alertId', event({}, { alertId: '' })],
      ['an alertId that is not text', event({}, { alertId: 7 })],
    ];
    for (const [name, value] of cases) {
      it(`with ${name}`, async () => {
        await handler.handle(value);
        expect(resolveRecipients.called).to.be.false;
        expect(insertMany.called).to.be.false;
        expect(mailProducer.publishEvent.called).to.be.false;
      });
    }
  });

  it('delivers a fresh event whose expiry cannot be read', async () => {
    await handler.handle(event({}, { expiresAt: 'not a date', firedAt: new Date().toISOString() }));
    expect(insertMany.calledOnce).to.be.true;
  });

  it('drops an event that expired a moment ago', async () => {
    await handler.handle(event({}, { expiresAt: new Date(Date.now() - 1000).toISOString() }));
    expect(insertMany.called).to.be.false;
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  it('stores the type, severity and origin the inbox shows', async () => {
    await handler.handle(event());
    const doc = insertMany.firstCall.args[0][0];
    expect(doc).to.include({
      type: 'SANDBOX_CAPACITY_CRITICAL',
      severity: 'critical',
      originService: 'AI Service',
      status: 'unread',
      orgId: orgA,
      isDeleted: false,
    });
    expect(doc.payload.alertId).to.equal('a1');
  });

  it('notifies and emails once when the broker delivers the same event twice', async () => {
    await handler.handle(event());
    await handler.handle(event());
    expect(insertMany.callCount).to.equal(1);
    expect(mailProducer.publishEvent.callCount).to.equal(2);
  });

  it('delivers to many admins with one store call and one email job per admin', async () => {
    admins = Array.from({ length: 500 }, id);
    await handler.handle(event());
    expect(insertMany.firstCall.args[0]).to.have.length(500);
    expect(mailProducer.publishEvent.callCount).to.equal(500);
    const sentTo = mailProducer.publishEvent.getCalls().map((call) => call.args[0].payload.mail.sendEmailTo);
    expect(sentTo.every((to) => to.length === 1)).to.be.true;
    expect(new Set(sentTo.map((to) => to[0])).size).to.equal(500);
  });

  it('never shows one admin the address of another', async () => {
    await handler.handle(event());
    for (const call of mailProducer.publishEvent.getCalls()) {
      const mail = call.args[0].payload.mail;
      expect(mail.sendEmailTo).to.have.length(1);
      expect(mail).to.not.have.any.keys('cc', 'bcc');
    }
  });

  it('queues the other admins when one mail job fails', async () => {
    mailProducer.publishEvent.onFirstCall().rejects(new Error('broker hiccup'));
    await handler.handle(event());
    expect(mailProducer.publishEvent.callCount).to.equal(2);
    expect(logger.info.calledWithMatch('Platform alert delivered', { emailQueued: 1 })).to.be.true;
  });

  it('sends in-app only when the event says the level was already emailed today', async () => {
    await handler.handle(event({ email: false }));
    expect(insertMany.calledOnce).to.be.true;
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  it('emails when an older event carries no email field', async () => {
    const legacy: Record<string, unknown> = event();
    delete legacy.email;
    await handler.handle(legacy);
    expect(mailProducer.publishEvent.callCount).to.equal(2);
  });

  it('skips the email when no admin has an address, after notifying in-app', async () => {
    listLiveByIds.callsFake(async (_org: string, ids: string[]) => ids.map((userId) => ({ _id: userId, email: '  ' })));
    await handler.handle(event());
    expect(insertMany.calledOnce).to.be.true;
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  it('emails only admins who are still live when the email is built', async () => {
    const [stillThere] = admins;
    listLiveByIds.callsFake(async () => [{ _id: stillThere, email: 'still@example.com' }]);
    await handler.handle(event());
    expect(mailProducer.publishEvent.firstCall.args[0].payload.mail.sendEmailTo).to.deep.equal(['still@example.com']);
  });

  it('keeps the in-app notification when looking up the addresses fails', async () => {
    listLiveByIds.rejects(new Error('users store down'));
    await handler.handle(event());
    expect(insertMany.calledOnce).to.be.true;
    expect(mailProducer.publishEvent.called).to.be.false;
    expect(logger.error.calledWithMatch('Platform alert email not queued')).to.be.true;
  });

  it('lets a retryable error while finding recipients reach the consumer', async () => {
    resolveRecipients.rejects(new DocStoreReadOnlyError());
    let thrown: unknown;
    try {
      await handler.handle(event());
    } catch (error) {
      thrown = error;
    }
    expect(thrown).to.be.instanceOf(DocStoreReadOnlyError);
    expect(insertMany.called).to.be.false;
  });

  it('sends no email for a resolved notice, even when asked', async () => {
    await handler.handle(event({ type: 'SANDBOX_CAPACITY_RESOLVED', severity: 'success', email: true }, { level: 'critical' }));
    expect(insertMany.calledOnce).to.be.true;
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  describe('the email body', () => {
    const render = (ev: Record<string, unknown>) => {
      const parsed = parseSandboxCapacityAlert(ev);
      if (!parsed.ok) throw new Error(parsed.reason);
      const mail = buildSandboxCapacityMail(parsed.alert)!;
      return getEmailContent('sandboxCapacityAlert', mail.templateData as never);
    };

    it('shows no text from the event', () => {
      const html = render(event(
        { title: '<img src=x onerror=alert(1)>', message: '{{constructor}}', redirectLink: 'http://evil.example' },
        { reasonText: '"><script>alert(1)</script>', indexingText: 'Visit http://evil.example', host: '<b>h</b>' },
      ));
      expect(html).to.not.contain('script');
      expect(html).to.not.contain('img src');
      expect(html).to.not.contain('&lt;b&gt;h');
      expect(html).to.not.contain('evil.example');
      expect(html).to.not.contain('constructor');
      expect(html).to.contain('All 6 code-run slots were in use.');
    });

    it('falls back to safe numbers when the payload numbers are missing, not finite or out of range', () => {
      const html = render(event({}, {
        runs: 'many', refused: Number.NaN, refusedPercent: Infinity, windowMinutes: null, waited: -5, busyPercent: 1e9,
      }));
      expect(html).to.contain('0 of 0 code runs');
      expect(html).to.contain('last 30 minutes');
      expect(html).to.not.contain('NaN');
      expect(html).to.not.contain('Infinity');
      expect(html).to.not.contain('-5');
      expect(html).to.not.contain('1000000000');
    });

    it('says alerts repeat in the app and the email comes at most once a day', () => {
      const html = render(event());
      expect(html).to.contain('notified in the app each time');
      expect(html).to.contain('this email is sent at most once a day for each level');
    });
  });
});

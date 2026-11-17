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
      ['no title', event({ title: 42 })],
      ['no message', event({ message: undefined })],
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

  it('delivers an event whose expiry cannot be read, rather than losing it', async () => {
    await handler.handle(event({}, { expiresAt: 'not a date' }));
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

  it('notifies and emails twice when the broker delivers the same event twice', async () => {
    // Dedup is the publisher's cooldown; pinned so a change here is deliberate.
    await handler.handle(event());
    await handler.handle(event());
    expect(insertMany.callCount).to.equal(2);
    expect(mailProducer.publishEvent.callCount).to.equal(2);
  });

  it('delivers to many admins with one store call and one email job', async () => {
    admins = Array.from({ length: 500 }, id);
    await handler.handle(event());
    expect(insertMany.firstCall.args[0]).to.have.length(500);
    expect(mailProducer.publishEvent.calledOnce).to.be.true;
    expect(mailProducer.publishEvent.firstCall.args[0].payload.mail.sendEmailTo).to.have.length(500);
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
    resolveRecipients.rejects(new DocStoreReadOnlyError('frozen'));
    let thrown: unknown;
    try {
      await handler.handle(event());
    } catch (error) {
      thrown = error;
    }
    expect(thrown).to.be.instanceOf(DocStoreReadOnlyError);
    expect(insertMany.called).to.be.false;
  });

  it('sends no email for a level it has no email for, even when asked', async () => {
    await handler.handle(event({ type: 'SANDBOX_CAPACITY_RESOLVED', severity: 'success' }, { level: 'resolved' }));
    expect(insertMany.calledOnce).to.be.true;
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  describe('the email body', () => {
    const render = (ev: Record<string, unknown>) => {
      const mail = buildSandboxCapacityMail(ev, ev.payload as never)!;
      return getEmailContent('sandboxCapacityAlert', mail.templateData as never);
    };

    it('escapes markup and template syntax from every text field', () => {
      const html = render(event(
        { title: '<img src=x onerror=alert(1)>', message: '{{constructor}}' },
        { reasonText: '"><script>alert(1)</script>', indexingText: '<a href="javascript:x">i</a>', host: '<b>h</b>' },
      ));
      expect(html).to.not.contain('<script>');
      expect(html).to.not.contain('<img src=x');
      expect(html).to.not.contain('<b>h</b>');
      expect(html).to.not.contain('href="javascript:');
      expect(html).to.contain('&lt;script&gt;');
    });

    it('never puts a link that is not http(s) in the email', () => {
      for (const link of ['javascript:alert(1)', 'data:text/html,x', 'not a url', '']) {
        const mail = buildSandboxCapacityMail(event({ redirectLink: link }), event().payload as never)!;
        expect(mail.templateData.docsUrl).to.equal('https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts');
      }
      const mail = buildSandboxCapacityMail(event({ redirectLink: 'https://example.com/x' }), event().payload as never)!;
      expect(mail.templateData.docsUrl).to.equal('https://example.com/x');
    });

    it('renders a very long reason in full', () => {
      const long = 'x'.repeat(100_000);
      expect(render(event({}, { reasonText: long }))).to.contain(long);
    });

    it('falls back to safe numbers when the payload numbers are missing or not finite', () => {
      const html = render(event({}, { runs: 'many', refused: Number.NaN, refusedPercent: Infinity, windowMinutes: null }));
      expect(html).to.contain('0 of 0 code runs');
      expect(html).to.contain('last 30 minutes');
      expect(html).to.not.contain('NaN');
      expect(html).to.not.contain('Infinity');
    });

    it('does not promise one email a day, since a resolved alert can fire again', () => {
      expect(render(event())).to.contain('or again after a back-to-normal notice');
    });
  });
});

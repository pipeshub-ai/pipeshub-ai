import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { PlatformAlertHandler } from '../../../../src/modules/notification/service/platform-alert.handler';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { setNotificationRepository } from '../../../../src/libs/repositories/notification';
import { setUserRepository } from '../../../../src/libs/repositories/user';
import { getCurrentOrgId } from '../../../../src/libs/context/orgContext';
import { DocStoreReadOnlyError } from '../../../../src/libs/repositories/errors';
import { getEmailContent } from '../../../../src/modules/mail/utils/email-content';
import { configureInbox, runWithEnvelope } from '../../../../src/libs/context/envelope';
import { MemInbox } from '../../../helpers/memory-inbox';

const id = () => new mongoose.Types.ObjectId().toString();

describe('notification/service/platform-alert.handler', () => {
  const orgA = id();
  const orgB = id();
  const admin1 = id();
  const admin2 = id();
  const robot = id();

  let logger: any;
  let notificationService: sinon.SinonStubbedInstance<NotificationService>;
  let mailProducer: { publishEvent: sinon.SinonStub };
  let resolveRecipients: sinon.SinonStub;
  let insertMany: sinon.SinonStub;
  let insertOrgs: Array<string | undefined>;
  let handler: PlatformAlertHandler;

  const event = (overrides: Record<string, unknown> = {}, payload: Record<string, unknown> = {}) => ({
    audience: 'platformAdmins',
    type: 'SANDBOX_CAPACITY_WARNING',
    severity: 'warning',
    status: 'unread',
    originService: 'AI Service',
    title: 'Code runs are waiting for capacity',
    message: 'For 72% of the last 30 minutes, code runs in chat waited for a free sandbox (57 runs waited).',
    redirectLink: 'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts',
    email: true,
    isDeleted: false,
    payload: {
      v: 1,
      alertId: 'a1',
      level: 'warning',
      firedAt: new Date().toISOString(),
      expiresAt: new Date(Date.now() + 6 * 3600_000).toISOString(),
      host: 'pipeshub-7d9c',
      sandboxMode: 'compose',
      memorySource: 'host',
      windowMinutes: 30,
      runs: 412,
      waited: 57,
      refused: 0,
      busyPercent: 72,
      refusedPercent: 0,
      longestWaitSeconds: 118,
      slotWaitSeconds: 120,
      reason: 'memory',
      reasonText: 'Free memory on the host was low, so new runs were held back to protect the databases and the app.',
      indexingText: 'Indexing was already slowed down from 10:13 UTC to give chat priority.',
      indexingSlowedSince: '2026-11-16T10:13:40Z',
      limit: { current: 3, ceiling: 6, floor: 2 },
      hostMemoryTotalGb: 16,
      hostMemoryFreeGb: 1.1,
      ...payload,
    },
    ...overrides,
  });

  beforeEach(() => {
    logger = { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() };
    notificationService = sinon.createStubInstance(NotificationService);
    mailProducer = { publishEvent: sinon.stub().resolves() };
    resolveRecipients = sinon.stub().resolves([{ orgId: orgA, userIds: [admin1, admin2] }]);
    insertOrgs = [];
    insertMany = sinon.stub().callsFake(async (docs: any[]) => {
      insertOrgs.push(getCurrentOrgId());
      return docs.map((doc, i) => ({ ...doc, _id: `n${i}` }));
    });
    setNotificationRepository({ insertMany } as never);
    setUserRepository({
      listLiveByIds: sinon.stub().callsFake(async (_org: string, ids: string[]) =>
        ids.map((userId) => ({ _id: userId, email: userId === robot ? 'robot@example.com' : `${userId}@example.com` })),
      ),
      serviceAccountIdsAmong: sinon.stub().callsFake(async (_org: string, ids: string[]) =>
        ids.filter((userId) => userId === robot),
      ),
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

  it('stores one notification per admin in their org, without the delivery-only fields', async () => {
    await handler.handle(event());

    expect(insertMany.calledOnce).to.be.true;
    const docs = insertMany.firstCall.args[0];
    expect(docs).to.have.length(2);
    expect(docs.map((d: any) => d.assignedTo)).to.deep.equal([admin1, admin2]);
    for (const doc of docs) {
      expect(doc.orgId).to.equal(orgA);
      expect(doc.type).to.equal('SANDBOX_CAPACITY_WARNING');
      expect(doc.severity).to.equal('warning');
      expect(doc).to.not.have.any.keys('audience', 'email', 'recipientUserIds', 'recipientRoles');
    }
    expect(insertOrgs).to.deep.equal([orgA]);
    expect((notificationService.sendToUser as sinon.SinonStub).callCount).to.equal(2);
  });

  it('queues one email per admin, sent only if SMTP is configured', async () => {
    await handler.handle(event());

    expect(mailProducer.publishEvent.callCount).to.equal(2);
    expect(mailProducer.publishEvent.secondCall.args[0].payload.mail.sendEmailTo).to.deep.equal([`${admin2}@example.com`]);
    const payload = mailProducer.publishEvent.firstCall.args[0].payload;
    expect(payload.orgId).to.equal(orgA);
    expect(payload.ifSmtpConfigured).to.be.true;
    expect(payload.mail.emailTemplateType).to.equal('sandboxCapacityAlert');
    expect(payload.mail.sendEmailTo).to.deep.equal([`${admin1}@example.com`]);
    expect(payload.mail.subject).to.equal('PipesHub: code runs in chat are waiting for capacity');
    // The queued data renders.
    expect(getEmailContent('sandboxCapacityAlert', payload.mail.templateData)).to.contain('Already done');
  });

  it('uses the refused subject for a critical alert', async () => {
    await handler.handle(event({ type: 'SANDBOX_CAPACITY_CRITICAL', severity: 'critical' }, { level: 'critical' }));

    const payload = mailProducer.publishEvent.firstCall.args[0].payload;
    expect(payload.mail.subject).to.equal('PipesHub: code runs in chat are being refused');
  });

  it('notifies a service-account admin in-app but does not email it', async () => {
    resolveRecipients.resolves([{ orgId: orgA, userIds: [admin1, robot] }]);

    await handler.handle(event());

    expect(insertMany.firstCall.args[0]).to.have.length(2);
    expect(mailProducer.publishEvent.firstCall.args[0].payload.mail.sendEmailTo).to.deep.equal([`${admin1}@example.com`]);
  });

  it('queues no email when the event does not ask for one, whatever its type', async () => {
    await handler.handle(event({ email: false }));
    await handler.handle(
      event({ type: 'SANDBOX_CAPACITY_RESOLVED', severity: 'success', email: false }, { level: 'warning', alertId: 'r1' }),
    );

    expect(insertMany.calledTwice).to.be.true;
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  it('stores a resolved notice in-app with no mail', async () => {
    await handler.handle(
      event(
        { type: 'SANDBOX_CAPACITY_RESOLVED', severity: 'success', title: 'Code runs are no longer held back', email: false },
        { level: 'warning' },
      ),
    );

    const docs = insertMany.firstCall.args[0];
    expect(docs.map((d: any) => d.severity)).to.deep.equal(['success', 'success']);
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  it('drops an expired event with a warning', async () => {
    await handler.handle(event({}, { expiresAt: new Date(Date.now() - 1000).toISOString() }));

    expect(resolveRecipients.called).to.be.false;
    expect(insertMany.called).to.be.false;
    expect(logger.warn.calledWithMatch('Platform alert skipped: expired')).to.be.true;
  });

  it('skips an event of another payload version', async () => {
    await handler.handle(event({}, { v: 2 }));

    expect(insertMany.called).to.be.false;
    expect(logger.warn.calledWithMatch('Platform alert skipped: invalid event')).to.be.true;
  });

  it('warns and stores nothing when there are no recipients', async () => {
    resolveRecipients.resolves([]);

    await handler.handle(event());

    expect(insertMany.called).to.be.false;
    expect(mailProducer.publishEvent.called).to.be.false;
    expect(logger.warn.calledWithMatch('Platform alert not delivered: no recipients')).to.be.true;
  });

  it('logs a failed mail publish and still resolves, so the event is not delivered again', async () => {
    mailProducer.publishEvent.rejects(new Error('broker down'));

    await handler.handle(event());

    expect(insertMany.calledOnce).to.be.true;
    expect(logger.error.calledWithMatch('Platform alert email not queued')).to.be.true;
  });

  it('rethrows a retryable store error and queues no email', async () => {
    const err = new DocStoreReadOnlyError();
    insertMany.rejects(err);

    let thrown: unknown;
    try {
      await handler.handle(event());
    } catch (e) {
      thrown = e;
    }
    expect(thrown).to.equal(err);
    expect(mailProducer.publishEvent.called).to.be.false;
  });

  it('delivers to every recipient org in its own org, with one email each', async () => {
    resolveRecipients.resolves([
      { orgId: orgA, userIds: [admin1] },
      { orgId: orgB, userIds: [admin2] },
    ]);

    await handler.handle(event());

    expect(insertOrgs).to.deep.equal([orgA, orgB]);
    expect(mailProducer.publishEvent.callCount).to.equal(2);
    expect(mailProducer.publishEvent.secondCall.args[0].payload.orgId).to.equal(orgB);
  });

  describe('with the durable consumer inbox', () => {
    const G = 'notification-consumer-group';
    let inbox: MemInbox;
    beforeEach(() => {
      inbox = new MemInbox();
      configureInbox(inbox);
      resolveRecipients.resolves([{ orgId: orgA, userIds: [admin1] }, { orgId: orgB, userIds: [admin2] }]);
    });
    afterEach(() => configureInbox(null));
    const deliver = (h: PlatformAlertHandler, msg: unknown) =>
      runWithEnvelope('notification', JSON.parse(JSON.stringify(msg)), (v) => h.handle(v), G);
    const fresh = () =>
      new PlatformAlertHandler(logger, notificationService as unknown as NotificationService, mailProducer as never, resolveRecipients);

    it('a retry after one org failed stores only the missing org, and the email goes out once', async () => {
      const msg = { ...event(), eventId: 'evt-p1' };
      let failB = true;
      insertMany.callsFake(async (docs: any[]) => {
        const org = getCurrentOrgId();
        if (org === orgB && failB) throw new DocStoreReadOnlyError();
        insertOrgs.push(org);
        return docs.map((doc, i) => ({ ...doc, _id: `n${i}` }));
      });
      let thrown: unknown;
      try { await deliver(handler, msg); } catch (e) { thrown = e; }
      expect(thrown).to.be.instanceOf(DocStoreReadOnlyError);
      expect(mailProducer.publishEvent.called).to.be.false;
      failB = false;
      await deliver(handler, msg);
      expect(insertOrgs).to.deep.equal([orgA, orgB]);
      expect(mailProducer.publishEvent.callCount).to.equal(2);
    });

    /** Runs every published mail job through the mail consumer's wrapper and inbox; returns the addresses actually sent to. */
    const sendAll = async () => {
      const sent: string[] = [];
      for (const call of mailProducer.publishEvent.getCalls()) {
        const job = call.args[0].payload;
        await runWithEnvelope('mail', JSON.parse(JSON.stringify(job)), async (v: any) => { sent.push(v.mail.sendEmailTo[0]); }, 'mail-consumer-group');
      }
      return sent.sort();
    };

    it('a stop after the last org was stored and before its mail was queued: the redelivery queues it, and each admin gets one email', async () => {
      const msg = { ...event(), eventId: 'evt-p3' };
      const hang = new Promise<void>(() => {});
      mailProducer.publishEvent.onFirstCall().returns(hang);
      // the first delivery stops inside its first mail publish: never acknowledged, so the broker delivers it again
      void deliver(handler, msg);
      for (let i = 0; i < 100 && !mailProducer.publishEvent.called; i++) await new Promise((r) => setTimeout(r, 5));
      expect(insertOrgs).to.deep.equal([orgA, orgB]);
      await deliver(fresh(), msg);
      await deliver(fresh(), msg);
      expect(insertOrgs).to.deep.equal([orgA, orgB]);
      // the hung publish may still have reached the broker: it carries the same event id as the redelivery's
      const ids = mailProducer.publishEvent.getCalls().map((c) => c.args[0].payload.eventId);
      expect(ids[0]).to.equal(ids[1]);
      expect(await sendAll()).to.deep.equal([`${admin1}@example.com`, `${admin2}@example.com`]);
    });

    it('a publish that failed partway is published again with the same ids, and each admin gets one email', async () => {
      resolveRecipients.resolves([{ orgId: orgA, userIds: [admin1, admin2] }]);
      const msg = { ...event(), eventId: 'evt-p4' };
      mailProducer.publishEvent.onSecondCall().rejects(new Error('broker away'));
      await deliver(handler, msg);
      await deliver(fresh(), msg);
      await deliver(fresh(), msg);
      expect(mailProducer.publishEvent.callCount).to.equal(4);
      expect(await sendAll()).to.deep.equal([`${admin1}@example.com`, `${admin2}@example.com`]);
    });

    it('an org stored by an earlier delivery still gets its email when the other org has no recipients left', async () => {
      const msg = { ...event(), eventId: 'evt-p5' };
      insertMany.callsFake(async (docs: any[]) => {
        const org = getCurrentOrgId();
        if (org === orgB) throw new DocStoreReadOnlyError();
        insertOrgs.push(org);
        return docs.map((doc, i) => ({ ...doc, _id: `n${i}` }));
      });
      let thrown: unknown;
      try { await deliver(handler, msg); } catch (e) { thrown = e; }
      expect(thrown).to.be.instanceOf(DocStoreReadOnlyError);
      // before the retry, org B loses its last admin
      resolveRecipients.resolves([{ orgId: orgA, userIds: [admin1] }]);
      await deliver(handler, msg);
      expect(insertOrgs).to.deep.equal([orgA]);
      expect(mailProducer.publishEvent.callCount).to.equal(1);
      expect(mailProducer.publishEvent.firstCall.args[0].payload.mail.sendEmailTo).to.deep.equal([`${admin1}@example.com`]);
      expect(logger.warn.calledWithMatch('Platform alert not delivered: no recipients')).to.be.false;
    });

    it('a redelivery after a restart stores nothing, queues no email and warns nothing', async () => {
      const msg = { ...event(), eventId: 'evt-p2' };
      await deliver(handler, msg);
      expect(insertOrgs).to.deep.equal([orgA, orgB]);
      expect(mailProducer.publishEvent.callCount).to.equal(2);
      // a restarted process has no memory of the alert id; the inbox still has each org's record
      await deliver(fresh(), msg);
      expect(insertOrgs).to.deep.equal([orgA, orgB]);
      expect(mailProducer.publishEvent.callCount).to.equal(2);
      expect(logger.warn.called).to.be.false;
      expect(logger.error.called).to.be.false;
    });
  });
});

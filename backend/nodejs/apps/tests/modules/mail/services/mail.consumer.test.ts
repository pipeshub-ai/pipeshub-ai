import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { MailConsumer } from '../../../../src/modules/mail/services/mail.consumer';
import { classifyMailError } from '../../../../src/modules/mail/types/mail-event.types';

describe('MailConsumer - asynchronous mail delivery', () => {
  let consumer: MailConsumer;
  let mockConsumer: any;
  let mockLogger: any;
  let mockSender: any;
  let mockNotificationProducer: any;
  let clock: sinon.SinonFakeTimers;

  const smtp = { host: 'mailpit', port: 1025, fromEmail: 'no-reply@example.com' };

  const payload = (overrides: Record<string, unknown> = {}) => ({
    mail: {
      emailTemplateType: 'appuserInvite',
      subject: 'You are invited',
      sendEmailTo: ['user@example.com'],
    },
    orgId: '507f1f77bcf86cd799439012',
    ...overrides,
  });

  /** Drives the retry loop's backoff sleeps without waiting in real time. */
  const runWithFakeTimers = async (work: Promise<void>): Promise<void> => {
    for (let i = 0; i < 10; i++) {
      await Promise.resolve();
      await clock.tickAsync(60_000);
    }
    await work;
  };

  beforeEach(() => {
    clock = sinon.useFakeTimers();
    mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    };
    mockConsumer = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(true),
      subscribe: sinon.stub().resolves(),
      consume: sinon.stub().resolves(),
    };
    mockSender = {
      getSmtpConfig: sinon.stub().returns(smtp),
      send: sinon.stub().resolves({ status: 'sent' }),
    };
    mockNotificationProducer = {
      start: sinon.stub().resolves(),
      publishEvent: sinon.stub().resolves(),
    };
    consumer = new MailConsumer(
      mockConsumer,
      mockLogger,
      mockSender,
      mockNotificationProducer,
      () =>
        ({
          frontendUrl: 'http://app.test',
          scopedJwtSecret: 'scoped-secret',
        }) as any,
    );
  });

  afterEach(() => {
    clock.restore();
    sinon.restore();
  });

  const deliver = (p: unknown) => (consumer as any).deliver(p);

  it('sends once and does not notify when delivery succeeds', async () => {
    await deliver(payload());

    expect(mockSender.send.calledOnce).to.be.true;
    expect(mockNotificationProducer.publishEvent.called).to.be.false;
  });

  it('does not retry a permanent failure, and notifies admins', async () => {
    mockSender.send.resolves({ status: 'permanent', error: '550 no such user' });

    await deliver(payload());

    // A 5xx would fail identically on replay, so exactly one attempt.
    expect(mockSender.send.callCount).to.equal(1);
    expect(mockNotificationProducer.publishEvent.calledOnce).to.be.true;

    const event = mockNotificationProducer.publishEvent.firstCall.args[0];
    expect(event.payload.type).to.equal('mail.deliveryFailed');
    expect(event.payload.recipientRoles).to.deep.equal(['admin']);
    expect(event.payload.orgId).to.equal('507f1f77bcf86cd799439012');
  });

  it('does not retry an indeterminate deadline, so a late SMTP success cannot duplicate', async () => {
    mockSender.send.resolves({
      status: 'indeterminate',
      error: 'SMTP send exceeded 120000ms deadline',
    });

    await deliver(payload());

    expect(mockSender.send.callCount).to.equal(1);
    expect(mockNotificationProducer.publishEvent.calledOnce).to.be.true;
  });

  it('retries a transient failure and stops as soon as it succeeds', async () => {
    mockSender.send
      .onCall(0).resolves({ status: 'transient', error: 'ETIMEDOUT' })
      .onCall(1).resolves({ status: 'sent' });

    await runWithFakeTimers(deliver(payload()));

    expect(mockSender.send.callCount).to.equal(2);
    expect(mockNotificationProducer.publishEvent.called).to.be.false;
  });

  it('gives up after the retry limit and notifies admins once', async () => {
    mockSender.send.resolves({ status: 'transient', error: 'ECONNREFUSED' });

    await runWithFakeTimers(deliver(payload()));

    expect(mockSender.send.callCount).to.equal(4);
    expect(mockNotificationProducer.publishEvent.calledOnce).to.be.true;
  });

  it('does not run the retry ladder while the SMTP circuit is open', async () => {
    mockSender.send.resolves({
      status: 'unavailable',
      error: 'SMTP server unreachable; sending paused',
    });

    await deliver(payload());

    expect(mockSender.send.callCount).to.equal(1);
    expect(mockNotificationProducer.publishEvent.calledOnce).to.be.true;
  });

  it('stops retrying once the per-message budget is spent, so the broker never sees a stuck handler', async () => {
    // Each attempt burns a full 40s connection timeout.
    mockSender.send.callsFake(async () => {
      clock.tick(40_000);
      return { status: 'transient', error: 'ETIMEDOUT' };
    });

    await runWithFakeTimers(deliver(payload()));

    expect(mockSender.send.callCount).to.equal(2);
    for (const call of mockSender.send.getCalls()) {
      expect(call.args[2]).to.be.at.most(90_000);
    }
    expect(mockNotificationProducer.publishEvent.calledOnce).to.be.true;
  });

  it('mints the set-password link at send time instead of carrying it in the event', async () => {
    const event = payload({
      mail: { ...payload().mail, templateData: { orgName: 'Acme' } },
      passwordResetLinkFor: {
        userId: 'u1',
        orgId: '507f1f77bcf86cd799439012',
        email: 'user@example.com',
      },
    });

    await deliver(event);

    const sent = mockSender.send.firstCall.args[0];
    expect(sent.templateData.orgName).to.equal('Acme');
    expect(sent.templateData.link).to.match(
      /^http:\/\/app\.test\/reset-password#token=.+/,
    );
    expect((event.mail as any).templateData).to.not.have.property('link');
  });

  it('notifies instead of silently dropping when the reset link cannot be built', async () => {
    const failing = new MailConsumer(
      mockConsumer,
      mockLogger,
      mockSender,
      mockNotificationProducer,
      () => {
        throw new Error('config unavailable');
      },
    );
    const event = payload({
      passwordResetLinkFor: { userId: 'u1', orgId: 'o1', email: 'a@b.co' },
    });

    await (failing as any).deliver(event);

    expect(mockSender.send.called).to.be.false;
    expect(mockNotificationProducer.publishEvent.calledOnce).to.be.true;
  });

  it('does not attempt delivery when SMTP is unconfigured', async () => {
    mockSender.getSmtpConfig.returns(null);

    await deliver(payload());

    expect(mockSender.send.called).to.be.false;
    expect(mockNotificationProducer.publishEvent.calledOnce).to.be.true;
  });

  it('logs instead of notifying when the event carries no orgId', async () => {
    mockSender.send.resolves({ status: 'permanent', error: '550 rejected' });

    await deliver(payload({ orgId: undefined }));

    // Without an org the notification pipeline would drop the event anyway.
    expect(mockNotificationProducer.publishEvent.called).to.be.false;
    expect(mockLogger.error.called).to.be.true;
  });

  it('collapses a storm of failures into one notification per org', async () => {
    mockSender.send.resolves({ status: 'permanent', error: '550 rejected' });

    // 50 recipients failing back-to-back, as in a bad-SMTP bulk import.
    for (let i = 0; i < 50; i++) {
      await deliver(payload({ mail: { ...payload().mail, sendEmailTo: [`u${i}@example.com`] } }));
    }

    expect(mockSender.send.callCount).to.equal(50);
    expect(mockNotificationProducer.publishEvent.callCount).to.equal(1);
  });

  it('reports the suppressed failures when the window closes', async () => {
    mockSender.send.resolves({ status: 'permanent', error: '550 rejected' });

    for (const to of ['a@x.co', 'b@x.co', 'c@x.co']) {
      await deliver(payload({ mail: { ...payload().mail, sendEmailTo: [to] } }));
    }
    expect(mockNotificationProducer.publishEvent.callCount).to.equal(1);

    await clock.tickAsync(5 * 60_000 + 1_000);

    expect(mockNotificationProducer.publishEvent.callCount).to.equal(2);
    const summary = mockNotificationProducer.publishEvent.secondCall.args[0];
    expect(summary.payload.payload.suppressedFailures).to.equal(2);
    expect(summary.payload.payload.recipients).to.deep.equal(['b@x.co', 'c@x.co']);
    expect(summary.payload.message).to.contain('2 more');
  });

  it('sends no summary when nothing was suppressed, and starts a fresh window after', async () => {
    mockSender.send.resolves({ status: 'permanent', error: '550 rejected' });

    await deliver(payload());
    await clock.tickAsync(5 * 60_000 + 1_000);
    expect(mockNotificationProducer.publishEvent.callCount).to.equal(1);

    await deliver(payload());
    expect(mockNotificationProducer.publishEvent.callCount).to.equal(2);
  });

  it('throttles per org, not globally', async () => {
    mockSender.send.resolves({ status: 'permanent', error: '550 rejected' });

    await deliver(payload({ orgId: '507f1f77bcf86cd799439012' }));
    await deliver(payload({ orgId: '507f1f77bcf86cd799439099' }));

    // A noisy org must not silence a different one.
    expect(mockNotificationProducer.publishEvent.callCount).to.equal(2);
  });

  it('skips an event whose payload is not a mail job', async () => {
    let captured: any;
    mockConsumer.consume.callsFake(async (cb: any) => {
      captured = cb;
    });
    await consumer.consume(async () => {});
    await captured({ key: 'k', value: { nonsense: true } });

    expect(mockSender.send.called).to.be.false;
    expect(mockLogger.warn.called).to.be.true;
  });
});

describe('classifyMailError', () => {
  it('treats SMTP 5xx as permanent and 4xx as transient', () => {
    expect(classifyMailError({ responseCode: 550 })).to.equal('permanent');
    expect(classifyMailError({ responseCode: 421 })).to.equal('transient');
  });

  it('treats network-shaped codes as transient', () => {
    expect(classifyMailError({ code: 'ETIMEDOUT' })).to.equal('transient');
    expect(classifyMailError({ code: 'ECONNECTION' })).to.equal('transient');
  });

  it('treats rejection codes as permanent', () => {
    expect(classifyMailError({ code: 'EAUTH' })).to.equal('permanent');
    expect(classifyMailError({ code: 'EMESSAGE' })).to.equal('permanent');
  });

  it('defaults to transient when the error carries no usable signal', () => {
    expect(classifyMailError(null)).to.equal('transient');
    expect(classifyMailError({})).to.equal('transient');
    expect(classifyMailError({ code: 'EPIPE' })).to.equal('transient');
    expect(classifyMailError({ code: 'EAI_NODATA' })).to.equal('transient');
  });
});

import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import nodemailer from 'nodemailer';
import { MailSenderService } from '../../../../src/modules/mail/services/mail.sender.service';
import { MailModel } from '../../../../src/modules/mail/schema/mailInfo.schema';

describe('MailSenderService', () => {
  let mockLogger: any;
  let sendMailStub: sinon.SinonStub;

  const smtp = {
    host: 'mailpit',
    port: 1025,
    fromEmail: 'no-reply@example.com',
  } as any;

  const body = {
    emailTemplateType: 'appuserInvite',
    subject: 'Invite',
    sendEmailTo: ['user@example.com'],
    templateData: { invitee: 'Admin', orgName: 'Corp', link: 'http://x/y' },
  } as any;

  beforeEach(() => {
    mockLogger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    };
    sendMailStub = sinon.stub().resolves({ messageId: 'm1' });
    sinon.stub(nodemailer, 'createTransport').returns({
      sendMail: sendMailStub,
      close: sinon.stub(),
    } as any);
    sinon.stub(MailModel.prototype, 'save').resolves({} as any);
  });

  afterEach(() => sinon.restore());

  it('reads SMTP config through the provider so a rebind is picked up', () => {
    // The service is a singleton but AppConfig is rebound when an admin
    // updates SMTP settings; capturing the config would go stale.
    let current: any = { smtp: { host: 'old-host', port: 25, fromEmail: 'a@b.c' } };
    const sender = new MailSenderService(() => current, mockLogger);

    expect(sender.getSmtpConfig()?.host).to.equal('old-host');

    current = { smtp: { host: 'new-host', port: 587, fromEmail: 'a@b.c' } };
    expect(sender.getSmtpConfig()?.host).to.equal('new-host');
  });

  it('returns null when SMTP has not been configured', () => {
    const sender = new MailSenderService(() => ({}) as any, mockLogger);
    expect(sender.getSmtpConfig()).to.equal(null);
  });

  it('reports sent on a successful delivery', async () => {
    const sender = new MailSenderService(() => ({ smtp }) as any, mockLogger);
    const result = await sender.send(body, smtp);

    expect(result.status).to.equal('sent');
    expect(sendMailStub.calledOnce).to.be.true;
  });

  it('still reports sent when only the audit record fails to save', async () => {
    // Otherwise the consumer would retry an email that was already delivered.
    (MailModel.prototype.save as sinon.SinonStub).rejects(new Error('mongo down'));
    const sender = new MailSenderService(() => ({ smtp }) as any, mockLogger);

    const result = await sender.send(body, smtp);

    expect(result.status).to.equal('sent');
    expect(mockLogger.error.called).to.be.true;
  });

  it('classifies an SMTP 5xx rejection as permanent', async () => {
    sendMailStub.rejects(Object.assign(new Error('550 rejected'), {
      responseCode: 550,
    }));
    const sender = new MailSenderService(() => ({ smtp }) as any, mockLogger);

    expect((await sender.send(body, smtp)).status).to.equal('permanent');
  });

  it('classifies a connection timeout as transient', async () => {
    sendMailStub.rejects(Object.assign(new Error('timed out'), {
      code: 'ETIMEDOUT',
    }));
    const sender = new MailSenderService(() => ({ smtp }) as any, mockLogger);

    expect((await sender.send(body, smtp)).status).to.equal('transient');
  });

  it('treats an unknown template as permanent rather than retrying it', async () => {
    const sender = new MailSenderService(() => ({ smtp }) as any, mockLogger);
    const result = await sender.send(
      { ...body, emailTemplateType: 'nope' },
      smtp,
    );

    expect(result.status).to.equal('permanent');
    expect(sendMailStub.called).to.be.false;
  });

  it('reuses one pooled transporter with every SMTP stage bounded', async () => {
    const sender = new MailSenderService(() => ({ smtp }) as any, mockLogger);
    await sender.send(body, smtp);
    await sender.send(body, smtp);

    const createTransport = nodemailer.createTransport as sinon.SinonStub;
    // Reused: 1000 invites must not mean 1000 connections and handshakes.
    expect(createTransport.calledOnce).to.be.true;

    const opts = createTransport.firstCall.args[0];
    expect(opts.pool, 'pool').to.be.true;
    expect(opts.dnsTimeout, 'dnsTimeout').to.equal(30_000);
    expect(opts.connectionTimeout, 'connectionTimeout').to.equal(30_000);
    expect(opts.greetingTimeout, 'greetingTimeout').to.equal(30_000);
    expect(opts.socketTimeout, 'socketTimeout').to.equal(60_000);
    // No username configured, so no credentials are offered at all.
    expect(opts.auth, 'auth').to.equal(undefined);
  });

  it('abandons a stalled send at the deadline and drops the pool', async () => {
    const clock = sinon.useFakeTimers();
    const closeStub = sinon.stub();
    try {
      (nodemailer.createTransport as sinon.SinonStub).returns({
        sendMail: sinon.stub().returns(new Promise(() => {})),
        close: closeStub,
      } as any);
      const sender = new MailSenderService(() => ({ smtp }) as any, mockLogger);

      const promise = sender.send(body, smtp);
      await clock.tickAsync(120_000 + 1);
      const result = await promise;

      // socketTimeout only measures inactivity, so without this an unbounded
      // send would wedge the consumer and stop all later mail.
      expect(result.status).to.equal('transient');
      expect(String((result as { error?: string }).error)).to.contain('deadline');
      // The stuck connection must not be handed to the next message.
      expect(closeStub.calledOnce).to.be.true;
    } finally {
      clock.restore();
    }
  });
});

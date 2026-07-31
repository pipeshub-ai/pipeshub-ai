import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import nodemailer from 'nodemailer';
import { MailController } from '../../../../src/modules/mail/controller/mail.controller';
import { MailModel } from '../../../../src/modules/mail/schema/mailInfo.schema';


describe('MailController - SMTP timeouts', () => {
  let createTransportStub: sinon.SinonStub;
  let sendMailStub: sinon.SinonStub;

  // The axios timeout in MailService that wraps this call.
  const CALLER_HTTP_TIMEOUT_MS = 30_000;

  const smtp: any = {
    host: 'smtp.example.com',
    port: 587,
    username: 'user',
    password: 'pass',
    fromEmail: 'no-reply@example.com',
  };

  const body: any = {
    emailTemplateType: 'appuserInvite',
    subject: 'Invite',
    sendEmailTo: ['user@example.com'],
    templateData: { invitee: 'Admin', orgName: 'Corp', link: 'http://x/y' },
  };

  beforeEach(() => {
    sendMailStub = sinon.stub().resolves({ messageId: 'm1' });
    createTransportStub = sinon
      .stub(nodemailer, 'createTransport')
      .returns({ sendMail: sendMailStub, close: sinon.stub() } as any);
    sinon.stub(MailModel.prototype, 'save').resolves({} as any);
  });

  afterEach(() => sinon.restore());

  const build = () =>
    new MailController({ smtp } as any, {
      debug: sinon.stub(), info: sinon.stub(),
      warn: sinon.stub(), error: sinon.stub(),
    } as any);

  it('bounds every SMTP stage below the caller timeout, keeping credentials', async () => {
    await build().emailSender(body, smtp);

    const opts = createTransportStub.firstCall.args[0];
    expect(opts.dnsTimeout, 'dnsTimeout').to.equal(10_000);
    expect(opts.connectionTimeout, 'connectionTimeout').to.equal(10_000);
    expect(opts.greetingTimeout, 'greetingTimeout').to.equal(10_000);
    expect(opts.socketTimeout, 'socketTimeout').to.equal(20_000);

    for (const k of [
      'dnsTimeout',
      'connectionTimeout',
      'greetingTimeout',
      'socketTimeout',
    ]) {
      expect(opts[k], k).to.be.below(CALLER_HTTP_TIMEOUT_MS);
    }
    expect(opts.auth).to.deep.equal({ user: 'user', pass: 'pass' });
  });

  it('aborts a stalled send before the caller times out, and closes the transport', async () => {
    const clock = sinon.useFakeTimers();
    const closeStub = sinon.stub();
    try {
      // socketTimeout only measures inactivity, so a trickling server resets it
      // indefinitely; only the end-to-end deadline bounds this.
      createTransportStub.returns({
        sendMail: sinon.stub().returns(new Promise(() => {})),
        close: closeStub,
      } as any);

      const promise = build().emailSender(body, smtp);
      await clock.tickAsync(CALLER_HTTP_TIMEOUT_MS - 1);
      const result = await promise;

      expect(result.status).to.be.false;
      expect(String(result.data)).to.contain('deadline');
      // A stuck socket must not be left behind.
      expect(closeStub.calledOnce).to.be.true;
    } finally {
      clock.restore();
    }
  });
});

import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import nodemailer from 'nodemailer';
import { MailController } from '../../../../src/modules/mail/controller/mail.controller';
import { MailModel } from '../../../../src/modules/mail/schema/mailInfo.schema';

/**
 * Nodemailer's defaults (120s connect, 30s DNS, 10min socket) outlive the
 * callers' own timeouts, so an unreachable SMTP host holds an HTTP request
 * open instead of failing. These assert the bounds stay in place.
 */
describe('MailController - SMTP timeouts', () => {
  let createTransportStub: sinon.SinonStub;
  let sendMailStub: sinon.SinonStub;

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
      .returns({ sendMail: sendMailStub } as any);
    sinon.stub(MailModel.prototype, 'save').resolves({} as any);
  });

  afterEach(() => sinon.restore());

  const build = () =>
    new MailController({ smtp } as any, {
      debug: sinon.stub(), info: sinon.stub(),
      warn: sinon.stub(), error: sinon.stub(),
    } as any);

  it('bounds every SMTP stage well inside the caller timeout', async () => {
    await build().emailSender(body, smtp);

    expect(createTransportStub.calledOnce).to.be.true;
    const opts = createTransportStub.firstCall.args[0];

    expect(opts.dnsTimeout, 'dnsTimeout').to.equal(10_000);
    expect(opts.connectionTimeout, 'connectionTimeout').to.equal(10_000);
    expect(opts.greetingTimeout, 'greetingTimeout').to.equal(10_000);
    expect(opts.socketTimeout, 'socketTimeout').to.equal(20_000);
  });

  it('keeps every stage under the 30s mail-backend request timeout', async () => {
    await build().emailSender(body, smtp);
    const opts = createTransportStub.firstCall.args[0];

    for (const key of [
      'dnsTimeout',
      'connectionTimeout',
      'greetingTimeout',
      'socketTimeout',
    ]) {
      expect(opts[key], key).to.be.a('number').and.to.be.below(30_000);
    }
  });

  it('still sends credentials when they are configured', async () => {
    await build().emailSender(body, smtp);
    const opts = createTransportStub.firstCall.args[0];

    expect(opts.auth).to.deep.equal({ user: 'user', pass: 'pass' });
  });
});

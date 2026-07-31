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
});

import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import nodemailer from 'nodemailer';
import { MailSenderService } from '../../../../src/modules/mail/services/mail.sender.service';
import { MailModel } from '../../../../src/modules/mail/schema/mailInfo.schema';

describe('MailSenderService extension points', () => {
  const smtp = { host: 'mailpit', port: 1025, fromEmail: 'no-reply@example.com' } as any;
  const logger: any = { debug: sinon.stub(), info: sinon.stub(), error: sinon.stub(), warn: sinon.stub() };
  let sendMail: sinon.SinonStub;

  beforeEach(() => {
    sendMail = sinon.stub().resolves({ messageId: 'm1' });
    sinon.stub(nodemailer, 'createTransport').returns({ sendMail, close: sinon.stub() } as any);
    sinon.stub(MailModel.prototype, 'save').resolves({} as any);
  });

  afterEach(() => sinon.restore());

  it('reads the global SMTP settings whatever org is asked for', async () => {
    const sender = new MailSenderService(() => ({ smtp }) as any, logger);

    expect(await sender.getSmtpConfig('org-1')).to.equal(smtp);
  });

  it('renders the body through renderContent, which a subclass can replace', async () => {
    class CustomSender extends MailSenderService {
      protected override renderContent(type: string): string {
        return `<p>custom ${type}</p>`;
      }
    }
    const sender = new CustomSender(() => ({ smtp }) as any, logger);

    const result = await sender.send(
      { emailTemplateType: 'customTemplate', subject: 'Hi', sendEmailTo: ['u@example.com'], templateData: {} } as any,
      smtp,
    );

    expect(result.status).to.equal('sent');
    expect(sendMail.firstCall.args[0].html).to.equal('<p>custom customTemplate</p>');
  });
});

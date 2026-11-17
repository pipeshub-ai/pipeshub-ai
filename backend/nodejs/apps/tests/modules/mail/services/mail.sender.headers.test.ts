import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import nodemailer from 'nodemailer';
import {
  MailSenderService,
  buildMailHeaders,
} from '../../../../src/modules/mail/services/mail.sender.service';

describe('MailSenderService custom headers', () => {
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
  const messageId = '<abc123.def@mail.example.com>';
  let sendMail: sinon.SinonStub;
  let sender: MailSenderService;

  beforeEach(() => {
    const logger: any = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    };
    sendMail = sinon.stub().resolves({ messageId: 'm1' });
    sinon
      .stub(nodemailer, 'createTransport')
      .returns({ sendMail, close: sinon.stub() } as any);
    sender = new MailSenderService(() => ({ smtp }) as any, logger);
    (sender as any).recordAudit = () => undefined;
  });

  afterEach(() => sinon.restore());

  it('passes the Message-ID and the allow-listed headers to the transport', async () => {
    const result = await sender.send(body, smtp, undefined, {
      'Message-ID': messageId,
      'List-Unsubscribe': '<https://app.example.com/unsubscribe?t=abc>',
      'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
      'Auto-Submitted': 'auto-generated',
    });
    expect(result.status).to.equal('sent');
    const message = sendMail.firstCall.args[0];
    expect(message.messageId).to.equal(messageId);
    expect(message.headers).to.deep.equal({
      'List-Unsubscribe': '<https://app.example.com/unsubscribe?t=abc>',
      'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
      'Auto-Submitted': 'auto-generated',
    });
  });

  it('changes nothing when no extra headers are given', async () => {
    await sender.send(body, smtp);
    const message = sendMail.firstCall.args[0];
    expect(message).to.not.have.property('messageId');
    expect(message).to.not.have.property('headers');
  });

  it('treats an allow-listed header left undefined as omitted, and still sends', async () => {
    const result = await sender.send(body, smtp, undefined, {
      'Message-ID': messageId,
      'List-Unsubscribe': undefined,
    });
    expect(result.status).to.equal('sent');
    const message = sendMail.firstCall.args[0];
    expect(message.messageId).to.equal(messageId);
    expect(message).to.not.have.property('headers');
  });

  it('still refuses null and an empty value', async () => {
    for (const value of [null, '']) {
      sendMail.resetHistory();
      const result = await sender.send(body, smtp, undefined, {
        'List-Unsubscribe': value,
      } as any);
      expect(result.status).to.equal('permanent');
      expect(sendMail.called).to.equal(false);
    }
  });

  it('refuses a header that is not on the list, and sends nothing', async () => {
    const result = await sender.send(body, smtp, undefined, {
      Bcc: 'x@example.com',
    } as any);
    expect(result.status).to.equal('permanent');
    expect(sendMail.called).to.equal(false);
  });

  it('refuses CR or LF in any value, and a malformed Message-ID', async () => {
    for (const bad of [
      { 'List-Unsubscribe': '<https://a>\r\nBcc: x@example.com' },
      { 'Auto-Submitted': 'auto\nBcc: x@example.com' },
      { 'Message-ID': '<a@b>\r\nBcc: x@example.com' },
      { 'Message-ID': 'no-brackets@example.com' },
      { 'Message-ID': '<no-at-sign>' },
      { 'List-Unsubscribe': '' },
    ]) {
      const result = await sender.send(body, smtp, undefined, bad as any);
      expect(result.status, JSON.stringify(bad)).to.equal('permanent');
    }
    expect(sendMail.called).to.equal(false);
  });

  it('refuses a Message-ID with an empty dot-separated part on either side', async () => {
    for (const id of [
      '<a..b@example.com>',
      '<.a@example.com>',
      '<a.@example.com>',
      '<a@example..com>',
      '<a@.example.com>',
      '<a@example.com.>',
      '<.@example.com>',
      '<a@.>',
    ]) {
      const result = await sender.send(body, smtp, undefined, {
        'Message-ID': id,
      });
      expect(result.status, id).to.equal('permanent');
    }
    expect(sendMail.called).to.equal(false);
  });

  it('accepts dotted Message-IDs within the length bounds, and refuses one past them', () => {
    for (const id of [
      '<a.b.c@mail.example.com>',
      '<x@localhost>',
      `<${'a'.repeat(200)}@${'b'.repeat(253)}>`,
    ]) {
      expect(buildMailHeaders({ 'Message-ID': id }), id).to.deep.equal({
        messageId: id,
      });
    }
    for (const id of [
      `<${'a'.repeat(201)}@example.com>`,
      `<a@${'b'.repeat(254)}>`,
    ]) {
      expect(buildMailHeaders({ 'Message-ID': id }), id).to.have.property(
        'error',
      );
    }
  });

  it('builds nothing for an absent set', () => {
    expect(buildMailHeaders(undefined)).to.deep.equal({});
    expect(buildMailHeaders({})).to.deep.equal({});
  });
});

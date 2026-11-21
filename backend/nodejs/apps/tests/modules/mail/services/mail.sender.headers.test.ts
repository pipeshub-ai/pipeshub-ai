import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import nodemailer from 'nodemailer';
import {
  MAX_MESSAGE_DATE_AGE_MS,
  MAX_MESSAGE_DATE_AHEAD_MS,
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

describe('MailSenderService message date', () => {
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
  const HOUR = 60 * 60 * 1000;
  let sendMail: sinon.SinonStub;
  let sender: MailSenderService;
  const newSender = () => {
    const logger: any = {
      debug: sinon.stub(),
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
    };
    const s = new MailSenderService(() => ({ smtp }) as any, logger);
    (s as any).recordAudit = () => undefined;
    return s;
  };

  beforeEach(() => {
    sendMail = sinon.stub().resolves({ messageId: 'm1' });
    sinon
      .stub(nodemailer, 'createTransport')
      .returns({ sendMail, close: sinon.stub() } as any);
    sender = newSender();
  });

  afterEach(() => sinon.restore());

  it('passes a caller-set date to the transport, and leaves the other headers as they were', async () => {
    const date = new Date(Date.now() - 3 * HOUR);
    const result = await sender.send(body, smtp, undefined, {
      'Message-ID': messageId,
      'List-Unsubscribe': '<https://app.example.com/unsubscribe?t=abc>',
      'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
      'Auto-Submitted': 'auto-generated',
      Date: date,
    });
    expect(result.status).to.equal('sent');
    const message = sendMail.firstCall.args[0];
    expect(message.date).to.be.instanceOf(Date);
    expect(message.date.getTime()).to.equal(date.getTime());
    expect(message.messageId).to.equal(messageId);
    expect(message.headers).to.deep.equal({
      'List-Unsubscribe': '<https://app.example.com/unsubscribe?t=abc>',
      'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click',
      'Auto-Submitted': 'auto-generated',
    });
  });

  it('leaves the date to the transport when none is set', async () => {
    for (const extra of [
      undefined,
      { 'Message-ID': messageId },
      { 'Message-ID': messageId, Date: undefined },
    ]) {
      sendMail.resetHistory();
      const result = await sender.send(body, smtp, undefined, extra);
      expect(result.status, JSON.stringify(extra)).to.equal('sent');
      expect(sendMail.firstCall.args[0]).to.not.have.property('date');
    }
  });

  it('refuses a date that is not a valid Date, and sends nothing', async () => {
    const invalid: Array<[string, unknown]> = [
      ['an invalid Date', new Date(Number.NaN)],
      ['an unparsable Date', new Date('not a date')],
      ['a date string', new Date().toUTCString()],
      ['a timestamp', Date.now()],
      ['null', null],
      ['an object', {}],
    ];
    for (const [label, bad] of invalid) {
      const result = await sender.send(body, smtp, undefined, {
        Date: bad,
      } as any);
      expect(result.status, label).to.equal('permanent');
      expect(result).to.have.property('error', 'Date is not valid');
    }
    expect(sendMail.called).to.equal(false);
  });

  it('refuses a date further in the past or the future than the bounds allow, and sends nothing', async () => {
    for (const bad of [
      new Date(Date.now() - MAX_MESSAGE_DATE_AGE_MS - HOUR),
      new Date(Date.now() + MAX_MESSAGE_DATE_AHEAD_MS + HOUR),
      new Date(0),
      new Date(8.64e15),
    ]) {
      const result = await sender.send(body, smtp, undefined, { Date: bad });
      expect(result.status, bad.toISOString()).to.equal('permanent');
      expect(result).to.have.property(
        'error',
        'Date is too far from the time of sending',
      );
    }
    expect(sendMail.called).to.equal(false);
  });

  it('accepts a date exactly at either bound, and refuses one a millisecond past it', () => {
    const now = Date.UTC(2026, 0, 5, 10);
    const at = (offset: number) =>
      buildMailHeaders({ Date: new Date(now + offset) }, now);
    expect(at(-MAX_MESSAGE_DATE_AGE_MS)).to.deep.equal({
      date: new Date(now - MAX_MESSAGE_DATE_AGE_MS),
    });
    expect(at(MAX_MESSAGE_DATE_AHEAD_MS)).to.deep.equal({
      date: new Date(now + MAX_MESSAGE_DATE_AHEAD_MS),
    });
    expect(at(0)).to.deep.equal({ date: new Date(now) });
    expect(at(-MAX_MESSAGE_DATE_AGE_MS - 1)).to.have.property('error');
    expect(at(MAX_MESSAGE_DATE_AHEAD_MS + 1)).to.have.property('error');
  });

  it('writes the caller-set date as the Date header of the composed message', async () => {
    sinon.restore();
    const stream = nodemailer.createTransport({
      streamTransport: true,
      buffer: true,
    });
    const compose = (
      stream.sendMail as (message: unknown) => Promise<{ message: Buffer }>
    ).bind(stream);
    const composed: Array<Promise<{ message: Buffer }>> = [];
    sinon.stub(stream, 'sendMail').callsFake(((message: unknown) => {
      const sent = compose(message);
      composed.push(sent);
      return sent;
    }) as never);
    sinon.stub(nodemailer, 'createTransport').returns(stream as any);
    const date = new Date(Math.floor((Date.now() - 2 * HOUR) / 1000) * 1000);
    const result = await newSender().send(body, smtp, undefined, {
      Date: date,
    });
    expect(result.status).to.equal('sent');
    expect(composed).to.have.length(1);
    const info = await composed[0]!;
    const header = /^Date: (.+)$/m.exec(info.message.toString())?.[1];
    expect(header, 'Date header').to.be.a('string');
    expect(new Date(header!).getTime()).to.equal(date.getTime());
  });
});

/**
 * What the mail and notification modules write to the log (docs/logging.md "Mail delivery (Node)"): one line per mail job or
 * failed request, classed by `mailErrorClass`, and never a recipient, a sign-in code, the SMTP server's reply or a message value.
 * Every record goes through the real Logger and is rendered by the real console format; the transport and broker are fakes.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express from 'express';
import http from 'http';
import winston from 'winston';
import nodemailer from 'nodemailer';
import type { AddressInfo } from 'net';
import { Logger } from '../../../src/libs/services/logger.service';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { requestContextMiddleware } from '../../../src/libs/middlewares/request.context';
import { accessLog } from '../../../src/libs/middlewares/access-log.middleware';
import { runWithRequestContext } from '../../../src/libs/context/request-context';
import { captureLogRecords, inRequestContext, newRequestId, recordsOf } from '../../helpers/log-capture';
import { ValidationError } from '../../../src/libs/errors/validation.error';
import { MailController } from '../../../src/modules/mail/controller/mail.controller';
import { MailSenderService } from '../../../src/modules/mail/services/mail.sender.service';
import { MailConsumer } from '../../../src/modules/mail/services/mail.consumer';
import { MailProducer } from '../../../src/modules/mail/services/mail.producer';
import {
  classifyMailError,
  mailErrorClass,
  SMTP_DEADLINE_ERROR_CODE,
} from '../../../src/modules/mail/types/mail-event.types';
import { NotificationConsumer } from '../../../src/modules/notification/service/notification.consumer';
import { NotificationService } from '../../../src/modules/notification/service/notification.service';
import * as notificationRepo from '../../../src/libs/repositories/notification';
import { setMailLogRepository } from '../../../src/libs/repositories/mail-log';
import type { AppConfig } from '../../../src/modules/tokens_manager/config/config';

const MESSAGE = Symbol.for('message');
type Row = Record<string, any>;

const ORG = '64b000000000000000000a01';
const ADMIN_ID = '64b0000000000000000000a1';
const RECIPIENT = 'alice.recipient@example.com';
const SENDER = 'noreply@example.com';
const OTP = '493817';
const SMTP_550 = `550 5.1.1 <${RECIPIENT}>: Recipient address rejected: User unknown`;
const SMTP_421 = `421 4.7.0 Try again later, closing connection for <${RECIPIENT}>`;
const PRIVATE = [RECIPIENT, OTP, 'Recipient address rejected', 'Try again later', 'Alice Recipient'];

function smtpError(text: string, responseCode: number, command = 'RCPT TO', code = 'EENVELOPE'): Error {
  return Object.assign(new Error(`Can't send mail - all recipients were rejected: ${text}`), {
    code,
    responseCode,
    response: text,
    command,
  });
}
const err = (fields: Row, message = 'x'): Error => Object.assign(new Error(message), fields);

const smtp = { host: 'smtp.example.com', port: 587, username: 'mailer', password: 'smtp-pass-x', fromEmail: SENDER };
const appConfig = () => ({ smtp, frontendUrl: 'http://fe.test', scopedJwtSecret: 's' }) as unknown as AppConfig;
const otpMail = (extra: Row = {}) => ({
  orgId: ORG,
  emailTemplateType: 'loginWithOTP',
  sendEmailTo: [RECIPIENT],
  subject: 'Your sign-in code',
  templateData: { otp: OTP, name: 'Alice Recipient', productName: 'PipesHub' },
  ...extra,
});

describe('mail and notification logging', function () {
  this.timeout(20000);
  let records: Row[];
  // The request (or broker message) under test; a line from any other context is not this test's.
  let requestId: string;
  const root = () => (Logger.getInstance() as any).logger as winston.Logger;
  const render = (r: Row): string => {
    const fmt = root().transports.find((t) => t instanceof winston.transports.Console)!.format!;
    const first = root().format.transform({ ...r }, {});
    const out = first ? fmt.transform(first as any, {}) : undefined;
    return out ? String((out as any)[MESSAGE]) : '';
  };
  const level = (r: Row) => {
    const l = String(r.level).toLowerCase();
    return l === 'warning' ? 'warn' : l;
  };
  // A typed field is on the record; any other key (`smtp_code`, `email_template_type`) is in its metadata.
  const fields = (r: Row): Row => ({ ...(r.metadata ?? {}), ...r });
  const mine = () => recordsOf(records, requestId);
  const atLeastInfo = () => mine().filter((r) => level(r) !== 'debug');
  const named = (message: string) => mine().filter((r) => r.message === message);
  const assertTextFree = () => {
    const lines = records.map(render);
    for (const value of PRIVATE) {
      const hit = lines.find((l) => l.includes(value));
      expect(hit, `${value} was logged`).to.equal(undefined);
    }
  };

  beforeEach(() => {
    records = captureLogRecords();
    requestId = newRequestId('mail-logging');
  });
  let recordMailLog: sinon.SinonStub;
  beforeEach(() => {
    // A sent mail's audit write is not awaited: a real store here could log into this or a later test's capture.
    recordMailLog = sinon.stub().resolves();
    setMailLogRepository({ record: recordMailLog } as any);
  });
  afterEach(() => {
    sinon.restore();
    setMailLogRepository();
  });

  describe('mailErrorClass', () => {
    const cases: Array<[string, unknown, string]> = [
      ['450 greylisting on RCPT (EENVELOPE) is temporary', smtpError('450 4.2.0 Greylisted', 450), 'unavailable'],
      ['451 on MAIL FROM is temporary', smtpError('451 try later', 451, 'MAIL FROM'), 'unavailable'],
      ['421 is temporary', smtpError(SMTP_421, 421), 'unavailable'],
      ['EAUTH 454 is temporary', err({ code: 'EAUTH', responseCode: 454, command: 'AUTH PLAIN' }), 'unavailable'],
      ['550 on RCPT is the recipient', smtpError(SMTP_550, 550), 'bad_request'],
      ['554 on DATA is the message', smtpError('554 spam', 554, 'DATA'), 'bad_request'],
      ['550 relaying denied on MAIL FROM is our setup', smtpError('550 relaying denied', 550, 'MAIL FROM'), 'misconfigured'],
      ['553 sender rejected on MAIL FROM is our setup', smtpError('553 sender rejected', 553, 'MAIL FROM'), 'misconfigured'],
      ['530 auth required', err({ code: 'EENVELOPE', responseCode: 530, command: 'MAIL FROM' }), 'misconfigured'],
      ['EAUTH 535', err({ code: 'EAUTH', responseCode: 535, command: 'AUTH PLAIN' }), 'misconfigured'],
      ['EAUTH without a reply', err({ code: 'EAUTH' }), 'misconfigured'],
      ['nodemailer Invalid sender', err({ code: 'EENVELOPE', command: 'API' }, 'Invalid sender "x"'), 'misconfigured'],
      ['no recipients', err({ code: 'EENVELOPE', command: 'API' }, 'No recipients defined'), 'bad_request'],
      ['message too large', err({ code: 'EMESSAGE', command: 'MAIL FROM' }, 'Message size larger than allowed 100'), 'bad_request'],
      ['5xx to another command', err({ code: 'EPROTOCOL', responseCode: 500, command: 'EHLO' }), 'upstream_5xx'],
      ['EPROTOCOL without a reply', err({ code: 'EPROTOCOL' }), 'upstream_5xx'],
      ['refused connection', err({ code: 'ECONNREFUSED' }), 'unavailable'],
      ['ESOCKET wrapping ECONNREFUSED', err({ code: 'ESOCKET', originalCode: 'ECONNREFUSED' }), 'unavailable'],
      ['DNS failure', err({ code: 'EDNS' }), 'unavailable'],
      ['an expired TLS certificate', err({ code: 'ESOCKET', originalCode: 'CERT_HAS_EXPIRED' }), 'misconfigured'],
      ['a failed TLS upgrade (ETLS)', err({ code: 'ETLS', command: 'CONN' }, 'Error initiating TLS'), 'misconfigured'],
      ['ETIMEDOUT', err({ code: 'ETIMEDOUT' }), 'timeout'],
      ['the send deadline', err({ code: SMTP_DEADLINE_ERROR_CODE }), 'timeout'],
      ['a greeting timeout', err({ code: 'ECONNECTION', timeoutType: 'GREETING_TIMEOUT' }), 'timeout'],
      ['EMESSAGE (our message build)', err({ code: 'EMESSAGE' }), 'internal'],
      ['an unknown template (a string)', 'Unknown Template', 'internal'],
      ['null', null, 'internal'],
      ['an empty object', {}, 'internal'],
    ];
    for (const [name, error, expected] of cases) {
      it(`${name} → ${expected}`, () => {
        expect(mailErrorClass(error)).to.equal(expected);
      });
    }

    it('never calls a failure the retry classifier retries the message’s own fault', () => {
      for (const [name, error] of cases) {
        if (typeof error === 'string' || error === null) continue;
        if (classifyMailError(error) === 'transient') {
          expect(mailErrorClass(error), name).to.not.equal('bad_request');
        }
      }
    });
  });

  describe('mail consumer: one mail.send.end per job', () => {
    const consumerOver = (sendMail: sinon.SinonStub) => {
      sinon.stub(nodemailer, 'createTransport').returns({ sendMail, close: () => undefined } as any);
      const logger = Logger.getInstance({ service: 'mailService' });
      let handle: ((m: any) => Promise<void>) | undefined;
      const broker = { isConnected: () => true, consume: async (h: any) => { handle = h; } } as any;
      const published: Row[] = [];
      const producer = { start: async () => undefined, publishEvent: async (e: Row) => { published.push(e); } } as any;
      const consumer = new MailConsumer(broker, logger, new MailSenderService(appConfig, logger), producer, appConfig);
      sinon.stub(MailConsumer.prototype as any, 'sleep').resolves();
      const handled = sinon.stub().resolves();
      return {
        published,
        handled,
        deliver: async (value: Row) => {
          await consumer.consume(handled);
          await inRequestContext(requestId, () => handle!({ value }));
        },
      };
    };
    const theEnd = () => {
      const ends = named('mail.send.end');
      expect(ends).to.have.length(1);
      expect(atLeastInfo()).to.have.length(1);
      return ends[0];
    };

    it('a recipient rejected on RCPT: one WARNING, bad_request, the reply code and no text', async () => {
      const { deliver, published } = consumerOver(sinon.stub().rejects(smtpError(SMTP_550, 550)));
      await deliver({ orgId: ORG, mail: otpMail() });
      const end = theEnd();
      expect(level(end)).to.equal('warn');
      expect(fields(end)).to.include({ outcome: 'failure', reason: 'permanent', error_class: 'bad_request', smtp_code: 550, attempt: 1, email_template_type: 'loginWithOTP' });
      assertTextFree();
      // the admin notification still says what failed (product text, not a log line)
      expect(JSON.stringify(published)).to.include(RECIPIENT);
    });

    it('a temporary refusal 4 times: 3 DEBUG retries and one WARNING, unavailable', async () => {
      const { deliver } = consumerOver(sinon.stub().rejects(smtpError(SMTP_421, 421)));
      await deliver({ orgId: ORG, mail: otpMail() });
      const end = theEnd();
      expect(fields(end)).to.include({ outcome: 'failure', reason: 'attempts_exhausted', error_class: 'unavailable', smtp_code: 421, attempt: 4 });
      expect(named('mail.send.retry').map(level)).to.deep.equal(['debug', 'debug', 'debug']);
      assertTextFree();
    });

    it('greylisting (450, EENVELOPE) is retried and reported as unavailable', async () => {
      const { deliver } = consumerOver(sinon.stub().rejects(smtpError('450 4.2.0 Greylisted', 450)));
      await deliver({ orgId: ORG, mail: otpMail() });
      expect(theEnd()).to.include({ error_class: 'unavailable', attempt: 4 });
    });

    it('our sender refused on MAIL FROM: one ERROR, misconfigured, not retried', async () => {
      const { deliver } = consumerOver(sinon.stub().rejects(smtpError(`553 5.7.1 <${SENDER}>: Sender address rejected`, 553, 'MAIL FROM')));
      await deliver({ orgId: ORG, mail: otpMail() });
      const end = theEnd();
      expect(level(end)).to.equal('error');
      expect(end).to.include({ outcome: 'failure', reason: 'permanent', error_class: 'misconfigured', attempt: 1 });
      expect(records.map(render).join('\n')).to.not.include('Sender address rejected');
    });

    it('a refused login: one ERROR, misconfigured', async () => {
      const { deliver } = consumerOver(sinon.stub().rejects(err({ code: 'EAUTH', responseCode: 535, command: 'AUTH PLAIN' }, 'Invalid login: 535 Authentication failed')));
      await deliver({ orgId: ORG, mail: otpMail() });
      const end = theEnd();
      expect(level(end)).to.equal('error');
      expect(end.error_class).to.equal('misconfigured');
    });

    it('a refused connection: retried, then one WARNING, unavailable', async () => {
      const { deliver } = consumerOver(sinon.stub().rejects(err({ code: 'ESOCKET', originalCode: 'ECONNREFUSED' }, 'connect ECONNREFUSED 10.0.0.9:587')));
      await deliver({ orgId: ORG, mail: otpMail() });
      const end = theEnd();
      expect(level(end)).to.equal('warn');
      expect(end.error_class).to.equal('unavailable');
    });

    it('a sent mail whose audit record fails to save: one line with the class only, never the store’s text', async () => {
      const { deliver } = consumerOver(sinon.stub().resolves({}));
      recordMailLog.rejects(new Error(`E11000 duplicate key error collection: maillogs index: to_1 dup key: { to: "${RECIPIENT}" }`));
      await deliver({ orgId: ORG, mail: otpMail() });
      for (let i = 0; i < 50 && named('Mail sent but audit record failed to save').length === 0; i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
      }
      const [line] = named('Mail sent but audit record failed to save');
      expect(line, 'audit failure line').to.exist;
      expect(fields(line)).to.include({ error_class: 'internal', error_type: 'Error', outcome: 'failure' });
      expect(named('mail.send.end').map(level)).to.deep.equal(['info']);
      assertTextFree();
      expect(records.map(render).join('\n')).to.not.include('E11000');
    });

    it('success after one temporary refusal: one INFO with attempt=2', async () => {
      const sendMail = sinon.stub();
      sendMail.onFirstCall().rejects(smtpError(SMTP_421, 421));
      sendMail.onSecondCall().resolves({});
      const { deliver } = consumerOver(sendMail);
      await deliver({ orgId: ORG, mail: otpMail() });
      const end = theEnd();
      expect(level(end)).to.equal('info');
      expect(end).to.include({ outcome: 'success', attempt: 2 });
      expect(end.error_class).to.equal(undefined);
      assertTextFree();
    });

    it('a payload without a template type: one WARNING, skipped, and no value (recipient, sign-in code)', async () => {
      const { deliver, handled } = consumerOver(sinon.stub().resolves({}));
      const mail = otpMail();
      delete (mail as Row).emailTemplateType;
      await deliver({ orgId: ORG, mail });
      const end = theEnd();
      expect(level(end)).to.equal('warn');
      expect(end).to.include({ outcome: 'skipped', reason: 'invalid_payload', attempt: 0 });
      assertTextFree();
      expect(handled.calledOnce).to.equal(true);
    });

    it('the delivery throws: one ERROR line, and the broker handler still runs (as on main)', async () => {
      const { deliver, handled, published } = consumerOver(sinon.stub().resolves({}));
      sinon.stub(MailConsumer.prototype as any, 'deliverInOrg').rejects(new TypeError('boom'));
      await deliver({ orgId: ORG, mail: otpMail() });
      const end = theEnd();
      expect(level(end)).to.equal('error');
      expect(end).to.include({ outcome: 'failure', reason: 'exception', error_class: 'internal' });
      expect(handled.calledOnce).to.equal(true);
      expect(published).to.have.length(0);
    });

    it('no orgId on the event: the end line, and the missing notification only at DEBUG', async () => {
      const { deliver, published } = consumerOver(sinon.stub().rejects(smtpError(SMTP_550, 550)));
      await deliver({ mail: otpMail({ orgId: undefined }) });
      theEnd();
      expect(published).to.have.length(0);
      expect(named('Mail failure not notified: no orgId on the event').map(level)).to.deep.equal(['debug']);
      assertTextFree();
    });
  });

  describe('the direct send route: the request’s one line', () => {
    async function post(sendMail: sinon.SinonStub, mail: Row = otpMail()) {
      sinon.stub(nodemailer, 'createTransport').returns({ sendMail, close: () => undefined } as any);
      const logger = Logger.getInstance({ service: 'mailService' });
      const controller = new MailController(appConfig(), logger, new MailSenderService(appConfig, logger));
      const app = express();
      app.use(requestContextMiddleware);
      app.use(accessLog());
      app.use(express.json());
      app.post('/api/v1/mail/emails/sendEmail', (req, res, next) => {
        void controller.sendMail(req, res, next);
      });
      app.use(ErrorMiddleware.handleError());
      const server = await new Promise<http.Server>((resolve) => {
        const listening = app.listen(0, '127.0.0.1', () => resolve(listening));
      });
      try {
        const res = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1/mail/emails/sendEmail`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', 'x-request-id': requestId },
          body: JSON.stringify(mail),
        });
        const body = (await res.json()) as Row;
        for (let i = 0; i < 50 && !mine().some((r) => r.message === 'http.request.end'); i += 1) {
          await new Promise((resolve) => setImmediate(resolve));
        }
        const errors = named('http.request.error');
        expect(errors).to.have.length(1);
        // nothing but the request's two lines at INFO or above
        expect(atLeastInfo().map((r) => r.message).sort()).to.deep.equal(['http.request.end', 'http.request.error']);
        return { status: res.status, body, line: errors[0] };
      } finally {
        server.closeAllConnections();
        await new Promise<void>((resolve) => server.close(() => resolve()));
      }
    }

    it('a recipient rejected: 500 as on main, classed upstream_5xx at WARNING, no text', async () => {
      const { status, body, line } = await post(sinon.stub().rejects(smtpError(SMTP_550, 550)));
      expect(status).to.equal(500);
      expect(body.error.message).to.include('send that email');
      expect(level(line)).to.equal('warn');
      expect(line.error_class).to.equal('upstream_5xx');
      assertTextFree();
    });

    it('a temporary refusal is unavailable', async () => {
      const { status, line } = await post(sinon.stub().rejects(smtpError(SMTP_421, 421)));
      expect(status).to.equal(500);
      expect(line.error_class).to.equal('unavailable');
    });

    it('our sender refused on MAIL FROM stays internal at ERROR, with no cause', async () => {
      const { status, line } = await post(sinon.stub().rejects(smtpError('553 sender rejected', 553, 'MAIL FROM')));
      expect(status).to.equal(500);
      expect(level(line)).to.equal('error');
      expect(line.error_class).to.equal('internal');
    });

    it('an unknown template stays internal at ERROR', async () => {
      const { status, line } = await post(sinon.stub().resolves({}), otpMail({ emailTemplateType: 'noSuchTemplate' }));
      expect(status).to.equal(500);
      expect(line.error_class).to.equal('internal');
    });

    it('a refused login and an unreachable server answer 502 as on main (upstream_5xx)', async () => {
      const auth = await post(sinon.stub().rejects(err({ code: 'EAUTH', responseCode: 535, command: 'AUTH PLAIN' })));
      expect(auth.status).to.equal(502);
      expect(auth.line.error_class).to.equal('upstream_5xx');
      sinon.restore();
      records = captureLogRecords();
      requestId = newRequestId('mail-logging');
      const down = await post(sinon.stub().rejects(err({ code: 'ESOCKET', originalCode: 'ECONNREFUSED' })));
      expect(down.status).to.equal(502);
      expect(down.line.error_class).to.equal('upstream_5xx');
    });
  });

  describe('notification consumer: facts, never the value', () => {
    const failureEvent = {
      orgId: ORG,
      type: 'mail.deliveryFailed',
      recipientUserIds: [ADMIN_ID],
      title: 'Email delivery failed',
      message: `Could not deliver "Your sign-in code" to ${RECIPIENT}: ${SMTP_550}`,
      severity: 'error',
      payload: { emailTemplateType: 'loginWithOTP', recipients: [RECIPIENT], error: SMTP_550 },
    };
    const deliver = async (value: Row) => {
      let handle: ((m: any) => Promise<void>) | undefined;
      const broker = { isConnected: () => true, consume: async (h: any) => { handle = h; } } as any;
      const consumer = new NotificationConsumer(broker, Logger.getInstance({ service: 'notification' }), { sendToUser: () => true } as any);
      await consumer.consume(async () => undefined);
      await inRequestContext(requestId, () => handle!({ value }));
    };

    it('a store failure: one ERROR with the type, the size and the error', async () => {
      sinon.stub(notificationRepo, 'getNotificationRepository').returns({
        insertMany: async () => { throw new Error('notification insert failed'); },
        // This build's consumer stores through insertNew (a row its recipient already has is not stored twice).
        insertNew: async () => { throw new Error('notification insert failed'); },
      } as any);
      await deliver(failureEvent);
      const [line] = atLeastInfo();
      expect(atLeastInfo()).to.have.length(1);
      expect(level(line)).to.equal('error');
      expect(render(line)).to.include('mail.deliveryFailed');
      expect(render(line)).to.include('notification insert failed');
      assertTextFree();
    });

    it('a refused event (the producer’s fault) is WARNING', async () => {
      sinon.stub(notificationRepo, 'getNotificationRepository').returns({
        insertMany: async () => { throw new ValidationError('Notification event refused', []); },
        insertNew: async () => { throw new ValidationError('Notification event refused', []); },
      } as any);
      await deliver(failureEvent);
      expect(atLeastInfo().map(level)).to.deep.equal(['warn']);
      assertTextFree();
    });

    it('an event with a bad org id: one WARNING with its size, no value', async () => {
      await deliver({ ...failureEvent, orgId: 'not-an-id' });
      const [line] = atLeastInfo();
      expect(level(line)).to.equal('warn');
      expect(render(line)).to.match(/"size_bytes": \d+/);
      assertTextFree();
    });
  });

  describe('producers and the socket service', () => {
    it('a mail publish is DEBUG, with the event type and topic as fields', async () => {
      const producer = new MailProducer({ isConnected: () => true, connect: async () => undefined, publish: async () => undefined } as any,
        Logger.getInstance({ service: 'mailService' }));
      await inRequestContext(requestId, () =>
        producer.publishEvent({ eventType: 'sendMail', timestamp: 1, payload: { mail: otpMail() } } as any));
      const lines = named('broker.event.published');
      expect(lines.map(level)).to.deep.equal(['debug']);
      expect(atLeastInfo()).to.have.length(0);
      assertTextFree();
    });

    it('a line about a recipient keeps the recipient as target_user_id inside another user’s request', () => {
      const service = new NotificationService({} as any);
      runWithRequestContext({ rootId: requestId, userId: '64b0000000000000000000b9' } as any, () => {
        service.sendToUser(ADMIN_ID, 'newNotification', {});
      });
      const [line] = atLeastInfo();
      expect(fields(line).target_user_id).to.equal(ADMIN_ID);
      expect(render(line)).to.include(ADMIN_ID);
    });
  });
});

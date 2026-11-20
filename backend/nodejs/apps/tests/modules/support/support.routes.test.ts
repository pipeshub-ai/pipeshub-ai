import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import express, { NextFunction, Request, Response } from 'express';
import type { AddressInfo } from 'net';
import type { Server } from 'http';
import { Container } from 'inversify';
import {
  makeOrgContext,
  runWithOrgContext,
} from '../../../src/libs/context/orgContext';
import { ErrorMiddleware } from '../../../src/libs/middlewares/error.middleware';
import { setOrgRepository } from '../../../src/libs/repositories/org';
import type { OrgRepository } from '../../../src/libs/repositories/org';
import { setUserRepository } from '../../../src/libs/repositories/user';
import type { UserRepository } from '../../../src/libs/repositories/user';
import type { Logger } from '../../../src/libs/services/logger.service';
import { MailBody } from '../../../src/modules/mail/middlewares/types';
import { MailSenderService } from '../../../src/modules/mail/services/mail.sender.service';
import { getEmailContent } from '../../../src/modules/mail/utils/email-content';
import {
  createFeedbackRouter,
  createSupportRouter,
} from '../../../src/modules/support/support.routes';
import * as userAdmin from '../../../src/modules/user_management/services/user-admin.service';
import { readZip } from './zip-reader';

const ORG_ID = '65a000000000000000000001';
const USER_ID = '65a000000000000000000002';
const SMTP = {
  host: 'smtp.acme.test',
  port: 587,
  fromEmail: 'noreply@acme.test',
};
const CHROME_ON_WINDOWS =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36';

describe('Contact support and Send feedback routes', () => {
  let server: Server;
  let baseUrl: string;
  let send: sinon.SinonStub;
  let getSmtpConfig: sinon.SinonStub;
  const savedSupportEmail = process.env.SUPPORT_EMAIL;

  const sent = (): MailBody[] =>
    send.getCalls().map((call) => call.args[0] as MailBody);

  const supportForm = (
    fields: Record<string, string> = {},
    files: Array<[string, Buffer]> = [],
  ): FormData => {
    const form = new FormData();
    const values = {
      area: 'connectors',
      description: 'The Drive sync stopped an hour ago.',
      requestKey: '1b4e28ba-2fa1-4d2e-883f-0016d3cca427',
      ...fields,
    };
    for (const [name, value] of Object.entries(values))
      form.append(name, value);
    for (const [name, data] of files)
      form.append('attachments', new Blob([data]), name);
    return form;
  };

  const request = async (
    method: string,
    path: string,
    body?: FormData,
    signedIn = true,
  ) => {
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      body,
      headers: {
        'user-agent': CHROME_ON_WINDOWS,
        ...(signedIn ? { 'x-test-user': '1' } : {}),
      },
      signal: AbortSignal.timeout(5000),
    });
    return {
      status: res.status,
      body: (await res.json().catch(() => ({}))) as Record<string, any>,
    };
  };

  beforeEach(async () => {
    process.env.SUPPORT_EMAIL = 'help@pipeshub.test';
    send = sinon.stub().resolves({ status: 'sent' });
    getSmtpConfig = sinon.stub().resolves(SMTP);
    const logger = {
      debug: sinon.stub(),
      info: sinon.stub(),
      warn: sinon.stub(),
      error: sinon.stub(),
    } as unknown as Logger;
    setUserRepository({
      getLive: async () => ({
        _id: USER_ID,
        fullName: 'Ada Lovelace',
        email: 'ada@acme.test',
      }),
    } as unknown as UserRepository);
    setOrgRepository({
      getNames: async () => ({
        registeredName: 'Acme Corporation',
        shortName: 'Acme',
      }),
    } as unknown as OrgRepository);
    sinon.stub(userAdmin, 'isUserOrgAdmin').resolves(true);

    const container = new Container();
    container.bind('Logger').toConstantValue(logger);
    container
      .bind(MailSenderService)
      .toConstantValue({ send, getSmtpConfig } as unknown as MailSenderService);
    container.bind('AuthMiddleware').toConstantValue({
      authenticate: (req: Request, _res: Response, next: NextFunction) => {
        if (!req.headers['x-test-user']) {
          _res.status(401).json({ error: { message: 'Unauthorized' } });
          return;
        }
        (req as Request & { user: unknown }).user = {
          userId: USER_ID,
          orgId: ORG_ID,
          isOAuth: req.headers['x-test-oauth'] === '1',
        };
        // As the real middleware does: the token's org is bound for the rest of the request.
        runWithOrgContext(
          makeOrgContext({
            orgId: ORG_ID,
            userId: USER_ID,
            kind: 'user',
            source: 'jwt',
          }),
          next,
        );
      },
    });

    const app = express();
    app.use(express.json());
    app.use('/support', createSupportRouter(container));
    app.use('/feedback', createFeedbackRouter(container));
    app.use(ErrorMiddleware.handleError());
    server = await new Promise<Server>((resolve) => {
      const s = app.listen(0, '127.0.0.1', () => resolve(s));
    });
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  afterEach(async () => {
    sinon.restore();
    setUserRepository();
    setOrgRepository();
    if (savedSupportEmail === undefined) delete process.env.SUPPORT_EMAIL;
    else process.env.SUPPORT_EMAIL = savedSupportEmail;
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  });

  describe('callers holding an access token', () => {
    it('cannot send a support request or feedback; only an interactive session can', async () => {
      for (const path of ['/support', '/feedback']) {
        const response = await fetch(`${baseUrl}${path}`, {
          method: 'POST',
          headers: { 'x-test-user': '1', 'x-test-oauth': '1' },
          body: new FormData(),
        });
        expect(response.status, path).to.equal(403);
      }
      expect(send.called).to.equal(false);
    });
  });

  describe('GET /support/context', () => {
    it('says whether email is set up, where requests go, and what is shown as sent', async () => {
      const { status, body } = await request('GET', '/support/context');
      expect(status).to.equal(200);
      expect(body).to.deep.equal({
        smtpConfigured: true,
        supportEmail: 'help@pipeshub.test',
        workspaceName: 'Acme',
        details: [
          { label: 'Workspace', value: 'Acme' },
          { label: 'Signed in as', value: 'Ada Lovelace (ada@acme.test)' },
        ],
      });
    });

    it('reports email as not set up when the settings are missing or cannot be read', async () => {
      getSmtpConfig.resolves(null);
      expect(
        (await request('GET', '/support/context')).body.smtpConfigured,
      ).to.equal(false);
      getSmtpConfig.rejects(new Error('store down'));
      expect(
        (await request('GET', '/support/context')).body.smtpConfigured,
      ).to.equal(false);
    });

    it('falls back to the brand support address without SUPPORT_EMAIL', async () => {
      delete process.env.SUPPORT_EMAIL;
      expect(
        (await request('GET', '/support/context')).body.supportEmail,
      ).to.equal('support@pipeshub.com');
    });

    it('refuses a caller who is not signed in', async () => {
      expect(
        (await request('GET', '/support/context', undefined, false)).status,
      ).to.equal(401);
    });
  });

  describe('POST /support', () => {
    it('emails support with the files and a Reply-To of the person, then sends them a copy built from what they saw', async () => {
      const { status, body } = await request(
        'POST',
        '/support',
        supportForm({}, [['shot.png', Buffer.from('png-bytes')]]),
      );
      expect(status).to.equal(201);
      expect(body.outcome).to.equal('sent');
      expect(body.reference).to.match(
        /^SUP-[23456789ABCDEFGHJKMNPQRSTVWXYZ]{6}$/,
      );
      expect(body.supportEmail).to.equal('help@pipeshub.test');

      await new Promise((resolve) => setImmediate(resolve));
      const [toSupport, copy] = sent();
      expect(toSupport.sendEmailTo).to.deep.equal(['help@pipeshub.test']);
      expect(toSupport.replyTo).to.equal('ada@acme.test');
      expect(toSupport.subject).to.equal(
        `[Support · Connectors and sync] Acme · ${String(body.reference)}`,
      );
      expect(toSupport.attachments).to.have.length(1);
      const zipName = `support-${String(body.reference)}.zip`;
      expect(toSupport.attachments?.[0].filename).to.equal(zipName);
      expect(toSupport.attachments?.[0].contentType).to.equal(
        'application/zip',
      );
      expect(
        String(
          readZip(toSupport.attachments?.[0].content as Buffer)['shot.png'],
        ),
      ).to.equal('png-bytes');
      const supportHtml = getEmailContent(
        toSupport.emailTemplateType,
        toSupport.templateData ?? {},
      );
      expect(supportHtml).to.include(String(body.reference));
      expect(supportHtml).to.include(`Acme (${ORG_ID})`);
      expect(supportHtml).to.include('Chrome 141 on Windows');
      expect(supportHtml).to.include(`1 file(s) in ${zipName}`);

      expect(copy.sendEmailTo).to.deep.equal(['ada@acme.test']);
      expect(copy.replyTo).to.equal('help@pipeshub.test');
      expect(copy.attachments?.[0].filename).to.equal(zipName);
      expect(
        String(readZip(copy.attachments?.[0].content as Buffer)['shot.png']),
      ).to.equal('png-bytes');
      const copyHtml = getEmailContent(
        copy.emailTemplateType,
        copy.templateData ?? {},
      );
      expect(copyHtml).to.include('Support request received');
      expect(copyHtml).to.include(`1 file(s) in ${zipName}`);
      expect(copyHtml).not.to.include(ORG_ID);
    });

    it('answers a retried request with the first result and sends nothing again', async () => {
      const first = await request('POST', '/support', supportForm());
      const again = await request('POST', '/support', supportForm());
      expect(again.body).to.deep.equal(first.body);
      await new Promise((resolve) => setImmediate(resolve));
      expect(sent()).to.have.length(2);
    });

    it('sends nothing and says so when email is not set up', async () => {
      getSmtpConfig.resolves(null);
      const { status, body } = await request('POST', '/support', supportForm());
      expect([status, body.outcome]).to.deep.equal([201, 'no_smtp']);
      expect(body.reference).to.match(/^SUP-/);
      expect(send.called).to.equal(false);
    });

    it('answers "pending" when the mail server is still busy at the deadline, and holds the copy back', async () => {
      send.resolves({ status: 'indeterminate', error: 'deadline' });
      const { body } = await request('POST', '/support', supportForm());
      expect(body.outcome).to.equal('pending');
      expect(send.calledOnce).to.equal(true);
    });

    it('answers "failed" when the send is refused, and sends no copy', async () => {
      send.resolves({ status: 'permanent', error: 'refused' });
      const { body } = await request('POST', '/support', supportForm());
      expect(body.outcome).to.equal('failed');
      expect(send.calledOnce).to.equal(true);
    });

    it('lets each person send five requests an hour', async () => {
      for (let i = 0; i < 5; i++) {
        const key = `1b4e28ba-2fa1-4d2e-883f-0016d3cca42${String(i)}`;
        expect(
          (await request('POST', '/support', supportForm({ requestKey: key })))
            .status,
        ).to.equal(201);
      }
      const sixth = await request(
        'POST',
        '/support',
        supportForm({ requestKey: '1b4e28ba-2fa1-4d2e-883f-0016d3cca429' }),
      );
      expect(sixth.status).to.equal(429);
    });

    it('lists the recent errors the person chose to include', async () => {
      const recentErrors = JSON.stringify([
        {
          method: 'GET',
          path: '/api/v1/connectors',
          status: 502,
          requestId: 'req-42',
          at: '2026-11-18T10:00:00.000Z',
        },
      ]);
      await request('POST', '/support', supportForm({ recentErrors }));
      const html = getEmailContent(
        sent()[0].emailTemplateType,
        sent()[0].templateData ?? {},
      );
      expect(html).to.include('GET /api/v1/connectors · 502 · req-42');
    });

    it('refuses a short description, an area this build does not offer, a query string in an error path, and files over 8 MB in total', async () => {
      const refused = [
        supportForm({ description: 'too short' }),
        supportForm({ area: 'not-an-area' }),
        supportForm({ requestKey: 'not-a-uuid' }),
        supportForm({
          recentErrors: JSON.stringify([
            {
              method: 'GET',
              path: '/api/v1/x?token=secret',
              status: 500,
              at: '2026-11-18T10:00:00.000Z',
            },
          ]),
        }),
        supportForm({}, [
          ['a.pdf', Buffer.alloc(4.5 * 1024 * 1024)],
          ['b.pdf', Buffer.alloc(4.5 * 1024 * 1024)],
        ]),
      ];
      for (const form of refused) {
        expect((await request('POST', '/support', form)).status).to.equal(400);
      }
      expect(send.called).to.equal(false);
    });

    it('refuses a file type the form does not offer', async () => {
      const { status } = await request(
        'POST',
        '/support',
        supportForm({}, [['run.exe', Buffer.from('MZ')]]),
      );
      expect(status).to.equal(400);
      expect(send.called).to.equal(false);
    });
  });

  describe('Send feedback', () => {
    const feedbackForm = (kind = 'feedback'): FormData => {
      const form = new FormData();
      form.append('kind', kind);
      form.append('description', 'Please add a dark theme for the editor.');
      form.append(
        'attachments',
        new Blob([Buffer.from('log line')]),
        'app.log',
      );
      return form;
    };

    it('emails support with the files, then sends the person a copy with the same files', async () => {
      const { status, body } = await request(
        'POST',
        '/feedback',
        feedbackForm(),
      );
      expect([status, body]).to.deep.equal([201, { outcome: 'sent' }]);
      await new Promise((resolve) => setImmediate(resolve));
      const [toSupport, copy] = sent();
      expect(toSupport.sendEmailTo).to.deep.equal(['help@pipeshub.test']);
      expect(toSupport.replyTo).to.equal('ada@acme.test');
      expect(toSupport.subject).to.equal(
        '[Feature request] Acme — Please add a dark theme for the editor.',
      );
      expect(
        getEmailContent(
          toSupport.emailTemplateType,
          toSupport.templateData ?? {},
        ),
      ).to.include('New Feature request');
      expect(copy.sendEmailTo).to.deep.equal(['ada@acme.test']);
      expect(copy.subject).to.equal('We received your feature request');
      expect(copy.attachments?.[0].filename).to.match(
        /^feedback-SUP-[A-Z0-9]{6}\.zip$/,
      );
      expect(
        String(readZip(copy.attachments?.[0].content as Buffer)['app.log']),
      ).to.equal('log line');
      expect(
        getEmailContent(copy.emailTemplateType, copy.templateData ?? {}),
      ).to.include('are attached to this email');
    });

    it('tells support and the person when files could not be attached', () => {
      const data = {
        kindLabel: 'Feedback',
        description: 'Please add a dark theme for the editor.',
        userName: 'Ada',
        attachmentCount: 0,
        attachmentsFailed: true,
        submittedAt: '8 Oct 2026, 21:10 UTC',
      };

      expect(getEmailContent('feedbackReceived', data)).to.include(
        'could not be attached',
      );
      expect(getEmailContent('feedbackCopy', data)).to.include(
        'Your files could not be attached',
      );
      expect(
        getEmailContent('feedbackCopy', { ...data, attachmentsFailed: false }),
      ).to.include('No files attached');
    });

    it('labels general feedback as Feedback', async () => {
      await request('POST', '/feedback', feedbackForm('issue'));
      expect(sent()[0].subject).to.match(/^\[Feedback\] Acme/);
    });

    it('says when nothing could be sent, and tells the person nothing was received', async () => {
      send.resolves({ status: 'transient', error: 'timeout' });
      expect(
        (await request('POST', '/feedback', feedbackForm())).body,
      ).to.deep.equal({ outcome: 'failed' });
      expect(send.calledOnce).to.equal(true);

      getSmtpConfig.resolves(null);
      expect(
        (await request('POST', '/feedback', feedbackForm())).body,
      ).to.deep.equal({ outcome: 'no_smtp' });
      expect(send.calledOnce).to.equal(true);
    });

    it('refuses an unknown kind', async () => {
      expect(
        (await request('POST', '/feedback', feedbackForm('cancellation')))
          .status,
      ).to.equal(400);
    });
  });
});

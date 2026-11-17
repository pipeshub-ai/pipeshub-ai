import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import nodemailer from 'nodemailer';
import {
  buildSandboxCapacityMail,
  footerNoteFor,
} from '../../../src/modules/notification/utils/sandbox-capacity-mail';
import { parseSandboxCapacityAlert } from '../../../src/modules/notification/utils/sandbox-capacity-alert';
import {
  getEmailContent,
  getEmailText,
} from '../../../src/modules/mail/utils/email-content';
import { MailSenderService } from '../../../src/modules/mail/services/mail.sender.service';
import { MailModel } from '../../../src/modules/mail/schema/mailInfo.schema';
import { setMailLogRepository } from '../../../src/libs/repositories/mail-log';
import { setIdentityPort } from '../../../src/libs/repositories/identity';

const DOCS = 'https://docs.pipeshub.com/deployment/code-sandboxes#load-alerts';

const payload = (overrides: Record<string, unknown> = {}) => ({
  v: 1,
  alertId: 'a1',
  level: 'warning',
  host: 'pipeshub-7d9c',
  memorySource: 'host',
  windowMinutes: 30,
  runs: 412,
  waited: 57,
  refused: 0,
  busyPercent: 72,
  refusedPercent: 0,
  longestWaitSeconds: 118,
  slotWaitSeconds: 120,
  reason: 'slots',
  limit: { current: 3, ceiling: 6, floor: 2 },
  hostMemoryTotalGb: 16,
  hostMemoryFreeGb: 1.1,
  ...overrides,
});

const templateData = (overrides: Record<string, unknown> = {}) => {
  const parsed = parseSandboxCapacityAlert({ type: 'SANDBOX_CAPACITY_WARNING', payload: payload(overrides) });
  if (!parsed.ok) throw new Error(parsed.reason);
  return buildSandboxCapacityMail(parsed.alert)!.templateData;
};

describe('sandbox capacity alert email: cooldown footer and plain-text part', () => {
  describe('footerNoteFor', () => {
    it('says once a day for a one-day cooldown and for an event that has none', () => {
      expect(footerNoteFor(86400)).to.contain('at most once a day for each level');
      expect(footerNoteFor(undefined)).to.contain('at most once a day for each level');
      expect(footerNoteFor('86400')).to.contain('at most once a day');
      expect(footerNoteFor(0)).to.contain('at most once a day');
    });

    it('names a shorter or longer cooldown', () => {
      expect(footerNoteFor(600)).to.contain('at most once every 10 minutes for each level');
      expect(footerNoteFor(3600)).to.contain('at most once an hour');
      expect(footerNoteFor(7200)).to.contain('at most once every 2 hours');
      expect(footerNoteFor(172800)).to.contain('at most once every 2 days');
      expect(footerNoteFor(90)).to.contain('at most once every 90 seconds');
      expect(footerNoteFor(60)).to.contain('at most once a minute');
    });
  });

  it('puts the cooldown from the event in the HTML footer', () => {
    const html = getEmailContent(
      'sandboxCapacityAlert',
      templateData({ emailCooldownSeconds: 600 }) as never,
    );
    expect(html).to.contain('at most once every 10 minutes for each level');
    expect(html).to.not.contain('once a day');
  });

  it('keeps the one-day footer for a job queued before the note existed', () => {
    const older: Record<string, unknown> = { ...templateData() };
    delete older.footerNote;
    const html = getEmailContent('sandboxCapacityAlert', older as never);
    expect(html).to.contain('at most once a day for each level');
  });

  it('renders a plain-text part with the same content as the HTML', () => {
    const text = getEmailText(
      'sandboxCapacityAlert',
      templateData({
        emailCooldownSeconds: 600,
        reason: 'memory',
        indexingSlowedSince: '2026-11-16T10:13:00Z',
      }) as never,
    )!;
    expect(text).to.contain('Code runs are waiting for capacity');
    expect(text).to.contain("Why runs were held back: Free memory on the host was low, so new runs were held back to protect the databases and the app.");
    expect(text).to.contain('Already done: Indexing was already slowed down from 10:13 UTC to give chat priority.');
    expect(text).to.contain('What you can do:\n- Add memory to the host');
    expect(text).to.contain(`- Read the load alerts guide: ${DOCS}`);
    expect(text).to.contain('Host: pipeshub-7d9c');
    expect(text).to.contain('at most once every 10 minutes for each level');
    expect(text).to.not.contain('&amp;');
  });

  it('has no plain-text part for other templates', () => {
    expect(getEmailText('appuserInvite', { invitee: 'a' })).to.equal(undefined);
  });

  describe('MailSenderService', () => {
    const smtp = { host: 'mailpit', port: 1025, fromEmail: 'no-reply@example.com' } as any;
    const logger = { debug: sinon.stub(), info: sinon.stub(), error: sinon.stub(), warn: sinon.stub() } as any;
    let sendMail: sinon.SinonStub;

    beforeEach(() => {
      sendMail = sinon.stub().resolves({ messageId: 'm1' });
      sinon.stub(nodemailer, 'createTransport').returns({ sendMail, close: sinon.stub() } as any);
      sinon.stub(MailModel.prototype, 'save').resolves({} as any);
    });

    afterEach(() => {
      setMailLogRepository();
      setIdentityPort();
      sinon.restore();
    });

    it('sends the alert with both an HTML and a plain-text part', async () => {
      const sender = new MailSenderService(() => ({ smtp }) as any, logger);
      const result = await sender.send(
        {
          emailTemplateType: 'sandboxCapacityAlert',
          subject: 's',
          sendEmailTo: ['admin@example.com'],
          templateData: templateData(),
        },
        smtp,
      );
      expect(result.status).to.equal('sent');
      const message = sendMail.firstCall.args[0];
      expect(message.html).to.contain('<html');
      expect(message.text).to.contain('Why runs were held back:');
    });

    it('sends other templates as HTML only', async () => {
      const sender = new MailSenderService(() => ({ smtp }) as any, logger);
      await sender.send(
        {
          emailTemplateType: 'appuserInvite',
          subject: 'Invite',
          sendEmailTo: ['user@example.com'],
          templateData: { invitee: 'Admin', orgName: 'Corp', link: 'http://x/y' },
        },
        smtp,
      );
      expect(sendMail.firstCall.args[0]).to.not.have.property('text');
    });
  });
});

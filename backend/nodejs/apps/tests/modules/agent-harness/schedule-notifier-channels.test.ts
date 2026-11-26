/**
 * The schedule notifier's channels when one of them cannot deliver (TR-07, G2): every channel is tried on
 * its own, the Inbox row is kept whatever happens outside it, a person with no address is skipped rather than
 * failed, and the channels that failed are named together afterwards so the caller retries only those.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  HarnessScheduleNotifier,
  NoticeChannelsFailed,
  SCHEDULE_FIRING_REFUSED_NOTIFICATION,
  type ScheduleMailSender,
} from '../../../src/libs/repositories/agent-harness/schedule-notifier/mongo';
import type {
  NoticeRow,
  ScheduleNoticeRecords,
} from '../../../src/libs/repositories/agent-harness/schedule-notifier/records';
import type { InteractiveRunNotice } from '../../../src/modules/agent-harness/ports/inbox';
import { NotificationContainer } from '../../../src/modules/notification/container/notification.container';

const ANA = { email: 'ana@example.com', name: 'Ana' };
const DEADLINE = Date.UTC(2026, 10, 9, 18, 0, 0);

interface Rig {
  saved: NoticeRow[];
  mails: unknown[];
  slacks: unknown[];
  pushes: unknown[];
}

function rig(options: {
  person?: () => Promise<typeof ANA | null>;
  mail?: Partial<ScheduleMailSender> | null;
  slack?: (() => Promise<'sent' | 'skipped'>) | null;
  savedRow?: boolean;
  saveFails?: boolean;
}): Rig & { notifier: HarnessScheduleNotifier } {
  const r: Rig = { saved: [], mails: [], slacks: [], pushes: [] };
  sinon.stub(NotificationContainer, 'getNotificationService').returns({
    sendToUser: (...args: unknown[]) => r.pushes.push(args),
  } as never);
  const records: ScheduleNoticeRecords = {
    answer: async () => null,
    person: options.person ?? (async () => ANA),
    saveNotice: async (_org, _actor, row) => {
      if (options.saveFails === true) throw new Error('inbox down');
      r.saved.push(row);
      return options.savedRow === false ? null : { _id: 'n1', ...row };
    },
  };
  const mail: ScheduleMailSender | undefined =
    options.mail === null
      ? undefined
      : {
          sendRunEnded: async (m) => void r.mails.push(m),
          sendTemplate: async (m) => void r.mails.push(m),
          ...(options.mail ?? {}),
        };
  const slackSend = options.slack;
  const notifier = new HarnessScheduleNotifier({
    records,
    ...(mail !== undefined ? { mail } : {}),
    ...(slackSend === null
      ? {}
      : {
          slack: {
            send: async (m: unknown) => (
              r.slacks.push(m),
              slackSend ? slackSend() : 'sent'
            ),
          },
        }),
    frontendUrl: () => 'https://pipeshub.example/',
  });
  return { ...r, notifier };
}

const failedOn = async (work: Promise<unknown>): Promise<string[] | 'ok'> => {
  try {
    await work;
    return 'ok';
  } catch (error) {
    expect(error).to.be.instanceOf(NoticeChannelsFailed);
    return (error as NoticeChannelsFailed).channels;
  }
};

const waiting = {
  orgId: 'o1',
  actorId: 'ana',
  scheduleId: 's1',
  scheduleName: 'Weekly digest',
  agentKey: 'digest',
  sessionId: 'c1',
  runId: 'r1',
  deadlineAt: DEADLINE,
};

const finished: InteractiveRunNotice = {
  orgId: 'o1',
  userId: 'ana',
  sessionId: 'c1',
  runId: 'r1',
  finished: true,
  cutShort: false,
  chatTitle: 'Q3 plan',
  channels: ['in_app', 'email', 'slack'],
  quiet: false,
};

describe('schedule notifier channels that cannot deliver', () => {
  afterEach(() => sinon.restore());

  describe("the person-notice channels a bound feature sends through", () => {
    it('a waiting run tells the actor in the Inbox only', async () => {
      const person = sinon.stub().resolves(ANA);
      const r = rig({ person });
      await r.notifier.approvalWaiting(waiting);
      expect(r.saved).to.have.length(1);
      expect([r.mails, r.slacks]).to.deep.equal([[], []]);
      expect(person.called).to.equal(false);
    });

    it("a template mail goes by the mail path with the person's name; a Slack line by the agent's bot; links are absolute", async () => {
      const r = rig({ mail: { sendTemplate: async (m) => void r.mails.push(m) } });
      expect(await r.notifier.person('o1', 'ana')).to.deep.equal(ANA);
      expect(r.notifier.appUrl('/chat/?conversationId=c1')).to.equal('https://pipeshub.example/chat/?conversationId=c1');
      await r.notifier.mail({ orgId: 'o1', to: ANA, subject: 'S', template: 'someTemplate', data: { a: 1 } });
      expect(r.mails).to.deep.equal([{ orgId: 'o1', to: ANA, subject: 'S', template: 'someTemplate', data: { a: 1 } }]);
      expect(await r.notifier.slack({ agentKey: 'digest', email: ANA.email, text: 'hi' })).to.equal('sent');
      expect(r.slacks).to.deep.equal([{ agentKey: 'digest', email: ANA.email, text: 'hi' }]);
    });

    it('no mail or Slack path throws, so the caller can tell an outage from a person it cannot reach', async () => {
      const r = rig({ mail: null, slack: null });
      let mailError: unknown;
      await r.notifier.mail({ orgId: 'o1', to: ANA, subject: 'S', template: 't', data: {} }).catch((e) => (mailError = e));
      expect(mailError).to.be.instanceOf(Error);
      let slackError: unknown;
      await r.notifier.slack({ email: ANA.email, text: 'hi' }).catch((e) => (slackError = e));
      expect(slackError).to.be.instanceOf(Error);
    });
  });

  describe('G2: a finished interactive run', () => {
    it('a person nobody can look up fails email and Slack; the Inbox row is kept and pushed', async () => {
      const r = rig({ person: () => Promise.reject(new Error('down')) });
      expect(await failedOn(r.notifier.runFinished(finished))).to.deep.equal([
        'email',
        'slack',
      ]);
      expect(r.saved).to.have.length(1);
      expect(r.pushes).to.have.length(1);
    });

    it('a person with no address is skipped outside the app; no mail or Slack path fails that channel', async () => {
      const skipped = rig({ person: async () => null });
      expect(await failedOn(skipped.notifier.runFinished(finished))).to.equal(
        'ok',
      );
      expect([skipped.mails, skipped.slacks]).to.deep.equal([[], []]);
      sinon.restore();
      expect(
        await failedOn(
          rig({ mail: null, slack: null }).notifier.runFinished(finished),
        ),
      ).to.deep.equal(['email', 'slack']);
    });

    it('a notice the inbox could not save fails the Inbox channel; the Slack line names the chat and links to it', async () => {
      const r = rig({
        saveFails: true,
        mail: { sendRunEnded: () => Promise.reject(new Error('smtp')) },
      });
      expect(await failedOn(r.notifier.runFinished(finished))).to.deep.equal([
        'in_app',
        'email',
      ]);
      expect(r.pushes).to.deep.equal([]);
      expect(
        (r.slacks[0] as { text: string }).text.startsWith('"Q3 plan": '),
      ).to.equal(true);
      expect((r.slacks[0] as { text: string }).text).to.match(
        /Open it: https:\/\/pipeshub\.example\/chat\/\?conversationId=c1$/,
      );
      expect(r.slacks[0]).to.not.have.property('agentKey');
    });

    it('a row the inbox did not return is not pushed', async () => {
      const r = rig({ savedRow: false });
      await r.notifier.runFinished({ ...finished, channels: ['in_app'] });
      expect(r.saved).to.have.length(1);
      expect(r.pushes).to.deep.equal([]);
    });
  });

  describe('a firing that could not start', () => {
    // The name rides in the payload so the Inbox words the row in the reader's language (ADR-INBOX-01 A1).
    it('is stored as a warning naming the schedule and the reason; quiet, it is not pushed', async () => {
      const r = rig({});
      const notice = {
        orgId: 'o1',
        actorId: 'ana',
        scheduleId: 's1',
        scheduleName: 'Weekly digest',
        reason: 'quota',
      };
      await r.notifier.firingRefused(notice);
      await r.notifier.firingRefused({ ...notice, quiet: true });
      expect(
        r.saved.map((row) => [row.type, row.severity, row.payload]),
      ).to.deep.equal([
        [
          SCHEDULE_FIRING_REFUSED_NOTIFICATION,
          'warning',
          { scheduleId: 's1', scheduleName: 'Weekly digest', reason: 'quota' },
        ],
        [
          SCHEDULE_FIRING_REFUSED_NOTIFICATION,
          'warning',
          { scheduleId: 's1', scheduleName: 'Weekly digest', reason: 'quota' },
        ],
      ]);
      expect(r.saved[0]?.message).to.equal(
        '"Weekly digest" did not run. Open Schedules to see why in its history.',
      );
      expect(r.pushes).to.have.length(1);
    });
  });
});

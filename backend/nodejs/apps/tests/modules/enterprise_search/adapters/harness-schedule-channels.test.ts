/**
 * TR-07: a scheduled run's end on each channel, with every outside send faked
 * (no mail service, no Slack): the inbox row with its summary and file links,
 * the mail and the Slack message with the outcome and a link only.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { NotificationContainer } from '../../../../src/modules/notification/container/notification.container';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import {
  AgentSlackBotMessages,
  MailScheduleNotices,
  type SlackWebClient,
} from '../../../../src/modules/enterprise_search/adapters/harness-schedule-channels';
import {
  HarnessScheduleNotifier,
  NoticeChannelsFailed,
  SCHEDULE_RUN_ENDED_NOTIFICATION,
  type ScheduleMailSender,
  type SlackDirectMessages,
} from '../../../../src/libs/repositories/agent-harness/schedule-notifier/mongo';
import { EmailTemplateType } from '../../../../src/modules/mail/middlewares/types';
import type { RunEndedNotice } from '../../../../src/modules/agent-harness/ports/schedules';

const notice: RunEndedNotice = {
  orgId: '507f1f77bcf86cd799439011',
  actorId: '507f191e810c19729de860ea',
  scheduleId: 'schedule-1',
  scheduleName: 'Nightly digest',
  agentKey: 'support-agent',
  sessionId: 'session-1',
  runId: 'run-1',
  status: 'completed',
  channels: ['in_app', 'email', 'slack'],
};

/** A run-ended notice mails through its own template; any other mail here is a wrong path. */
const unexpectedTemplate = async (): Promise<void> => expect.fail('a run-ended notice sent a template mail');

const ANSWER =
  '## Digest\n\nThree **new** tickets [1], see [the board](https://x.test/b).\n\n' +
  '::artifact[digest.pdf](record:rec-1){application/pdf|doc|rec-1|ARTIFACT|1}';

describe('TR-07 HarnessScheduleNotifier.runEnded', () => {
  afterEach(() => sinon.restore());

  function stubInbox() {
    const create = sinon.stub(Notifications, 'create').resolves([{ toObject: () => ({ _id: 'n1' }) }] as never);
    const sendToUser = sinon.spy();
    sinon.stub(NotificationContainer, 'getNotificationService').returns({ sendToUser } as never);
    return { create, sendToUser };
  }

  function channels() {
    const mails: Array<Parameters<ScheduleMailSender['sendRunEnded']>[0]> = [];
    const slacks: Array<Parameters<SlackDirectMessages['send']>[0]> = [];
    return {
      mails,
      slacks,
      mail: { sendRunEnded: async (m: (typeof mails)[number]) => void mails.push(m), sendTemplate: unexpectedTemplate },
      slack: { send: async (m: (typeof slacks)[number]) => (slacks.push(m), 'sent' as const) },
    };
  }

  it('TR-07: the inbox gets a summary and the run’s files; mail and Slack the outcome and a link, never the answer', async () => {
    const { create, sendToUser } = stubInbox();
    const c = channels();
    await new HarnessScheduleNotifier({
      answer: async () => ANSWER,
      person: async () => ({ email: 'alice@example.com', name: 'Alice' }),
      mail: c.mail,
      slack: c.slack,
      frontendUrl: () => 'https://ph.example.com/',
    }).runEnded(notice);

    const [[row]] = create.firstCall.args as unknown as [[Record<string, unknown>]];
    expect(row).to.deep.include({
      type: SCHEDULE_RUN_ENDED_NOTIFICATION,
      severity: 'success',
      title: 'A scheduled run finished',
      message: '"Nightly digest": Digest Three new tickets, see the board.',
      redirectLink: '/chat/?agentId=support-agent&conversationId=session-1',
      payload: {
        scheduleId: 'schedule-1',
        scheduleName: 'Nightly digest',
        runId: 'run-1',
        conversationId: 'session-1',
        status: 'completed',
        artifacts: [{ name: 'digest.pdf', link: '/record/rec-1/' }],
      },
    });
    expect(sendToUser.calledOnce).to.equal(true);
    const link = 'https://ph.example.com/chat/?agentId=support-agent&conversationId=session-1';
    expect(c.mails).to.deep.equal([
      {
        orgId: notice.orgId,
        to: { email: 'alice@example.com', name: 'Alice' },
        subject: 'A scheduled run finished: Nightly digest',
        scheduleName: 'Nightly digest',
        outcome: 'A scheduled run finished',
        link,
      },
    ]);
    expect(c.slacks).to.deep.equal([
      { agentKey: 'support-agent', email: 'alice@example.com', text: `"Nightly digest" finished. Open it: ${link}` },
    ]);
    expect(JSON.stringify([c.mails, c.slacks])).to.not.include('tickets');
  });

  it("GA-05 (G-07): the inbox row carries the run's summary and every file it saved; mail and Slack still carry none of it", async () => {
    const { create } = stubInbox();
    const c = channels();
    const digest: NonNullable<RunEndedNotice['digest']> = {
      outcome: 'answered',
      actions: { total: 3, failed: 1, refused: 0, tools: [{ name: 'jira__create_issue', count: 3 }] },
      commands: { total: 0, failed: 0 },
      records: { read: 4, changed: 2 },
      artifacts: [
        { name: 'digest.pdf', recordId: 'rec-1' },
        { name: 'tickets.csv', recordId: 'rec-2' },
        { name: 'kept.md' },
      ],
      files: { added: 0, modified: 0, deleted: 0, working: 0 },
      approvals: { asked: 1, approved: 1, denied: 0, unanswered: 0 },
      questions: { asked: 0, open: false },
      delegated: 0,
      cost: { usdMicros: 120_000, tokens: 9_000 },
      costEstimated: false,
      durationMs: 42_000,
    };
    await new HarnessScheduleNotifier({
      answer: async () => ANSWER,
      person: async () => ({ email: 'alice@example.com', name: 'Alice' }),
      mail: c.mail,
      slack: c.slack,
      frontendUrl: () => 'https://ph.example.com',
    }).runEnded({ ...notice, digest });

    const [[row]] = create.firstCall.args as unknown as [[Record<string, unknown>]];
    expect(row['message']).to.equal(
      '"Nightly digest": Used 3 tools (1 failed), read 4 records, made 2 changes, saved 3 files, ' +
        'asked 1 approval (1 approved, 0 denied), cost USD 0.12. Digest Three new tickets, see the board.',
    );
    const payload = row['payload'] as Record<string, unknown>;
    expect(payload['artifacts']).to.deep.equal([
      { name: 'digest.pdf', link: '/record/rec-1/' },
      { name: 'tickets.csv', link: '/record/rec-2/' },
    ]);
    expect(payload['digest']).to.deep.equal(digest);
    const outside = JSON.stringify([c.mails, c.slacks]);
    for (const word of ['tools', 'records', 'USD', 'tickets.csv']) expect(outside).to.not.include(word);
  });

  it('TR-07: only the channels named; a failing channel does not stop the others and is named after', async () => {
    const { create } = stubInbox();
    const slacks: unknown[] = [];
    let thrown: unknown;
    try {
      await new HarnessScheduleNotifier({
        answer: async () => null,
        person: async () => ({ email: 'alice@example.com', name: 'Alice' }),
        mail: { sendRunEnded: async () => Promise.reject(new Error('smtp down')), sendTemplate: unexpectedTemplate },
        slack: { send: async (m) => (slacks.push(m), 'sent') },
      }).runEnded({ ...notice, status: 'failed', channels: ['in_app', 'email', 'slack'] });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).to.be.instanceOf(NoticeChannelsFailed);
    expect((thrown as NoticeChannelsFailed).channels).to.deep.equal(['email']);
    expect(create.calledOnce).to.equal(true);
    expect((create.firstCall.args as unknown as [[Record<string, unknown>]])[0][0]).to.deep.include({
      severity: 'warning',
      title: 'A scheduled run failed',
      message: '"Nightly digest"',
    });
    expect(slacks).to.have.length(1);

    sinon.restore();
    const inbox = stubInbox();
    await new HarnessScheduleNotifier({ answer: async () => 'ok' }).runEnded({ ...notice, channels: ['in_app'] });
    expect(inbox.create.calledOnce).to.equal(true);
  });

  it('TR-07: a user with no address gets no mail and no Slack message, and that is not a failure', async () => {
    stubInbox();
    const c = channels();
    await new HarnessScheduleNotifier({
      answer: async () => 'done',
      person: async () => null,
      mail: c.mail,
      slack: c.slack,
    }).runEnded(notice);
    expect([c.mails, c.slacks]).to.deep.equal([[], []]);
  });
});

describe('TR-07 MailScheduleNotices', () => {
  it('TR-07: one mail to the actor, through the app’s mail path and its run-ended template', async () => {
    const sent: Array<Record<string, unknown>> = [];
    const mail = new MailScheduleNotices(
      { sendMail: async (params) => (sent.push(params as unknown as Record<string, unknown>), { statusCode: 200 }) },
      () => 'scoped-secret',
    );
    await mail.sendRunEnded({
      orgId: 'org-1',
      to: { email: 'alice@example.com', name: 'Alice' },
      subject: 'A scheduled run finished: Nightly digest',
      scheduleName: 'Nightly digest',
      outcome: 'A scheduled run finished',
      link: 'https://ph.example.com/chat/?conversationId=s',
    });
    expect(sent[0]).to.deep.include({
      emailTemplateType: EmailTemplateType.ScheduledRunEnded,
      usersMails: ['alice@example.com'],
      subject: 'A scheduled run finished: Nightly digest',
      templateData: {
        name: 'Alice',
        scheduleName: 'Nightly digest',
        outcome: 'A scheduled run finished',
        link: 'https://ph.example.com/chat/?conversationId=s',
      },
    });
    const failing = new MailScheduleNotices({ sendMail: async () => ({ statusCode: 500 }) }, () => 's');
    let thrown = false;
    await failing
      .sendRunEnded({ orgId: 'o', to: { email: 'a@b.c', name: 'A' }, subject: 's', scheduleName: 'n', outcome: 'o', link: 'l' })
      .catch(() => (thrown = true));
    expect(thrown).to.equal(true);
  });
});

describe('TR-07 AgentSlackBotMessages', () => {
  function client(users: Record<string, string>, posted: Array<{ channel: string; text: string }>): SlackWebClient {
    return {
      users: {
        lookupByEmail: async ({ email }) => {
          const id = users[email];
          if (id === undefined) throw Object.assign(new Error('not found'), { data: { error: 'users_not_found' } });
          return { ok: true, user: { id } };
        },
      },
      chat: { postMessage: async (m) => void posted.push(m) },
    };
  }

  it('TR-07: an agent has a bot only when the registry names it; the assistant and other agents have none', async () => {
    const bots = new AgentSlackBotMessages(
      async () => [{ botToken: 'xoxb-support', agentId: 'support-agent' }, { botToken: 'xoxb-free', agentId: null }],
      () => {
        throw new Error('no client is opened to answer this');
      },
    );
    expect(await bots.hasBot('support-agent')).to.equal(true);
    expect(await bots.hasBot('other-agent')).to.equal(false);
  });

  it("TR-07: the agent's own bot messages the actor found by email; no agent bot, no agent or no Slack account sends nothing", async () => {
    const posted: Array<{ channel: string; text: string }> = [];
    const tokens: string[] = [];
    const slack = new AgentSlackBotMessages(
      async () => [
        { botToken: 'xoxb-other', agentId: 'other-agent' },
        { botToken: 'xoxb-support', agentId: 'support-agent' },
      ],
      (token) => (tokens.push(token), client({ 'alice@example.com': 'U1' }, posted)),
    );
    expect(await slack.send({ agentKey: 'support-agent', email: 'alice@example.com', text: 'done' })).to.equal('sent');
    expect(posted).to.deep.equal([{ channel: 'U1', text: 'done' }]);
    expect(tokens).to.deep.equal(['xoxb-support']);
    expect(await slack.send({ agentKey: 'support-agent', email: 'bob@example.com', text: 'x' })).to.equal('skipped');
    expect(await slack.send({ agentKey: 'no-bot-agent', email: 'alice@example.com', text: 'x' })).to.equal('skipped');
    expect(await slack.send({ email: 'alice@example.com', text: 'x' })).to.equal('skipped');
    expect(posted).to.have.length(1);
  });
});

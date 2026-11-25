/**
 * R35-SLACK D-SLACK-ASK (R34FIX-FU-2): a Slack turn whose stream ended before its run (it handed off at an approval
 * card, or the bot went away) gets the run's end posted in its thread, once, by the bot that answers the thread; a run
 * the bot still reads is the bot's to post. The thread is the run's own (`input.reply`), pinned by the Slack route
 * from the verified bot and never sent to a worker.
 */
import { expect } from 'chai';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { RunWatchRegistry, slackThreadViewer } from '../../../src/modules/agent-harness/application/run-end-notices';
import {
  type SlackThreadPoster,
  ThreadReplyService,
} from '../../../src/modules/agent-harness/application/thread-replies';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { workerInput } from '../../../src/modules/agent-harness/domain/worker-input';
import {
  chatTurnFieldRole,
  SLACK_THREAD_FIELD,
  slackReply,
} from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { SlackBotThreadReplies } from '../../../src/modules/enterprise_search/adapters/slack-thread-replies';

const NOW = 1_700_000_000_000;
const THREAD = { botId: 'bot-1', channel: 'C0E2E', threadTs: '1794543996.000004' };
const ANSWER = {
  _id: 'c1',
  messages: [
    { messageType: 'user_query', content: 'make a ticket' },
    { messageType: 'bot_response', content: 'Created the ticket **ACME-1**.', citations: [] },
  ],
};

const run = (over: Partial<HarnessRun> = {}): HarnessRun =>
  ({
    runId: 'r1',
    orgId: 'o1',
    sessionId: 'c1',
    actorId: 'ana',
    status: 'completed',
    outcome: 'answer',
    idempotencyKey: 'turn-1',
    input: { query: 'make a ticket', reply: { slack: THREAD } },
    ...over,
  }) as HarnessRun;

function world() {
  const store = new InMemoryInboxStore();
  const posted: Array<Parameters<SlackThreadPoster['post']>[0]> = [];
  const service = new ThreadReplyService({
    leases: store,
    claims: store,
    conversations: { finishedConversation: async () => ANSWER },
    poster: {
      post: async (reply) => {
        posted.push(reply);
        return 'posted';
      },
    },
    now: () => NOW,
  });
  return { store, posted, service };
}

describe('R35-SLACK D-SLACK-ASK: a Slack run that ends after its stream gets its end in the thread', () => {
  it('SLACKREPLY-1: an unwatched end is posted once, with the finished conversation and its link', async () => {
    const { service, posted } = world();
    expect(await service.runEnded(run({ agentKey: 'agent-1' }))).to.equal('posted');
    expect(await service.runEnded(run({ agentKey: 'agent-1' }))).to.equal('claimed_elsewhere');
    expect(posted).to.deep.equal([
      {
        orgId: 'o1',
        ...THREAD,
        end: 'answered',
        conversation: ANSWER,
        link: '/chat/?agentId=agent-1&conversationId=c1',
      },
    ]);
  });

  it("SLACKREPLY-2: while the bot's stream reads the run it is the bot's to post; the web app watching is not", async () => {
    const { service, store, posted } = world();
    const watchers = new RunWatchRegistry(store, { now: () => NOW });
    const web = watchers.watch({ orgId: 'o1', userId: 'ana', sessionId: 'c1' });
    expect(await service.runEnded(run({ runId: 'r-web' }))).to.equal('posted');
    web();
    const slack = watchers.watch({ orgId: 'o1', userId: slackThreadViewer('ana'), sessionId: 'c1' });
    await new Promise((resolve) => setImmediate(resolve));
    expect(await service.runEnded(run({ runId: 'r-slack' }))).to.equal('watched');
    slack();
    expect(posted.map((p) => p.end)).to.deep.equal(['answered']);
  });

  it('SLACKREPLY-3: a failed or stopped run is said so without a conversation; a running one, a job and a web run are not posted', async () => {
    const { service, posted } = world();
    expect(await service.runEnded(run({ runId: 'f', status: 'failed' }))).to.equal('posted');
    expect(await service.runEnded(run({ runId: 'x', status: 'cancelled' }))).to.equal('posted');
    expect(posted.map((p) => [p.end, p.conversation])).to.deep.equal([
      ['failed', null],
      ['cancelled', null],
    ]);
    expect(await service.runEnded(run({ runId: 'w', status: 'running' }))).to.equal('not_ended');
    expect(
      await service.runEnded(run({ runId: 'c', parent: { runId: 'r1', invocationId: 'inv-1', depth: 1 } } as Partial<HarnessRun>)),
    ).to.equal('no_thread');
    expect(await service.runEnded(run({ runId: 'web', input: { query: 'q' } } as Partial<HarnessRun>))).to.equal(
      'no_thread',
    );
  });

  it('SLACKREPLY-4: the thread is Node\'s alone: a worker never reads it', () => {
    const input = workerInput({ input: run().input, requires: { foldVersion: 0, features: [] } } as Pick<HarnessRun, 'input' | 'requires'>);
    expect(input).to.not.have.property('reply');
    expect(input).to.have.property('query', 'make a ticket');
  });
});

describe('R35-SLACK D-SLACK-ASK: the Slack route pins the thread from the verified bot', () => {
  const req = (configId?: string) =>
    ({ tokenPayload: configId === undefined ? { email: 'ana@x.test' } : { email: 'ana@x.test', configId } }) as never;

  it('SLACKROUTE-1: the body names the thread, the token names the bot', () => {
    const body = { [SLACK_THREAD_FIELD]: { channel: 'C0E2E', threadTs: '1794543996.000004' } };
    expect(slackReply(req('bot-1'), body)).to.deep.equal({ slack: THREAD });
    expect(chatTurnFieldRole(SLACK_THREAD_FIELD)).to.equal('served');
  });

  it('SLACKROUTE-2: no bot in the token, or a thread that is not one, pins nothing', () => {
    const good = { channel: 'C0E2E', threadTs: '1794543996.000004' };
    expect(slackReply(req(), { slackThread: good })).to.equal(undefined);
    expect(slackReply(req('bot-1'), { slackThread: { ...good, channel: '<!channel>' } })).to.equal(undefined);
    expect(slackReply(req('bot-1'), { slackThread: { ...good, threadTs: 'now' } })).to.equal(undefined);
    expect(slackReply(req('bot-1'), {})).to.equal(undefined);
  });
});

describe('R35-SLACK D-SLACK-ASK: the bot that answers the thread posts the end there', () => {
  function poster(bots: Array<{ botId?: string; botToken: string }>) {
    const sent: Array<{ token: string; args: Record<string, unknown> }> = [];
    const replies = new SlackBotThreadReplies(
      async () => bots,
      (token) => ({
        chat: {
          postMessage: async (args) => {
            sent.push({ token, args: args as Record<string, unknown> });
            return { ok: true };
          },
        },
      }),
      () => 'https://pipeshub.example/',
    );
    return { replies, sent };
  }
  const reply = (over: Partial<Parameters<SlackThreadPoster['post']>[0]> = {}) => ({
    orgId: 'o1',
    ...THREAD,
    end: 'answered' as const,
    conversation: ANSWER,
    link: '/chat/?conversationId=c1',
    ...over,
  });

  it("SLACKPOST-1: the answer is posted in the thread as the bot composes its own answer, with that bot's token", async () => {
    const { replies, sent } = poster([{ botId: 'other', botToken: 'xoxb-other' }, { botId: 'bot-1', botToken: 'xoxb-1' }]);
    expect(await replies.post(reply())).to.equal('posted');
    expect(sent).to.have.length(1);
    expect(sent[0]!.token).to.equal('xoxb-1');
    expect(sent[0]!.args).to.include({ channel: 'C0E2E', thread_ts: THREAD.threadTs, unfurl_links: false });
    expect(JSON.stringify(sent[0]!.args['blocks'])).to.contain('Created the ticket *ACME-1*');
  });

  it('SLACKPOST-2: a failed run says so and links the conversation; a removed bot posts nothing', async () => {
    const { replies, sent } = poster([{ botId: 'bot-1', botToken: 'xoxb-1' }]);
    await replies.post(reply({ end: 'failed', conversation: null }));
    expect(sent[0]!.args['text']).to.equal(
      "I couldn't finish this. <https://pipeshub.example/chat/?conversationId=c1|Open the conversation in PipesHub> to see what happened.",
    );
    expect(await poster([]).replies.post(reply())).to.equal('skipped');
  });
});

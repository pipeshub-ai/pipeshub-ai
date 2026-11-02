import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  SLACK_PLAN_TITLE,
  SlackPlanStream,
  SlackTaskPlan,
  webResultSources,
} from '../../../src/integrations/slack-bot/src/utils/plan-stream';
import {
  SLACK_STREAM_MESSAGE_CHAR_LIMIT,
  type TypedSlackClient,
} from '../../../src/integrations/slack-bot/src/helpers';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyArgs = any;

const expired = () => Object.assign(new Error('done'), { data: { error: 'message_not_in_streaming_state' } });

function slackClient() {
  let next = 0;
  const apiCall = sinon.stub().callsFake(async (method: string) =>
    method === 'chat.startStream' ? { ok: true, ts: `s${++next}` } : { ok: true },
  );
  return { users: { info: sinon.stub() }, chat: { postMessage: sinon.stub(), update: sinon.stub() }, apiCall };
}

const calls = (c: ReturnType<typeof slackClient>, method: string): AnyArgs[] =>
  c.apiCall.getCalls().filter((call) => call.args[0] === method).map((call) => call.args[1] as AnyArgs);

function stream(c = slackClient(), plan = new SlackTaskPlan()) {
  return {
    c,
    plan,
    s: new SlackPlanStream(c as unknown as TypedSlackClient, 'C1', '1.1', { recipient_user_id: 'U1' }, plan),
  };
}

describe('slack-bot/utils/plan-stream', () => {
  beforeEach(() => sinon.stub(console, 'error'));
  afterEach(() => sinon.restore());

  describe('SlackTaskPlan', () => {
    it('opens a tool as in progress with the text written before it, and closes it by call id', () => {
      const plan = new SlackTaskPlan();
      plan.narration('Let me look in Jira.');

      expect(plan.toolStarted('jira_search', 'Searching Jira', 'c1')).to.deep.equal({
        type: 'task_update', id: 'c1', title: 'Searching Jira', status: 'in_progress', details: 'Let me look in Jira.',
      });
      expect(plan.toolFinished('jira_search', 'Searched Jira', 'completed', 'c1')).to.deep.include({
        id: 'c1', title: 'Searched Jira', status: 'complete', details: 'Let me look in Jira.',
      });
    });

    it('marks failed and blocked tools as errors', () => {
      const plan = new SlackTaskPlan();
      expect(plan.toolFinished('a', 'A', 'failed', 'c1').status).to.equal('error');
      expect(plan.toolFinished('b', 'B', 'blocked', 'c2').status).to.equal('error');
    });

    it('leaves a tool waiting for approval pending, saying so, even when the reply stops', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('jira_create', 'Creating issue', 'c1');

      expect(plan.toolFinished('jira_create', 'Create issue', 'awaiting_approval', 'c1')).to.deep.include({
        status: 'pending', output: 'Waiting for approval',
      });
      expect(plan.failRunning(new Set(['c1']))).to.deep.equal([]);
    });

    it('pairs a result without a call id with the open tool of the same name', () => {
      const plan = new SlackTaskPlan();
      const started = plan.toolStarted('search', 'Searching');
      expect(plan.toolFinished('search', 'Searched', 'completed').id).to.equal(started.id);
    });

    it('shows finished reasoning as a step only when flushed, so a discarded call can drop it', () => {
      const plan = new SlackTaskPlan();
      plan.reasoningDelta('Check the ');
      plan.reasoningDelta('docs first.');
      plan.reasoningDone();
      expect(plan.flushReasoning()).to.deep.include({ title: 'Reasoning', status: 'complete', details: 'Check the docs first.' });
      expect(plan.flushReasoning()).to.equal(null);

      plan.reasoningDelta('Wrong idea.');
      plan.reasoningDone();
      plan.dropReasoning();
      expect(plan.flushReasoning()).to.equal(null);
    });

    it('turns sub-agents into tasks', () => {
      const plan = new SlackTaskPlan();
      expect(plan.subAgent('Researcher', 'started').status).to.equal('in_progress');
      expect(plan.subAgent('Researcher', 'failed')).to.deep.include({ title: 'Researcher', status: 'error' });
    });

    it("puts a running tool's progress in its output, once per change", () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('mcp_jira_search', 'Searching Jira', 'c1');
      expect(plan.toolProgress('mcp_jira_search', ' 3/10')).to.deep.include({ id: 'c1', output: '3/10' });
      expect(plan.toolProgress('mcp_jira_search', ' 3/10')).to.equal(null);
      expect(plan.toolProgress('other', ' 1/2')).to.equal(null);
    });

    it('lists the pages a web search returned as the step sources', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('web_search', 'Searching the web', 'c1');
      const content = 'Title: A\nURL: https://www.example.com/a.\nTitle: B\nURL: https://docs.test/b\nURL: https://docs.test/b';

      expect(plan.toolFinished('web_search', 'Searched the web', 'completed', 'c1', content).sources).to.deep.equal([
        { type: 'url', url: 'https://www.example.com/a', text: 'example.com' },
        { type: 'url', url: 'https://docs.test/b', text: 'docs.test' },
      ]);
      expect(webResultSources('jira_search', content)).to.deep.equal([]);
    });

    it('uses the fetched page itself as the source of fetch_url, not the links inside it', () => {
      const result = JSON.stringify({ ok: true, url: 'https://docs.test/page', title: 'Page', blocks: [{ text: 'see https://other.test/x' }] });
      expect(webResultSources('fetch_url', result)).to.deep.equal([{ type: 'url', url: 'https://docs.test/page', text: 'docs.test' }]);
      expect(webResultSources('fetch_url', '{"ok": false}')).to.deep.equal([]);
    });

    it('starts the snapshot with the plan title and fails only running tasks of the message', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('search', 'Searching', 'c1');
      plan.toolStarted('fetch', 'Fetching', 'c2');

      expect(plan.snapshot()[0]).to.deep.equal({ type: 'plan_update', title: SLACK_PLAN_TITLE });
      expect(plan.failRunning(new Set(['c1']))).to.deep.equal([
        { type: 'task_update', id: 'c1', title: 'Searching', status: 'error' },
      ]);
    });
  });

  describe('SlackPlanStream', () => {
    it('opens nothing until there is a step or answer text', async () => {
      const { c, s } = stream();
      expect(await s.stop()).to.equal(true);
      expect(c.apiCall.called).to.equal(false);
    });

    it('opens the reply in plan mode, titled Thinking, on the first step', async () => {
      const { c, s, plan } = stream();

      await s.tasks([plan.toolStarted('search', 'Searching', 'c1')]);

      expect(calls(c, 'chat.startStream')).to.deep.equal([{
        channel: 'C1',
        thread_ts: '1.1',
        chunks: [
          { type: 'plan_update', title: 'Thinking' },
          { type: 'task_update', id: 'c1', title: 'Searching', status: 'in_progress' },
        ],
        task_display_mode: 'plan',
        unfurl_links: false,
        unfurl_media: false,
        recipient_user_id: 'U1',
      }]);
    });

    it('opens on answer text without a plan when there was no step', async () => {
      const { c, s } = stream();
      await s.answer('Revenue grew **12%**.');

      const [start] = calls(c, 'chat.startStream');
      expect(start.chunks).to.deep.equal([{ type: 'markdown_text', text: 'Revenue grew **12%**.' }]);
    });

    it('marks itself unavailable when Slack refuses the first message', async () => {
      const { c, s, plan } = stream();
      c.apiCall.rejects(Object.assign(new Error('nope'), { data: { error: 'not_allowed' } }));

      await s.tasks([plan.toolStarted('search', 'Searching', 'c1')]);

      expect(s.unavailable).to.equal(true);
      expect(await s.stop({ markdown: 'Failed.' })).to.equal(false);
    });

    it('streams the answer only as markdown_text chunks', async () => {
      const { c, s, plan } = stream();
      await s.tasks([plan.toolStarted('search', 'Searching', 'c1')]);
      await s.answer('Revenue grew 12%.');

      const [append] = calls(c, 'chat.appendStream');
      expect(append).to.deep.equal({ channel: 'C1', ts: 's1', chunks: [{ type: 'markdown_text', text: 'Revenue grew 12%.' }] });
    });

    it('moves to a new message in plan mode when one is full, closing an open code fence', async () => {
      const { c, s, plan } = stream();
      await s.answer('```ts\n');
      const line = `${'x'.repeat(99)}\n`;
      await s.answer(line.repeat(Math.ceil(SLACK_STREAM_MESSAGE_CHAR_LIMIT / line.length) + 5));
      await s.tasks([plan.toolStarted('late', 'Late step', 'c9')]);

      const [, second] = calls(c, 'chat.startStream');
      expect(second.task_display_mode).to.equal('plan');
      expect(second.chunks[0]).to.deep.equal({ type: 'markdown_text', text: '```\n' });
      const [rollover] = calls(c, 'chat.stopStream');
      expect(rollover).to.deep.equal({ channel: 'C1', ts: 's1', chunks: [{ type: 'markdown_text', text: '\n```\n' }] });
      expect(calls(c, 'chat.appendStream').pop().chunks[0]).to.include({ id: 'c9' });
    });

    it('goes on in a new message when the ten-minute stream has ended', async () => {
      const { c, s } = stream();
      await s.answer('Start.');
      c.apiCall.withArgs('chat.appendStream').rejects(expired());

      await s.answer('The rest.');

      expect(calls(c, 'chat.startStream')[1].chunks).to.deep.equal([{ type: 'markdown_text', text: 'The rest.' }]);
      expect(s.ts).to.equal('s2');
    });

    it('finishes with the sources as blocks and fails steps left running, without chat.update', async () => {
      const { c, s, plan } = stream();
      await s.tasks([plan.toolStarted('search', 'Searching', 'c1')]);
      const sources = [{ type: 'section', text: { type: 'mrkdwn', text: '*Sources:*' } }];

      expect(await s.stop({ blocks: sources })).to.equal(true);

      expect(calls(c, 'chat.stopStream')).to.deep.equal([{
        channel: 'C1',
        ts: 's1',
        chunks: [{ type: 'task_update', id: 'c1', title: 'Searching', status: 'error' }],
        blocks: sources,
      }]);
      expect(c.chat.update.called).to.equal(false);
    });

    it('tells the caller to post an error it could not add to an expired stream', async () => {
      const { c, s } = stream();
      await s.answer('Start.');
      c.apiCall.withArgs('chat.stopStream').rejects(expired());

      expect(await s.stop({ markdown: 'Something went wrong.' })).to.equal(false);
    });

    it('takes back a discarded call: deletes its message and reopens with the plan and the text before it', async () => {
      const { c, s, plan } = stream();
      await s.tasks([plan.toolStarted('search', 'Searching', 'c1')]);
      await s.answer('Kept opening. ');
      s.markSegment();
      await s.answer('Wrong partial answer.');
      await s.tasks([plan.toolFinished('search', 'Searched', 'completed', 'c1')]);

      await s.dropSegment();

      expect(calls(c, 'chat.delete')).to.deep.equal([{ channel: 'C1', ts: 's1' }]);
      expect(calls(c, 'chat.startStream')[1].chunks).to.deep.equal([
        { type: 'plan_update', title: 'Thinking' },
        { type: 'task_update', id: 'c1', title: 'Searched', status: 'complete' },
        { type: 'markdown_text', text: 'Kept opening. ' },
      ]);
      await s.answer('Right answer.');
      expect(calls(c, 'chat.appendStream').pop()).to.deep.include({ ts: 's2' });
    });

    it('leaves the reply alone when the discarded call showed nothing', async () => {
      const { c, s } = stream();
      await s.answer('Kept.');
      s.markSegment();
      await s.dropSegment();
      expect(calls(c, 'chat.delete')).to.deep.equal([]);
    });

    it('deletes the reply when the run asks a question instead', async () => {
      const { c, s } = stream();
      await s.answer('Start.');
      await s.discard();

      expect(calls(c, 'chat.delete')).to.deep.equal([{ channel: 'C1', ts: 's1' }]);
      expect(await s.stop({ blocks: [{ type: 'divider' }] })).to.equal(false);
    });
  });
});

import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  SlackPlanStream,
  SlackTaskPlan,
  compactArgs,
  openFence,
  redactSecrets,
  splitIntoRows,
  viewSources,
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
  beforeEach(() => {
    sinon.stub(console, 'error');
    SlackPlanStream.minIntervalMs = 0;
  });
  afterEach(() => sinon.restore());

  describe('SlackTaskPlan', () => {
    it('shows a lone call with its label as the title and its Arguments and Result as output', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('knowledgegraph__search', 'Searched the knowledge base', 'c1');
      plan.toolArgs('knowledgegraph__search', { summary: 'Searched for "Q3"' }, 'c1');

      expect(
        plan.toolFinished('knowledgegraph__search', 'Searched the knowledge base', 'completed', 'c1', { summary: 'No results found' }),
      ).to.deep.equal([{
        type: 'task_update',
        id: 'tools-1',
        title: 'Knowledgegraph: Searched the knowledge base',
        status: 'complete',
        output: 'Arguments: Searched for "Q3"\nResult: No results found',
      }]);
    });

    it('joins calls with no text between them into one row: a summary line and every call listed', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('web_search', 'Searched the web', 'c1');
      plan.toolStarted('web_search', 'Searched the web', 'c2');
      expect(plan.toolProgress('web_search', '1/3')[0]).to.deep.include({ details: '2 calls · 2 running' });
      plan.toolFinished('web_search', 'Searched the web', 'completed', 'c1', { summary: '7 results' });
      const [row] = plan.toolFinished('web_search', 'Searched the web', 'failed', 'c2', { summary: 'Failed: The search took too long' });

      expect(row).to.deep.include({
        id: 'tools-1',
        title: 'Explored 2 searches',
        status: 'error',
        details: '2 calls · 1 failed',
        output: '✓ Searched the web\n    Result: 7 results\n⚠ Searched the web\n    Result: Failed: The search took too long',
      });
      expect((row!.details as string).length).to.be.at.most(256);

      plan.endGroup();
      expect(plan.toolStarted('fetch_url', 'Read a web page', 'c3')[0]!.id).to.not.equal('tools-1');
    });

    it('names the pages a burst of fetches read', () => {
      const plan = new SlackTaskPlan();
      const page = (url: string) => ({ view: { kind: 'fields', fields: [{ label: 'Title', value: 'Page' }], url } });
      plan.toolStarted('fetch_url', 'Read a web page', 'c1');
      plan.toolStarted('fetch_url', 'Read a web page', 'c2');
      plan.toolFinished('fetch_url', 'Read a web page', 'completed', 'c1', { ...page('https://investors.asana.com/q3'), summary: 'Read “Q3 results” (investors.asana.com)' });
      const [row] = plan.toolFinished('fetch_url', 'Read a web page', 'completed', 'c2', page('https://trustcenter.asana.com/'));

      expect(row).to.deep.include({
        title: 'Fetched 2 pages: investors.asana.com, trustcenter.asana.com',
        output: '✓ Read a web page (investors.asana.com)\n    Result: Read “Q3 results” (investors.asana.com)\n✓ Read a web page (trustcenter.asana.com)',
      });
    });

    it('says "Ran N tools" for a mix, and tags each call with its app when they differ', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('jira__search_issues', 'Searched Jira', 'c1');
      const [row] = plan.toolStarted('confluence__get_page', 'Read a page', 'c2');
      expect(row).to.deep.include({ title: 'Ran 2 tools', status: 'in_progress', output: '• Jira: Searched Jira\n• Confluence: Read a page' });
    });

    it('lists every call, continuing in another row rather than dropping or cutting one', () => {
      const plan = new SlackTaskPlan();
      const long = `Searched for "${'x'.repeat(900)}"`;
      let rows: ReturnType<SlackTaskPlan['toolStarted']> = [];
      for (let i = 0; i < 6; i += 1) {
        plan.toolStarted('web_search', 'Searched the web', `c${String(i)}`);
        plan.toolArgs('web_search', { summary: long }, `c${String(i)}`);
        rows = plan.toolFinished('web_search', 'Searched the web', 'completed', `c${String(i)}`);
      }

      expect(rows.length).to.be.greaterThan(1);
      expect(rows[1]).to.deep.include({ title: 'Explored 6 searches (continued)', status: 'complete' });
      const headings = rows.flatMap((r) => (r.output as string).split('\n').filter((l) => l.startsWith('✓')));
      expect(headings).to.have.length(6);
      expect(rows.every((r) => (r.output as string).length <= 2500)).to.equal(true);
    });

    it('falls back to compact arguments, shows a SQL query, and never shows a secret', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('sql__execute_sql_query', 'Ran a query', 'c1');
      plan.toolStarted('github__create_issue', 'Created an issue', 'c2');
      plan.toolArgs('sql__execute_sql_query', { summary: 'Querying the database', raw: '{"query":"SELECT 1"}' }, 'c1');
      const [row] = plan.toolArgs(
        'github__create_issue',
        { raw: '{"title":"Bug","token":"ghp_abcdefghijklmnopqrstuvwxyz0123","labels":["a"],"api_key":"k-123"}' },
        'c2',
      );

      const output = row!.output as string;
      expect(output).to.include('Arguments: Querying the database\n    Query: SELECT 1');
      expect(output).to.include('Arguments: title: Bug, token: [redacted], api_key: [redacted]');
      expect(output).to.not.include('ghp_').and.not.include('k-123').and.not.include('labels');
    });

    it('stays running while any call runs, and shows progress only then', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('mcp_jira_search', 'Searched Jira', 'c1');
      expect(plan.toolProgress('mcp_jira_search', ' 3/10')[0]).to.deep.include({ status: 'in_progress', output: 'Progress: 3/10' });
      expect(plan.toolProgress('mcp_jira_search', ' 3/10')).to.deep.equal([]);
      expect(plan.toolFinished('mcp_jira_search', 'Searched Jira', 'completed', 'c1')[0]).to.not.have.property('output');
    });

    it('keeps a tool waiting for approval in progress, saying so, and never fails it when the reply stops', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('jira_create', 'Created an issue', 'c1');
      expect(plan.toolFinished('jira_create', 'Created an issue', 'awaiting_approval', 'c1')[0]).to.deep.include({
        status: 'in_progress', details: 'Waiting for your approval',
      });
      expect(plan.failRunning(new Set(['tools-1']))).to.deep.equal([]);
    });

    it('shows how an approval was settled, as the dashboard chips it', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('jira_create', 'Created an issue', 'c1', { approvalAnswer: 'allowed' });
      const [row] = plan.toolStarted('jira_create', 'Created an issue', 'c2', { approvalAnswer: 'declined' });
      expect(row!.output).to.equal('• Created an issue · Approved\n• Created an issue · Declined');
    });

    it('puts a sub-agent in a row of its own, with its own calls listed', () => {
      const plan = new SlackTaskPlan();
      expect(plan.subAgent('Researcher', 'started')[0]).to.deep.include({ title: 'Researcher', status: 'in_progress' });
      plan.toolStarted('web_search', 'Searched the web', 'c1', { agent: 'Researcher' });
      plan.toolFinished('web_search', 'Searched the web', 'completed', 'c1', { summary: '4 results' });
      const [row] = plan.subAgent('Researcher', 'completed');
      expect(row).to.deep.include({
        title: 'Researcher',
        status: 'complete',
        details: '1 call',
        output: '✓ Searched the web\n    Result: 4 results',
      });
      expect(plan.subAgent('Writer', 'failed')[0]).to.deep.include({ title: 'Writer', status: 'error' });
    });

    it('pairs a result without a call id with the open call of the same name', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('search', 'Searched');
      expect(plan.toolFinished('search', 'Searched', 'completed')[0]).to.deep.include({ id: 'tools-2', status: 'complete' });
    });

    it('shows narration as rows of its own, in full, and ends the open group', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('web_search', 'Searched the web', 'c1');
      const rows = plan.narration('Let me check the trust page.');
      expect(rows).to.deep.equal([{ type: 'task_update', id: rows[0]!.id, title: 'Let me check the trust page.', status: 'complete' }]);
      expect(plan.toolStarted('web_search', 'Searched the web', 'c2')[0]!.id).to.not.equal('tools-1');
    });

    it('shows finished reasoning as a row only when flushed, so a discarded call can drop it', () => {
      const plan = new SlackTaskPlan();
      plan.reasoningDelta('Check the ');
      plan.reasoningDelta('docs first.');
      plan.reasoningDone();
      expect(plan.flushReasoning()).to.deep.include({ title: 'Reasoning', status: 'complete', output: 'Check the docs first.' });
      expect(plan.flushReasoning()).to.equal(null);

      plan.reasoningDelta('Wrong idea.');
      plan.reasoningDone();
      plan.dropReasoning();
      expect(plan.flushReasoning()).to.equal(null);
    });

    it('fails only the running rows it is given', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('search', 'Searched', 'c1');
      expect(plan.failRunning(new Set(['tools-1']))).to.deep.equal([
        { type: 'task_update', id: 'tools-1', title: 'Searched', status: 'error' },
      ]);
    });

    it('never sends a status Slack does not document', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('a', 'A', 'c1');
      const rows = [
        ...plan.toolFinished('a', 'A', 'awaiting_approval', 'c1'),
        ...plan.subAgent('R', 'started'),
        ...plan.narration('Note.'),
      ];
      expect(rows.every((r) => ['in_progress', 'complete', 'error'].includes(r.status))).to.equal(true);
    });
  });

  describe('compactArgs and redactSecrets', () => {
    it('shows scalars as key: value, skips nested values and masks secrets', () => {
      expect(compactArgs('{"project":"OPS","limit":5,"filter":{"a":1},"password":"hunter2"}')).to.equal(
        'project: OPS, limit: 5, password: [redacted]',
      );
      expect(compactArgs('not json')).to.equal('');
      expect(redactSecrets('Authorization: Bearer abc.def.ghijklmnop and xoxb-123-456-abc')).to.equal(
        'Authorization: Bearer [redacted] and [redacted]',
      );
      expect(redactSecrets('Searched for "password reset policy"')).to.equal('Searched for "password reset policy"');
    });
  });

  describe('openFence', () => {
    it('follows CommonMark: same character, at least as long, nothing after it', () => {
      expect(openFence('```ts\ncode\n```\n')).to.equal(null);
      expect(openFence('~~~\ncode\n')).to.deep.equal({ marker: '~~~', opener: '~~~' });
      expect(openFence('````md\n```ts\ninner\n```\n')).to.deep.equal({ marker: '````', opener: '````md' });
      expect(openFence('```\ncode\n``` not a close\n')).to.deep.equal({ marker: '```', opener: '```' });
      expect(openFence('~~~\n```\n~~~\n')).to.equal(null);
    });
  });

  describe('splitIntoRows', () => {
    it('cuts at sentence ends, then at spaces, keeping every word', () => {
      const text = `${'First sentence is here. '.repeat(8)}${'word '.repeat(80)}`.trim();
      const rows = splitIntoRows(text, 100);
      expect(rows.every((r) => r.length <= 100)).to.equal(true);
      expect(rows.join(' ')).to.equal(text.replace(/\s+/g, ' '));
      expect(rows[0]!.endsWith('.')).to.equal(true);
      expect(splitIntoRows('Short.')).to.deep.equal(['Short.']);
    });
  });

  describe('viewSources', () => {
    it('lists the real pages of a result view, never a citation placeholder', () => {
      expect(viewSources({
        kind: 'records',
        columns: ['Title'],
        rows: [
          { cells: ['Trust at Asana'], url: 'https://asana.com/trust' },
          { cells: ['Placeholder'], url: 'https://ref83.xyz' },
          { cells: [''], url: 'https://www.docs.test/b' },
          { cells: ['Bad'], url: 'javascript:alert(1)' },
        ],
      })).to.deep.equal([
        { type: 'url', url: 'https://asana.com/trust', text: 'Trust at Asana' },
        { type: 'url', url: 'https://www.docs.test/b', text: 'docs.test' },
      ]);
      expect(viewSources('nope')).to.deep.equal([]);
    });
  });

  describe('SlackPlanStream', () => {
    it('opens nothing until there is a step or text', async () => {
      const { c, s } = stream();
      expect(await s.stop()).to.equal(true);
      expect(c.apiCall.called).to.equal(false);
    });

    it('opens the reply in plan mode, titled Thinking, on the first step', async () => {
      const { c, s, plan } = stream();

      await s.tasks(plan.toolStarted('search', 'Searching', 'c1'));

      expect(calls(c, 'chat.startStream')).to.deep.equal([{
        channel: 'C1',
        thread_ts: '1.1',
        chunks: [
          { type: 'plan_update', title: 'Thinking' },
          { type: 'task_update', id: 'tools-1', title: 'Searching', status: 'in_progress' },
        ],
        task_display_mode: 'plan',
        unfurl_links: false,
        unfurl_media: false,
        recipient_user_id: 'U1',
      }]);
    });

    it('opens on text without a plan when there was no step', async () => {
      const { c, s } = stream();
      await s.answer('Revenue grew **12%**.');

      const [start] = calls(c, 'chat.startStream');
      expect(start.chunks).to.deep.equal([{ type: 'markdown_text', text: 'Revenue grew **12%**.' }]);
    });

    it('marks itself unavailable when Slack refuses the first message', async () => {
      const { c, s, plan } = stream();
      c.apiCall.rejects(Object.assign(new Error('nope'), { data: { error: 'not_allowed' } }));

      await s.tasks(plan.toolStarted('search', 'Searching', 'c1'));

      expect(s.unavailable).to.equal(true);
      expect(await s.stop({ markdown: 'Failed.' })).to.equal(false);
    });

    it('streams the answer only as markdown_text chunks, joining pieces as they are', async () => {
      const { c, s, plan } = stream();
      await s.tasks(plan.toolStarted('search', 'Searching', 'c1'));
      await s.answer('Revenue ');
      await s.answer('grew 12%.');
      await s.answer('Next part.', { paragraph: true });

      const texts = calls(c, 'chat.appendStream').map((a) => a.chunks as AnyArgs[]);
      expect(texts).to.deep.equal([
        [{ type: 'markdown_text', text: 'Revenue ' }],
        [{ type: 'markdown_text', text: 'grew 12%.' }],
        [{ type: 'markdown_text', text: '\n\nNext part.' }],
      ]);
    });

    it('moves to a new message in plan mode when one is full, closing an open code fence', async () => {
      const { c, s, plan } = stream();
      await s.answer('```ts\n');
      const line = `${'x'.repeat(99)}\n`;
      await s.answer(line.repeat(Math.ceil(SLACK_STREAM_MESSAGE_CHAR_LIMIT / line.length) + 5));
      await s.tasks(plan.toolStarted('late', 'Late step', 'c9'));

      const [, second] = calls(c, 'chat.startStream');
      expect(second.task_display_mode).to.equal('plan');
      expect(second.chunks[0]).to.deep.equal({ type: 'markdown_text', text: '```ts\n' });
      const [rollover] = calls(c, 'chat.stopStream');
      expect(rollover).to.deep.equal({ channel: 'C1', ts: 's1', chunks: [{ type: 'markdown_text', text: '\n```\n' }] });
      expect(calls(c, 'chat.appendStream').pop().chunks[0]).to.include({ title: 'Late step' });
    });

    it('goes on in a new message when the ten-minute stream has ended', async () => {
      const { c, s } = stream();
      await s.answer('Start.');
      c.apiCall.withArgs('chat.appendStream').rejects(expired());

      await s.answer('The rest.');

      expect(calls(c, 'chat.startStream')[1].chunks).to.deep.equal([{ type: 'markdown_text', text: 'The rest.' }]);
      expect(s.ts).to.equal('s2');
    });

    it('finishes with the sources as blocks and fails steps left running, without removing the plan', async () => {
      const { c, s, plan } = stream();
      await s.tasks(plan.toolStarted('search', 'Searching', 'c1'));
      const sources = [{ type: 'section', text: { type: 'mrkdwn', text: '*Sources:*' } }];

      expect(await s.stop({ blocks: sources })).to.equal(true);

      expect(calls(c, 'chat.stopStream')).to.deep.equal([{
        channel: 'C1',
        ts: 's1',
        chunks: [{ type: 'task_update', id: 'tools-1', title: 'Searching', status: 'error' }],
        blocks: sources,
      }]);
      expect(calls(c, 'chat.delete')).to.deep.equal([]);
      expect(c.chat.update.called).to.equal(false);
    });

    it('tells the caller to post an error it could not add to an expired stream', async () => {
      const { c, s } = stream();
      await s.answer('Start.');
      c.apiCall.withArgs('chat.stopStream').rejects(expired());

      expect(await s.stop({ markdown: 'Something went wrong.' })).to.equal(false);
    });

    it('takes back a discarded call: reopens with the plan, every step at its final status, and the text before it', async () => {
      const { c, s, plan } = stream();
      await s.tasks(plan.toolStarted('search', 'Searching', 'c1'));
      await s.answer('Kept opening.');
      s.markSegment();
      await s.answer('Wrong partial answer.');
      await s.tasks(plan.toolFinished('search', 'Searched', 'completed', 'c1'));
      await s.tasks(plan.narration('Checking once more.'));

      await s.dropSegment();

      expect(calls(c, 'chat.delete')).to.deep.equal([{ channel: 'C1', ts: 's1' }]);
      expect(calls(c, 'chat.startStream')[1].chunks).to.deep.equal([
        { type: 'plan_update', title: 'Thinking' },
        { type: 'task_update', id: 'tools-1', title: 'Searched', status: 'complete' },
        { type: 'markdown_text', text: 'Kept opening.' },
        { type: 'task_update', id: 'note-2', title: 'Checking once more.', status: 'complete' },
      ]);
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

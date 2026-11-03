import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  SlackPlanStream,
  SlackTaskPlan,
  type SlackTaskChunk,
  compactArgs,
  isSecretKey,
  openFence,
  redactSecrets,
  splitIntoRows,
  viewSources,
} from '../../../src/integrations/slack-bot/src/utils/plan-stream';
import { replayRows } from './slack-bot-harness';
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
    const run = (plan: SlackTaskPlan, name: string, label: string, id: string, args: string, status: string, summary?: string) => {
      plan.toolStarted(name, label, id);
      if (args) plan.toolArgs(name, { summary: args }, id);
      return plan.toolFinished(name, label, status, id, summary ? { summary } : {});
    };

    it('titles a row by what it did and sends only title and status until it is sealed', () => {
      const plan = new SlackTaskPlan();
      const [live] = run(plan, 'knowledgegraph__search', 'Searched the knowledge base', 'c1', 'Searched for "Q3"', 'completed', 'No results found');
      expect(live).to.deep.equal({ type: 'task_update', id: 'tools-1', title: 'Searched the knowledge base', status: 'complete' });

      plan.endGroup();
      expect(plan.takeSealed()).to.deep.equal([{
        type: 'task_update',
        id: 'tools-1',
        title: 'Searched the knowledge base',
        status: 'complete',
        details: 'Knowledgegraph',
        output: '✓ "Q3" → No results found',
      }]);
      expect(plan.takeSealed()).to.deep.equal([]);
    });

    it('starts a new row for a different tool, never a mixed one, and shows no counts', () => {
      const plan = new SlackTaskPlan();
      run(plan, 'jira__search_issues', 'Searched Jira', 'c1', 'Searched for "OPS"', 'completed', '2 results');
      const [next] = plan.toolStarted('confluence__get_page', 'Read a Confluence page', 'c2');
      expect(next!.id).to.not.equal('tools-1');
      const [sealed] = plan.takeSealed();
      expect(sealed).to.deep.include({ id: 'tools-1', title: 'Searched Jira', details: 'Jira', output: '✓ "OPS" → 2 results' });
      expect(JSON.stringify(sealed)).to.not.match(/\b\d+ (calls?|tools|searches|running|failed)\b/);
    });

    it('reads a failed record download like a person would say it', () => {
      const plan = new SlackTaskPlan();
      run(plan, 'artifacts__get_record_download_url', 'Got record download links', 'c1',
        'Get Record Download Url — record_id: 8dc0d3d5-4b1e-4c2a-9f3d-2a6b7c8d9e0f', 'failed',
        'Failed: No download link for records from an external connector. Ask an admin.');
      plan.endGroup();
      const [row] = plan.takeSealed();
      expect(row).to.deep.include({
        title: 'Got record download links',
        status: 'error',
        details: 'Artifacts',
        output: '✗ record 8dc0d3d5 → No download link for records from an external connector',
      });
    });

    it('is an error row only when every call failed; a partial failure shows on its line', () => {
      const plan = new SlackTaskPlan();
      run(plan, 'web_search', 'Searched the web', 'c1', 'Searched for "a"', 'completed', '4 results');
      run(plan, 'web_search', 'Searched the web', 'c2', 'Searched for "b"', 'failed', 'Failed: The search took too long');
      plan.endGroup();
      const [row] = plan.takeSealed();
      expect(row).to.deep.include({ status: 'complete', output: '✓ "a" → 4 results\n✗ "b" → The search took too long' });
    });

    it('waits to seal a closed row until its last call ends', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('web_search', 'Searched the web', 'c1');
      plan.endGroup();
      expect(plan.takeSealed()).to.deep.equal([{ type: 'task_update', id: 'tools-1', title: 'Searched the web', status: 'in_progress' }]);
      const [row] = plan.toolFinished('web_search', 'Searched the web', 'completed', 'c1', { summary: '3 results' });
      expect(row).to.deep.include({ status: 'complete', output: '✓ 3 results' });
    });

    it('shows six lines and sums up the rest', () => {
      const plan = new SlackTaskPlan();
      for (let i = 0; i < 8; i += 1) run(plan, 'web_search', 'Searched the web', `c${String(i)}`, `Searched for "q${String(i)}"`, 'completed', 'ok');
      plan.endGroup();
      const [row] = plan.takeSealed();
      expect(row!.output!.split('\n')).to.have.length(7);
      expect(row!.output!.endsWith('+2 more')).to.equal(true);
    });

    it('cuts ids to 8 characters, falls back to compact arguments and masks secrets', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('github__create_issue', 'Created an issue', 'c1');
      plan.toolArgs('github__create_issue', { raw: '{"title":"Bug","token":"ghp_abcdefghijklmnopqrstuvwxyz0123","labels":["a"]}' }, 'c1');
      plan.toolFinished('github__create_issue', 'Created an issue', 'completed', 'c1', { summary: 'Created issue 4f9a2c1e8b7d6a5f4e3d2c1b and token=abc' });
      plan.endGroup();
      const [row] = plan.takeSealed();
      expect(row!.output).to.equal('✓ title: Bug, token: [redacted] → Created issue 4f9a2c1e and token=[redacted]');
    });

    it('keeps a tool waiting for approval in progress, then ends it as waiting, not failed, when the reply stops', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('jira_create', 'Created an issue', 'c1');
      expect(plan.toolFinished('jira_create', 'Created an issue', 'awaiting_approval', 'c1')[0]).to.deep.include({ status: 'in_progress' });
      expect(plan.failRunning(new Set(['tools-1']))).to.deep.equal([{
        type: 'task_update',
        id: 'tools-1',
        title: 'Waiting for your approval: Created an issue',
        status: 'complete',
        output: '• Waiting for your approval',
      }]);
    });
    it('never shows a secret as a call\'s argument, whatever its key', () => {
      for (const key of ['password', 'token', 'api_key']) {
        const plan = new SlackTaskPlan();
        run(plan, 'mcp_x_login', 'X · login', 'c1', `Login — ${key}: "hunter2", user: "bob"`, 'completed', 'ok');
        plan.toolStarted('mcp_x_login', 'X · login', 'c2');
        plan.toolArgs('mcp_x_login', { summary: `Login — ${key}: "hunter2"` }, 'c2');
        plan.toolFinished('mcp_x_login', 'X · login', 'completed', 'c2', { summary: 'ok' });
        plan.endGroup();
        const [row] = plan.takeSealed();
        expect(row!.output, key).to.not.include('hunter2');
        expect(row!.output!.split('\n')[0], key).to.equal('✓ "bob" → Ok');
      }
    });

    it('masks secrets in narration and reasoning', () => {
      const plan = new SlackTaskPlan();
      expect(plan.narration('Using token=abc123 now.')[0]!.title).to.equal('Using token=[redacted]');
      plan.reasoningDelta('Try password: hunter2');
      plan.reasoningDone();
      expect(plan.flushReasoning()!.output).to.equal('Try password: [redacted]');
    });

    it('keeps a sealed row\'s details for a replay after its status changes', () => {
      const plan = new SlackTaskPlan();
      run(plan, 'web_search', 'Searched the web', 'c1', 'Searched for "a"', 'completed', '4 results');
      plan.endGroup();
      plan.takeSealed();
      plan.toolFinished('web_search', 'Searched the web', 'failed', 'c1', { summary: 'Failed: late' });
      expect(plan.current('tools-1')).to.deep.include({ status: 'error', output: '✓ "a" → 4 results' });
    });


    it('keeps the waiting title within the title limit', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('jira_create', `Created ${'x'.repeat(300)}`, 'c1');
      plan.toolFinished('jira_create', `Created ${'x'.repeat(300)}`, 'awaiting_approval', 'c1');
      const [row] = plan.failRunning(new Set(['tools-1']));
      expect(row!.title.startsWith('Waiting for your approval: Created x')).to.equal(true);
      expect(row!.title.length).to.be.at.most(256);
    });

    it('shows how an approval was settled, as the dashboard chips it', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('jira_create', 'Created an issue', 'c1', { approvalAnswer: 'allowed' });
      plan.toolFinished('jira_create', 'Created an issue', 'completed', 'c1', { summary: 'Created OPS-12' });
      plan.endGroup();
      expect(plan.takeSealed()[0]!.output).to.equal('✓ Created OPS-12 · Approved');
    });

    it('shows a blocked call with its reason', () => {
      const plan = new SlackTaskPlan();
      run(plan, 'jira_create', 'Created an issue', 'c1', '', 'blocked', 'Not allowed here');
      plan.endGroup();
      expect(plan.takeSealed()[0]).to.deep.include({ status: 'error', output: '✗ Not allowed here' });
    });

    it('puts a sub-agent in a row of its own, its calls named on their lines', () => {
      const plan = new SlackTaskPlan();
      expect(plan.subAgent('Researcher', 'started')[0]).to.deep.include({ title: 'Researcher', status: 'in_progress' });
      plan.toolStarted('web_search', 'Searched the web', 'c1', { agent: 'Researcher' });
      plan.toolArgs('web_search', { summary: 'Searched for "Q3"' }, 'c1');
      plan.toolFinished('web_search', 'Searched the web', 'completed', 'c1', { summary: '4 results' });
      const [row] = plan.subAgent('Researcher', 'completed');
      expect(row).to.deep.equal({
        type: 'task_update',
        id: row!.id,
        title: 'Researcher',
        status: 'complete',
        output: '✓ Searched the web: "Q3" → 4 results',
      });
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

    it('shows narration as plain rows: a title, never details or output', () => {
      const plan = new SlackTaskPlan();
      const rows = plan.narration('Let me check the trust page.');
      expect(rows).to.deep.equal([{ type: 'task_update', id: rows[0]!.id, title: 'Let me check the trust page.', status: 'complete' }]);
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
    it('shows scalars as key: value, skips nested values and masks secret keys', () => {
      expect(compactArgs('{"project":"OPS","limit":5,"filter":{"a":1},"password":"hunter2","botToken":"abc"}')).to.equal(
        'project: OPS, limit: 5, password: [redacted], botToken: [redacted]',
      );
      expect(compactArgs('not json')).to.equal('');
    });

    const CASES: [string, string, string][] = [
      ['a JSON value with spaces', '{"api_key": "my secret value", "q": "x"}', '{"api_key": "[redacted]", "q": "x"}'],
      ['snake, kebab and camel keys', 'auth_token=abc bot-token: def', 'auth_token=[redacted]'],
      ['secret_key', 'secret_key: s3cr3t', 'secret_key: [redacted]'],
      ['private_key', '"private_key": "-----BEGIN KEY-----"', '"private_key": "[redacted]"'],
      ['aws_secret_access_key', 'aws_secret_access_key=wJalr', 'aws_secret_access_key=[redacted]'],
      ['api-key', 'api-key: k-1', 'api-key: [redacted]'],
      ['apiKey', "apiKey='k 2'", "apiKey='[redacted]'"],
      ['a query parameter', 'Read https://api.test/v1?key=abc&q=x', 'Read https://api.test/v1?key=[redacted]&q=x'],
      ['URL user info', 'Fetched https://user:pa55@host.test/a', 'Fetched https://***@host.test/a'],
      ['an Authorization header', 'Authorization: Basic dXNlcjpwYXNz', 'Authorization: [redacted]'],
      ['a bare bearer token', 'sent Bearer abcdefghijkl', 'sent Bearer [redacted]'],
      ['github_pat_', 'github_pat_11ABCDEF_xyz', '[redacted]'],
      ['ghp_', 'ghp_abcdefghijklmnopqrstuvwxyz01', '[redacted]'],
      ['glpat-', 'glpat-abcdefghij1234', '[redacted]'],
      ['xoxe-', 'xoxe-1-abc', '[redacted]'],
      ['sk-ant-', 'sk-ant-api03-abc', '[redacted]'],
      ['a password with spaces to the end of the line', 'password: correct horse battery\nnext', 'password: [redacted]\nnext'],
      ['a --password flag', '--password=hunter2', '--password=[redacted]'],
      ['a --token flag in a command', 'curl --token=abc123 x', 'curl --token=[redacted]'],
      ['an --api-key flag', 'run --api-key=abc', 'run --api-key=[redacted]'],
    ];
    for (const [name, input, expected] of CASES) {
      it(`masks ${name}`, () => {
        expect(redactSecrets(input)).to.equal(expected);
      });
    }

    it('stays fast on long runs of dots and dashes', () => {
      for (const unit of ['a.', 'ab-']) {
        const text = unit.repeat(Math.ceil(100_000 / unit.length));
        const started = Date.now();
        redactSecrets(text);
        expect(Date.now() - started, unit).to.be.below(100);
      }
    });

    it('keeps ordinary words', () => {
      expect(redactSecrets('Searched for "password reset policy"')).to.equal('Searched for "password reset policy"');
    });

    const KEPT = ['monkey: banana', 'keyboard=qwerty', 'author: Jane Doe', 'authority: FDA', 'title: Bug, labels: a'];
    for (const input of KEPT) {
      it(`keeps "${input}": no segment of the key names a secret`, () => {
        expect(redactSecrets(input)).to.equal(input);
      });
    }

    it('reads key names by segment', () => {
      for (const name of ['api_key', 'apiKey', 'x-api-key', 'secret_key', 'APIKey', 'clientSecret', 'auth.token', 'credentials']) {
        expect(isSecretKey(name), name).to.equal(true);
      }
      for (const name of ['monkey', 'keyboard', 'author', 'authority', 'tokenizer', 'keyword']) {
        expect(isSecretKey(name), name).to.equal(false);
      }
      expect(redactSecrets('title: Bug, token: abc')).to.equal('title: Bug, token: [redacted]');
    });

    it('masks secrets in results too', () => {
      const plan = new SlackTaskPlan();
      plan.toolStarted('http__get', 'Called an API', 'c1');
      plan.toolFinished('http__get', 'Called an API', 'completed', 'c1', { summary: 'Got token=abc123 back' });
      plan.endGroup();
      expect(plan.takeSealed()[0]!.output).to.equal('✓ Got token=[redacted]');
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

    it('drops secret query parameters, masks secrets in a title and leaves out a link with a token in it', () => {
      expect(viewSources({
        kind: 'records',
        rows: [
          { cells: ['Report token=abc123'], url: 'https://h.example/x?api_key=SEKRET123&page=2' },
          { cells: ['Leaky'], url: 'https://h.example/u/ghp_abcdefghijklmnopqrstuvwxyz0123' },
        ],
      })).to.deep.equal([{ type: 'url', url: 'https://h.example/x?page=2', text: 'Report token=[redacted]' }]);
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
        chunks: [{ type: 'task_update', id: 'tools-1', title: 'Searching', status: 'error', output: '• Searching' }],
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

    describe('sends a row\'s details and output once, as Slack adds every resend to what it shows', () => {
      const tsOf = (n: number) => `s${String(n)}`;
      const tooLong = () => Object.assign(new Error('big'), { data: { error: 'msg_blocks_too_long' } });
      /** The rows as Slack shows them, each row's details sent at most once. */
      const shown = (c: ReturnType<typeof slackClient>, refused: ReadonlySet<number> = new Set()) => {
        const rows = replayRows(c, tsOf, refused);
        for (const row of rows) expect(row.detailSends, `${row.id} sends its details once`).to.be.at.most(1);
        expect(new Set(rows.map((r) => r.id)).size, 'no row repeats').to.equal(rows.length);
        return rows;
      };
      const setup = () => {
        const parts = stream();
        // As the entry does: rows sealed meanwhile go first.
        const push = (rows: SlackTaskChunk[]) => parts.s.tasks([...parts.plan.takeSealed(), ...rows]);
        const search = async (id: string, query: string, status = 'completed', summary = '4 results') => {
          await push(parts.plan.toolStarted('kb__search', 'Searched the knowledge base', id));
          await push(parts.plan.toolArgs('kb__search', { summary: `Searched for "${query}"` }, id));
          await push(parts.plan.toolFinished('kb__search', 'Searched the knowledge base', status, id, { summary }));
        };
        return { ...parts, push, search };
      };

      it('1) seals a group once, when the next step starts', async () => {
        const { c, s, plan, push, search } = setup();
        await search('c1', 'a');
        await search('c2', 'b');
        await push(plan.narration('Checking one more thing.'));
        await s.stop();

        const [row, note] = shown(c);
        expect(row).to.deep.include({ title: 'Searched the knowledge base', status: 'complete', details: 'Kb', output: '✓ "a" → 4 results\n✓ "b" → 4 results' });
        expect(note).to.deep.include({ title: 'Checking one more thing.', details: '', output: '' });
      });

      it('2) shows a failed call once', async () => {
        const { c, s, plan, push } = setup();
        await push(plan.toolStarted('artifacts__get_record_download_url', 'Got record download links', 'c1'));
        await push(plan.toolArgs('artifacts__get_record_download_url', { summary: 'record_id: 8dc0d3d5-4b1e-4c2a-9f3d-2a6b7c8d9e0f' }, 'c1'));
        await push(plan.toolFinished('artifacts__get_record_download_url', 'Got record download links', 'failed', 'c1', { summary: 'Failed: No download link.' }));
        await s.stop();

        expect(shown(c)).to.deep.equal([{
          id: 'tools-1', title: 'Got record download links', status: 'error', details: 'Artifacts',
          output: '✗ record 8dc0d3d5 → No download link', sources: [], detailSends: 1,
        }]);
      });

      it('3) lets a row that turned error take more calls, still sending them once', async () => {
        const { c, s, plan, push, search } = setup();
        await search('c1', 'a', 'failed', 'Failed: Timed out');
        expect(shown(c)[0]!.status).to.equal('error');
        await search('c2', 'b');
        await push(plan.toolFinished('kb__search', 'Searched the knowledge base', 'completed', 'c2', { summary: 'late repeat' }));
        await s.stop();

        expect(shown(c)[0]).to.deep.include({ status: 'complete', output: '✗ "a" → Timed out\n✓ "b" → Late repeat' });
      });

      it('4) holds a call waiting for approval, then shows how it was answered, once', async () => {
        const { c, s, plan, push } = setup();
        await push(plan.toolStarted('jira__create', 'Created an issue', 'c1'));
        await push(plan.toolFinished('jira__create', 'Created an issue', 'awaiting_approval', 'c1'));
        expect(shown(c)[0]).to.deep.include({ status: 'in_progress', output: '' });
        await push(plan.toolStarted('jira__create', 'Created an issue', 'c1', { approvalAnswer: 'allowed' }));
        await push(plan.toolFinished('jira__create', 'Created an issue', 'completed', 'c1', { summary: 'Created OPS-12' }));
        await s.stop();

        expect(shown(c)[0]).to.deep.include({ title: 'Created an issue', status: 'complete', output: '✓ Created OPS-12 · Approved' });
      });

      it('5) seals a sub-agent row when the sub-agent ends', async () => {
        const { c, s, plan, push } = setup();
        await push(plan.subAgent('Researcher', 'started'));
        await push(plan.toolStarted('web_search', 'Searched the web', 'c1', { agent: 'Researcher' }));
        await push(plan.toolArgs('web_search', { summary: 'Searched for "Q3"' }, 'c1'));
        await push(plan.toolFinished('web_search', 'Searched the web', 'completed', 'c1', { summary: '4 results' }));
        await push(plan.subAgent('Researcher', 'completed'));
        await push(plan.narration('Done researching.'));
        await s.stop();

        expect(shown(c)[0]).to.deep.include({ title: 'Researcher', status: 'complete', output: '✓ Searched the web: "Q3" → 4 results' });
      });

      it('6) settles rows left open at stop, and ends the reply as failed elsewhere, each once', async () => {
        const { c, s, plan, push, search } = setup();
        await search('c1', 'a');
        await push(plan.toolStarted('web_search', 'Searched the web', 'c2'));
        await s.stop();
        await push(plan.toolFinished('web_search', 'Searched the web', 'completed', 'c2', { summary: 'too late' }));

        const rows = shown(c);
        expect(rows.map((r) => [r.status, r.output])).to.deep.equal([
          ['complete', '✓ "a" → 4 results'],
          ['error', '• Searched the web'],
        ]);
        expect(calls(c, 'chat.stopStream')[0].chunks, 'only the open row is settled').to.have.length(1);
      });

      it('7) shrinks a row Slack finds too big and never resends it or adds a "(continued)" row', async () => {
        const { c, s, plan, push, search } = setup();
        for (let i = 0; i < 6; i += 1) await search(`c${String(i)}`, `query ${String(i)}`);
        const refusedAt = c.apiCall.callCount;
        c.apiCall.withArgs('chat.appendStream').onCall(calls(c, 'chat.appendStream').length).rejects(tooLong());
        await push(plan.narration('Next.'));
        await s.stop();

        const rows = shown(c, new Set([refusedAt]));
        expect(rows.map((r) => r.title)).to.deep.equal(['Searched the knowledge base', 'Next.']);
        expect(rows[0]!.output.split('\n')).to.deep.equal([
          '✓ "query 0" → 4 results', '✓ "query 1" → 4 results', '✓ "query 2" → 4 results', '+3 more',
        ]);
      });

      it('7b) counts the calls a shrunk row already summed up', async () => {
        const { c, s, plan, push, search } = setup();
        for (let i = 0; i < 8; i += 1) await search(`c${String(i)}`, `q${String(i)}`);
        const refusedAt = c.apiCall.callCount;
        c.apiCall.withArgs('chat.appendStream').onCall(calls(c, 'chat.appendStream').length).rejects(tooLong());
        await push(plan.narration('Next.'));
        await s.stop();

        expect(shown(c, new Set([refusedAt]))[0]!.output.split('\n').pop()).to.equal('+5 more');
      });

      it('8) takes back a discarded model call without showing any row twice', async () => {
        const { c, s, plan, push, search } = setup();
        await search('c1', 'kept');
        await push(plan.narration('Kept note.'));
        s.markSegment();
        await s.answer('Wrong partial answer.');
        await search('c2', 'dropped');
        await s.dropSegment();
        await search('c3', 'after');
        await push(plan.narration('Answering now.'));
        await s.stop();

        const rows = shown(c);
        expect(rows.map((r) => r.output || r.title)).to.deep.equal([
          '✓ "kept" → 4 results', 'Kept note.', '✓ "dropped" → 4 results\n✓ "after" → 4 results', 'Answering now.',
        ]);
      });

      it('9) keeps rows of a full message there when the reply goes on in a new one', async () => {
        const { c, s, plan, push, search } = setup();
        await search('c1', 'a');
        await push(plan.narration('Writing.'));
        const line = `${'x'.repeat(99)}\n`;
        await s.answer(line.repeat(Math.ceil(SLACK_STREAM_MESSAGE_CHAR_LIMIT / line.length) + 5));
        await search('c2', 'b');
        await push(plan.narration('More.'));
        await s.stop();

        expect(calls(c, 'chat.startStream')).to.have.length(2);
        expect(shown(c).map((r) => r.output || r.title)).to.deep.equal([
          '✓ "a" → 4 results', 'Writing.', '✓ "b" → 4 results', 'More.',
        ]);
      });

      it('10) gives calls of the same kind after a sealed row a new row, leaving the old one as it was', async () => {
        const { c, s, plan, search } = setup();
        await search('c1', 'a');
        plan.endGroup();
        await search('c2', 'b');
        await search('c3', 'c');
        await s.stop();

        expect(shown(c).map((r) => [r.id, r.output])).to.deep.equal([
          ['tools-1', '✓ "a" → 4 results'],
          ['tools-2', '✓ "b" → 4 results\n✓ "c" → 4 results'],
        ]);
      });
      it('11) moves a row still open when its message expires to the next message, sealing it there once', async () => {
        const { c, s, plan, push } = setup();
        await push(plan.toolStarted('kb__search', 'Searched the knowledge base', 'c1'));
        c.apiCall.withArgs('chat.appendStream').onCall(calls(c, 'chat.appendStream').length).rejects(expired());
        await push(plan.toolArgs('kb__search', { summary: 'Searched for "a"' }, 'c1'));
        await push(plan.toolFinished('kb__search', 'Searched the knowledge base', 'completed', 'c1', { summary: '4 results' }));
        await push(plan.narration('Next.'));
        await s.stop();

        const rows = replayRows(c, tsOf);
        for (const row of rows) expect(row.detailSends, row.id).to.be.at.most(1);
        const copies = rows.filter((r) => r.id === 'tools-1');
        expect(copies.map((r) => [r.status, r.output])).to.deep.equal([
          ['in_progress', ''],
          ['complete', '✓ "a" → 4 results'],
        ]);
      });

      it('12) settles an open row by title and status when the answer rolls over, and seals it once in the next message', async () => {
        const { c, s, plan, push } = setup();
        await push(plan.toolStarted('kb__search', 'Searched the knowledge base', 'c1'));
        const line = `${'x'.repeat(99)}\n`;
        await s.answer(line.repeat(Math.ceil(SLACK_STREAM_MESSAGE_CHAR_LIMIT / line.length) + 5));
        await push(plan.toolArgs('kb__search', { summary: 'Searched for "a"' }, 'c1'));
        await push(plan.toolFinished('kb__search', 'Searched the knowledge base', 'completed', 'c1', { summary: '4 results' }));
        await push(plan.narration('Next.'));
        await s.stop();

        const [settle] = calls(c, 'chat.stopStream');
        expect(settle.chunks[0]).to.deep.equal({ type: 'task_update', id: 'tools-1', title: 'Searched the knowledge base', status: 'complete' });
        const rows = replayRows(c, tsOf);
        for (const row of rows) expect(row.detailSends, row.id).to.be.at.most(1);
        expect(rows.filter((r) => r.id === 'tools-1').map((r) => [r.status, r.output])).to.deep.equal([
          ['complete', ''],
          ['complete', '✓ "a" → 4 results'],
        ]);
      });

      it('13) names apps and actions in plain words, never internal names', async () => {
        const { c, s, plan, push } = setup();
        await push(plan.toolStarted('fetch_tools', 'Fetch Tools', 'c1'));
        await push(plan.toolArgs('fetch_tools', { summary: 'Loading tools for "mcp_tracker"' }, 'c1'));
        await push(plan.toolFinished('fetch_tools', 'Fetch Tools', 'completed', 'c1', { summary: 'Loaded 3 tools' }));
        await push(plan.toolStarted('mcp_tracker_create_ticket', 'Tracker · create_ticket', 'c2'));
        await push(plan.toolArgs('mcp_tracker_create_ticket', { summary: 'Create Ticket — title: "Think", password: "hunter2"' }, 'c2'));
        await push(plan.toolFinished('mcp_tracker_create_ticket', 'Tracker · create_ticket', 'blocked', 'c2'));
        await push(plan.toolStarted('mcp_tracker_create_ticket', 'Tracker · create_ticket', 'c3'));
        await push(plan.toolFinished('mcp_tracker_create_ticket', 'Tracker · create_ticket', 'blocked', 'c3', { summary: 'Declined by the user.' }));
        await s.stop();

        const rows = shown(c).map(({ title, status, details, output }) => ({ title, status, details, output }));
        expect(rows).to.deep.equal([
          { title: 'Loaded tools', status: 'complete', details: '', output: '✓ Tracker → Loaded 3 tools' },
          {
            title: 'Create ticket', status: 'error', details: 'Tracker',
            output: '✗ "Think" → Needs approval in the PipesHub app\n✗ Declined by the user',
          },
        ]);
        expect(JSON.stringify(rows)).to.not.match(/hunter2|mcp_tracker|create_ticket|\bMCP\b/);
      });

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
        { type: 'task_update', id: 'tools-1', title: 'Searched', status: 'complete', output: '✓ Searched' },
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

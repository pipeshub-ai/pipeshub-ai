import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import { Readable } from 'stream';
import { SlackPlanStream } from '../../../src/integrations/slack-bot/src/utils/plan-stream';

// Loads a Slack bot index.ts with Slack, storage and the registry faked, so a
// test can drive one answer through it and check what lands in the thread.

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Handler = (args: any) => Promise<void>;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type AnyArgs = any;

const BOT = {
  botId: 'cfg-1',
  agentId: null,
  botToken: 'xoxb-1',
  signingSecret: 's-1',
};
export const STREAM_TS = '300.1';
export const STATUS_TS = '200.1';

/** The run's frames, one chunk each, the way a live stream delivers them. */
export const sse = (...frames: Record<string, unknown>[]) =>
  Readable.from(
    (async function* live() {
      for (const f of frames) {
        // A moment between frames, so sends interleave with arriving text.
        await new Promise((resolve) => setImmediate(resolve));
        yield `event: ${String(f.type)}\ndata: ${JSON.stringify(f)}\n\n`;
      }
    })(),
  );

/** The answer as the model streams it: its citation is a placeholder. */
export const ANSWER = 'Revenue grew 12% [source](ref1).';
/** The same answer as the backend finishes it. */
export const NORMALIZED_ANSWER = 'Revenue grew 12% [1](/record/rec-1).';
/** What Slack shows: the placeholder as a plain marker that matches the Sources list. */
export const SHOWN_ANSWER = 'Revenue grew 12% [1].';

export const finished = (content: string, citations: unknown[] = []) => ({
  type: 'RUN_FINISHED',
  runId: 'r1',
  result: {
    conversation: {
      _id: 'conv-1',
      messages: [{ messageType: 'bot_response', content, citations }],
    },
  },
});

export const CITATIONS = [
  {
    citationData: {
      chunkIndex: 1,
      metadata: {
        recordId: 'rec-1',
        recordName: 'Q3 report',
        webUrl: 'https://x.test/q3',
      },
    },
  },
];

/** Text the way a model streams it: a few characters per frame. */
export const tokens = (text: string, size = 4): Record<string, unknown>[] =>
  (text.match(new RegExp(`[\\s\\S]{1,${String(size)}}`, 'g')) ?? []).map(
    (delta) => ({ type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta }),
  );

export const answerFrames = (toolStatus: string, preamble = '') => [
  { type: 'RUN_STARTED', runId: 'r1' },
  ...(preamble
    ? [{ type: 'TEXT_MESSAGE_START', runId: 'r1' }, ...tokens(preamble)]
    : []),
  {
    type: 'TOOL_CALL_START',
    runId: 'r1',
    toolCallId: 'c1',
    toolCallName: 'search',
  },
  {
    type: 'TOOL_CALL_RESULT',
    runId: 'r1',
    toolCallId: 'c1',
    status: toolStatus,
  },
  { type: 'TEXT_MESSAGE_START', runId: 'r1' },
  ...tokens(ANSWER),
  { type: 'TEXT_MESSAGE_END', runId: 'r1' },
  finished(NORMALIZED_ANSWER, CITATIONS),
];

export function loadBot(src: string, extraFakes: Record<string, unknown>) {
  const events = new Map<string, Handler>();
  const fakes: Record<string, unknown> = {
    [`${src}/slackApp.ts`]: {
      action: () => undefined,
      view: () => undefined,
      message: () => undefined,
      event: (name: string, h: Handler) => events.set(name, h),
      start: async () => undefined,
    },
    [`${src}/receiver.ts`]: {
      router: {
        use: () => undefined,
        get: () => undefined,
        post: () => undefined,
      },
    },
    [`${src}/botRegistry.ts`]: { getCurrentMatchedSlackBot: () => BOT },
    [`${src}/utils/db.ts`]: {
      connect: async () => undefined,
      dropLegacyThreadBotIndex: async () => undefined,
    },
    [`${src}/utils/conversation.ts`]: {
      getFromDatabase: async () => null,
      saveToDatabase: async () => undefined,
    },
    ...extraFakes,
  };
  const saved = new Map<string, NodeJS.Module | undefined>();
  for (const [file, exports] of Object.entries(fakes)) {
    let path: string;
    try {
      path = require.resolve(file);
    } catch {
      continue; // this checkout's bot doesn't import it
    }
    saved.set(path, require.cache[path]);
    require.cache[path] = {
      id: path,
      filename: path,
      loaded: true,
      exports: { default: exports, ...(exports as object) },
    } as never;
  }
  const indexPath = require.resolve(`${src}/index.ts`);
  saved.set(indexPath, require.cache[indexPath]);
  delete require.cache[indexPath];
  require(indexPath);
  const restore = () => {
    for (const [path, mod] of saved) {
      if (mod) require.cache[path] = mod;
      else delete require.cache[path];
    }
  };
  return { events, restore };
}

export function slackClient(startStreamFails = false) {
  let opened = 0;
  return {
    users: {
      info: sinon
        .stub()
        .resolves({ user: { profile: { email: 'owner@example.com' } } }),
    },
    auth: { test: sinon.stub().resolves({ user_id: 'UBOT', bot_id: 'BBOT' }) },
    chat: {
      postMessage: sinon.stub().resolves({ ok: true, ts: STATUS_TS }),
      update: sinon.stub().resolves({ ok: true }),
      postEphemeral: sinon.stub().resolves({ ok: true }),
    },
    conversations: { replies: sinon.stub().resolves({ messages: [] }) },
    apiCall: sinon.stub().callsFake(async (method: string) => {
      if (method !== 'chat.startStream') return { ok: true };
      if (startStreamFails)
        throw Object.assign(new Error('nope'), {
          data: { error: 'not_allowed' },
        });
      opened += 1;
      return {
        ok: true,
        ts: opened === 1 ? STREAM_TS : `300.${String(opened)}`,
      };
    }),
  };
}

export const apiCalls = (
  client: ReturnType<typeof slackClient>,
  method: string,
): AnyArgs[] =>
  client.apiCall
    .getCalls()
    .filter((c) => c.args[0] === method)
    .map((c) => c.args[1] as AnyArgs);

/** Every chunk the reply streamed, opening chunks included. */
export const streamedChunks = (
  client: ReturnType<typeof slackClient>,
): AnyArgs[] =>
  [
    ...apiCalls(client, 'chat.startStream'),
    ...apiCalls(client, 'chat.appendStream'),
  ].flatMap((call) => call.chunks as AnyArgs[]);

export const streamedText = (client: ReturnType<typeof slackClient>): string =>
  streamedChunks(client)
    .filter((c) => c.type === 'markdown_text')
    .map((c) => c.text as string)
    .join('');

/** The answer text still in the thread: streamed text of the messages that were not deleted. */
export function visibleText(client: ReturnType<typeof slackClient>): string {
  const deleted = new Set(
    apiCalls(client, 'chat.delete').map((d) => d.ts as string),
  );
  const byTs = new Map<string, string>();
  // Opened messages get their ts in call order: 300.1, 300.2, ...
  let opened = 0;
  for (const call of client.apiCall.getCalls()) {
    const [method, args] = call.args as [string, AnyArgs];
    let ts: string | undefined;
    if (method === 'chat.startStream') {
      opened += 1;
      ts = opened === 1 ? STREAM_TS : `300.${String(opened)}`;
    } else if (method === 'chat.appendStream' || method === 'chat.stopStream') {
      ts = args.ts as string;
    }
    if (!ts) continue;
    const text = ((args.chunks ?? []) as AnyArgs[])
      .filter((c) => c.type === 'markdown_text')
      .map((c) => c.text as string)
      .join('');
    byTs.set(ts, (byTs.get(ts) ?? '') + text);
  }
  return [...byTs.entries()]
    .filter(([ts]) => !deleted.has(ts))
    .map(([, text]) => text)
    .join('');
}

/** Text and steps in the order they went to the stream. */
export const streamOrder = (client: ReturnType<typeof slackClient>): string[] =>
  streamedChunks(client).map((c) =>
    c.type === 'task_update'
      ? `[${c.id as string}:${c.status as string}]`
      : c.type === 'plan_update'
        ? `[plan:${c.title as string}]`
        : (c.text as string),
  );

/** Each step's last state as the reply ended. */
export const finalSteps = (client: ReturnType<typeof slackClient>): Map<string, AnyArgs> => {
  const steps = new Map<string, AnyArgs>();
  const chunks = [
    ...streamedChunks(client),
    ...apiCalls(client, 'chat.stopStream').flatMap((stop) => (stop.chunks ?? []) as AnyArgs[]),
  ];
  for (const c of chunks) {
    if (c.type === 'task_update') steps.set(c.id as string, c);
  }
  return steps;
};

export function expectStreamedAnswer(
  client: ReturnType<typeof slackClient>,
  toolEnd: string,
): void {
  const [start] = apiCalls(client, 'chat.startStream');
  expect(start).to.deep.include({ thread_ts: '100.1', task_display_mode: 'plan' });
  expect(start.chunks[0]).to.deep.equal({ type: 'plan_update', title: 'Thinking' });
  const everyCall = [
    ...apiCalls(client, 'chat.startStream'),
    ...apiCalls(client, 'chat.appendStream'),
  ];
  expect(everyCall.every((call) => !('markdown_text' in call))).to.equal(true);

  // The step, then the answer streamed after it with its citation as a marker.
  const order = streamOrder(client);
  expect(order.filter((c) => c.startsWith('['))).to.include(
    `[tools-1:${toolEnd === 'complete' ? 'complete' : 'error'}]`,
  );
  expect(streamedText(client)).to.equal(SHOWN_ANSWER);

  const [stop] = apiCalls(client, 'chat.stopStream');
  expect(stop.ts).to.equal(STREAM_TS);
  const sources = JSON.stringify(stop.blocks);
  expect(sources)
    .to.include('Sources')
    .and.include('[1] ')
    .and.include('Q3 report')
    .and.include('https://x.test/q3');

  expect(client.chat.update.called, 'no chat.update on the answer').to.equal(
    false,
  );
  // The timeline shows progress itself: no status message.
  expect(client.chat.postMessage.called).to.equal(false);
  expect(apiCalls(client, 'chat.delete')).to.deep.equal([]);
}

export function expectPostedAnswer(
  client: ReturnType<typeof slackClient>,
): void {
  const posts = client.chat.postMessage
    .getCalls()
    .map((c) => c.args[0] as AnyArgs);
  expect(posts[0]).to.include({ thread_ts: '100.1', text: '_Thinking..._' });
  const answer = posts.find(
    (m) =>
      Array.isArray(m.blocks) && JSON.stringify(m.blocks).includes('Revenue'),
  );
  expect(answer, 'final answer').to.not.equal(undefined);
  expect(answer.text).to.include('Revenue grew 12%');
  const sections = (answer.blocks as AnyArgs[]).filter(
    (b) => b.type === 'section',
  );
  expect(sections.length).to.be.greaterThan(0);
  expect(sections.every((b) => b.expand === true)).to.equal(true);
  expect(apiCalls(client, 'chat.delete')).to.deep.equal([
    { channel: 'C1', ts: STATUS_TS },
  ]);
}

/**
 * The thread-level checks every Slack bot entry must pass: `src` is the
 * entry's directory, `stubConfig` fakes its ConfigService, `fakes` replaces
 * modules its startup would otherwise connect to.
 */
export function describeAnswerWiring(
  title: string,
  src: string,
  stubConfig: () => void,
  fakes: Record<string, unknown> = {},
): void {
  describe(title, () => {
    let bot: ReturnType<typeof loadBot>;
    let post: sinon.SinonStub;

    before(() => {
      sinon.stub(console, 'log');
      sinon.stub(console, 'error');
      stubConfig();
      // No pause between stream calls in tests.
      SlackPlanStream.minIntervalMs = 0;
      bot = loadBot(src, fakes);
    });

    after(() => {
      sinon.restore();
      bot.restore();
    });

    beforeEach(() => {
      post = sinon.stub(axios, 'post');
    });

    afterEach(() => post.restore());

    async function ask(
      frames: Record<string, unknown>[],
      startStreamFails = false,
    ) {
      post.resolves({ data: sse(...frames) });
      const client = slackClient(startStreamFails);
      await bot.events.get('app_mention')!({
        event: {
          type: 'app_mention',
          user: 'U1',
          channel: 'C1',
          ts: '100.1',
          text: 'how did revenue do?',
        },
        client,
        context: { teamId: 'T1', botUserId: 'UBOT' },
      });
      return client;
    }

    it('streams the step and then the answer in one message, ending with the sources', async () => {
      expectStreamedAnswer(await ask(answerFrames('completed')), 'complete');
    });

    it('marks a failed tool as an error', async () => {
      expectStreamedAnswer(await ask(answerFrames('failed')), 'error');
    });

    it('streams a long answer live, in several appends', async () => {
      const answer = 'Asana grew revenue in the third quarter. '.repeat(100);
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer),
        finished(answer),
      ]);

      const appends = apiCalls(client, 'chat.appendStream').filter((a) =>
        (a.chunks as AnyArgs[]).some((c) => c.type === 'markdown_text'),
      );
      expect(appends.length).to.be.greaterThan(1);
      expect(visibleText(client)).to.equal(answer);
    });

    it('shows citation placeholders as [n] markers that match a numbered Sources list with real links', async () => {
      const answer =
        'Revenue grew [source](ref1). The [Asana Trust Center](https://ref2.xyz) lists the report https://ref1.xyz.';
      const cites = [
        {
          citationData: {
            chunkIndex: 1,
            metadata: {
              recordId: 'r1',
              recordName: 'Q3 report',
              webUrl: 'https://x.test/q3',
            },
          },
        },
        {
          citationData: {
            chunkIndex: 2,
            metadata: {
              recordId: 'r2',
              recordName: 'Trust at Asana',
              webUrl: 'https://asana.com/trust',
            },
          },
        },
      ];
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 3),
        finished('normalized', cites),
      ]);

      expect(visibleText(client)).to.equal(
        'Revenue grew [1]. The Asana Trust Center [2] lists the report [1].',
      );
      const [stop] = apiCalls(client, 'chat.stopStream');
      const listed = JSON.stringify(stop.blocks);
      expect(listed)
        .to.include('"[1] "')
        .and.include('https://x.test/q3')
        .and.include('"[2] "')
        .and.include('https://asana.com/trust');
      const everything = JSON.stringify(
        client.apiCall.getCalls().map((c) => c.args),
      );
      expect(everything).to.not.match(/ref\d+\.xyz|\(ref\d+\)/);
    });

    it('never gives two different citations the same [n] in one reply', async () => {
      const opening = 'The older filing [source](ref9) covers this. '.repeat(40);
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(opening, 40),
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'search' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);

      const text = visibleText(client);
      expect(text).to.include('The older filing [1] covers this.');
      expect(text.endsWith('Revenue grew 12% [2].')).to.equal(true);
    });

    it('keeps line breaks, headings and ordinary links of a multi-paragraph answer streamed in small deltas', async () => {
      const answer = [
        '## Asana overview',
        '',
        'Asana is a work-management platform.',
        '',
        '## SOC 3 report',
        '',
        'You can find it in the [Asana Trust Center](https://asana.com/trust); the report is listed there.',
      ].join('\n');
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 3),
        finished(answer),
      ]);

      expect(visibleText(client)).to.equal(answer);
    });

    it('groups tool calls with no text between them into one step, listing every call, like the dashboard', async () => {
      const call = (id: string, name: string, args: string, status = 'completed', summary?: string) => [
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: id, toolCallName: name },
        { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: id, delta: '{}', argsSummary: args },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: id, status, ...(summary ? { resultSummary: summary } : {}) },
      ];
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        ...call('c1', 'knowledgegraph__search', 'Searched for "Asana SOC 3"', 'completed', '3 results\nDoc A'),
        ...call('c2', 'knowledgegraph__search', 'Searched for "Asana revenue"', 'failed', 'no results'),
        ...call('c3', 'knowledgegraph__semantic_search', 'Searched for "Q3"'),
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens('The first lookup was inconclusive.'),
        ...call('c4', 'fetch_url', 'Fetched asana.com'),
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);

      const steps = [...finalSteps(client).values()];
      expect(steps.map((t) => t.title)).to.deep.equal([
        'Knowledgegraph: Explored 3 searches',
        'The first lookup was inconclusive.',
        'Fetch Url',
      ]);
      expect(steps[0]).to.deep.include({
        status: 'error',
        details: '3 calls · 1 failed',
        output: [
          '✓ Search',
          'Arguments: Searched for "Asana SOC 3"',
          'Result: 3 results',
          'Doc A',
          '',
          '✗ Search',
          'Arguments: Searched for "Asana revenue"',
          'Result: Failed: no results',
          '',
          '✓ Semantic Search',
          'Arguments: Searched for "Q3"',
        ].join('\n'),
      });
      expect(steps[1]).to.deep.equal({ type: 'task_update', id: steps[1].id, title: 'The first lookup was inconclusive.', status: 'complete' });
      expect(steps[2]).to.deep.include({ status: 'complete', output: 'Arguments: Fetched asana.com' });
      expect(steps[2]).to.not.have.property('details');
      expect(streamedText(client)).to.equal(SHOWN_ANSWER);
    });

    it('lists every call of a long group', async () => {
      const frames = Array.from({ length: 6 }, (_, i) => [
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: `c${String(i)}`, toolCallName: 'web_search' },
        { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: `c${String(i)}`, delta: '{}', argsSummary: `Searched for "query ${String(i)}"` },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: `c${String(i)}`, status: 'completed', resultSummary: `${String(i)} results` },
      ]).flat();
      const client = await ask([{ type: 'RUN_STARTED', runId: 'r1' }, ...frames, finished('')]);

      const [group] = [...finalSteps(client).values()];
      expect(group.title).to.equal('Explored 6 searches');
      expect(group.details).to.equal('6 calls');
      expect((group.output as string).split('\n\n')).to.deep.equal(
        Array.from({ length: 6 }, (_, i) =>
          ['✓ Web Search', `Arguments: Searched for "query ${String(i)}"`, `Result: ${String(i)} results`].join('\n'),
        ),
      );
    });

    it('keeps the plan and every step in the message after the answer is done', async () => {
      const client = await ask(answerFrames('completed', 'Let me check the reports.'));

      expect(apiCalls(client, 'chat.delete')).to.deep.equal([]);
      expect(apiCalls(client, 'chat.startStream')).to.have.length(1);
      const [start] = apiCalls(client, 'chat.startStream');
      expect(start.chunks[0]).to.deep.equal({ type: 'plan_update', title: 'Thinking' });
      const steps = [...finalSteps(client).values()];
      expect(steps.map((t) => [t.title, t.status])).to.deep.equal([
        ['Let me check the reports.', 'complete'],
        ['Search', 'complete'],
      ]);
    });

    it('lists only real pages as a step sources', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        {
          type: 'TOOL_CALL_START',
          runId: 'r1',
          toolCallId: 'c1',
          toolCallName: 'web_search',
        },
        {
          type: 'TOOL_CALL_RESULT',
          runId: 'r1',
          toolCallId: 'c1',
          status: 'completed',
          content: 'URL: https://ref83.xyz',
          resultView: {
            kind: 'records',
            columns: ['Title'],
            total: 2,
            rows: [
              { cells: ['Trust at Asana'], url: 'https://asana.com/trust' },
              { cells: ['Placeholder'], url: 'https://ref83.xyz' },
            ],
          },
        },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);

      const done = streamedChunks(client)
        .filter((c) => c.type === 'task_update')
        .pop();
      expect(done.sources).to.deep.equal([
        { type: 'url', url: 'https://asana.com/trust', text: 'Trust at Asana' },
      ]);
      expect(
        JSON.stringify(client.apiCall.getCalls().map((c) => c.args)),
      ).to.not.match(/ref\d+\.xyz/);
    });

    it('shows a short opening before a step once, in full, as its own row in the plan', async () => {
      const client = await ask(answerFrames('completed', 'Let me check the reports.'));

      const rows = [...finalSteps(client).values()];
      expect(rows[0]).to.deep.include({ title: 'Let me check the reports.', status: 'complete' });
      expect(rows.some((t) => t.details !== undefined)).to.equal(false);
      expect(streamedText(client)).to.equal(SHOWN_ANSWER);
    });

    it('splits a long opening into rows at sentence ends, never cutting it short', async () => {
      const sentence = 'I will check the revenue in the filings and the trust page. ';
      const opening = sentence.repeat(6).trim();
      const client = await ask(answerFrames('completed', opening));

      const rows = [...finalSteps(client).values()].filter((t) => String(t.id).startsWith('note-'));
      expect(rows.length).to.be.greaterThan(1);
      expect(rows.every((r) => (r.title as string).length <= 250 && (r.title as string).endsWith('.'))).to.equal(true);
      expect(rows.map((r) => r.title as string).join(' ')).to.equal(opening);
      expect(JSON.stringify(rows)).to.not.include('…');
    });

    it('shows an opening up to 1,500 characters before a step as rows of the plan, not answer text', async () => {
      const opening = `${'Here is what I know so far about revenue. '.repeat(30)}It grew in Q3.`;
      const client = await ask(answerFrames('completed', opening));

      expect(visibleText(client)).to.equal(SHOWN_ANSWER);
      const rows = [...finalSteps(client).values()].filter((t) => String(t.id).startsWith('note-'));
      expect(rows.map((r) => r.title as string).join(' ')).to.equal(opening);
    });

    it('sends the end of a longer opening, streamed token by token, before the step', async () => {
      const opening = `${'Here is what I know so far about revenue. '.repeat(40)}It grew in Q3`;
      const client = await ask(answerFrames('completed', opening));

      expect(visibleText(client)).to.equal(`${opening}\n\n${SHOWN_ANSWER}`);
    });

    it('takes back the text of a discarded model call and keeps the steps', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        {
          type: 'TOOL_CALL_START',
          runId: 'r1',
          toolCallId: 'c1',
          toolCallName: 'search',
        },
        {
          type: 'TOOL_CALL_RESULT',
          runId: 'r1',
          toolCallId: 'c1',
          status: 'completed',
        },
        { type: 'TEXT_MESSAGE_START', runId: 'r1', messageId: 'm1' },
        ...tokens('Revenue fell, according to the old report. '.repeat(40)),
        { type: 'TEXT_MESSAGE_END', runId: 'r1', messageId: 'm1' },
        {
          type: 'CUSTOM',
          runId: 'r1',
          name: 'message_discarded',
          value: { messageId: 'm1' },
        },
        { type: 'TEXT_MESSAGE_START', runId: 'r1', messageId: 'm2' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);

      expect(visibleText(client)).to.equal(SHOWN_ANSWER);
      expect(apiCalls(client, 'chat.delete')).to.deep.equal([
        { channel: 'C1', ts: STREAM_TS },
      ]);
      const [, reopened] = apiCalls(client, 'chat.startStream');
      expect(reopened.chunks[0]).to.deep.equal({ type: 'plan_update', title: 'Thinking' });
      expect(reopened.chunks[1]).to.deep.include({ id: 'tools-1', status: 'complete' });
    });

    it('never sends an artifact marker split across flushes', async () => {
      const intro = 'The report is ready for you to download. '.repeat(10);
      const marker = '::artifact[report.pdf](record:abc){application/pdf||abc}';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          runId: 'r1',
          delta: `${intro}${marker.slice(0, 20)}`,
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          runId: 'r1',
          delta: marker.slice(20, 40),
        },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          runId: 'r1',
          delta: `${marker.slice(40)} Done.`,
        },
        finished(`${intro}${marker} Done.`),
      ]);

      expect(visibleText(client)).to.equal(`${intro} Done.`);
    });

    it('streams the text before a table and puts the table below it as blocks', async () => {
      const intro = `${'Revenue by quarter, with the detail you asked for. '.repeat(10)}\n\n`;
      const answer = `${intro}| Quarter | Revenue |\n| --- | --- |\n| Q3 | 12% |\n\nThat is all.`;
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: intro },
        {
          type: 'TEXT_MESSAGE_CONTENT',
          runId: 'r1',
          delta: answer.slice(intro.length),
        },
        finished(answer),
      ]);

      expect(visibleText(client))
        .to.include('Revenue by quarter')
        .and.not.include('Quarter |');
      const [stop] = apiCalls(client, 'chat.stopStream');
      const blocks = stop.blocks as AnyArgs[];
      expect(blocks.some((b) => b.type === 'table')).to.equal(true);
      expect(JSON.stringify(blocks)).to.include('That is all.');
    });

    it('puts a table as blocks even when the answer came only with the end of the run', async () => {
      const answer = 'Revenue by quarter:\n\n| Quarter | Revenue |\n| --- | --- |\n| Q3 | 12% |\n\nThat is all.';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'search' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed' },
        finished(answer),
      ]);

      expect(streamedText(client)).to.include('Revenue by quarter:').and.not.include('|');
      const [stop] = apiCalls(client, 'chat.stopStream');
      expect((stop.blocks as AnyArgs[]).some((b) => b.type === 'table')).to.equal(true);
      expect(JSON.stringify(stop.blocks)).to.include('That is all.');
    });

    it('shows no steps for a run with none', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens('Hello there.'),
        finished('Hello there.'),
      ]);

      expect(
        streamedChunks(client).some((c) => c.type === 'task_update'),
      ).to.equal(false);
      expect(streamedText(client)).to.equal('Hello there.');
    });

    it('replaces the steps with the questions when the run asks one', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        {
          type: 'TOOL_CALL_START',
          runId: 'r1',
          toolCallId: 'c1',
          toolCallName: 'search',
        },
        {
          type: 'TOOL_CALL_RESULT',
          runId: 'r1',
          toolCallId: 'c1',
          status: 'completed',
        },
        {
          type: 'CUSTOM',
          runId: 'r1',
          name: 'ask_user_question',
          value: { questions: [{ question: 'Which quarter?' }] },
        },
        finished(''),
      ]);

      expect(apiCalls(client, 'chat.delete')).to.deep.equal([
        { channel: 'C1', ts: STREAM_TS },
      ]);
      const posted = client.chat.postMessage
        .getCalls()
        .map((c) => (c.args[0] as AnyArgs).text as string);
      expect(posted.some((t) => t.includes('Which quarter?'))).to.equal(true);
    });

    it('posts the error itself when the stream has expired', async () => {
      post.resolves({
        data: sse(
          { type: 'RUN_STARTED', runId: 'r1' },
          {
            type: 'TOOL_CALL_START',
            runId: 'r1',
            toolCallId: 'c1',
            toolCallName: 'search',
          },
          { type: 'RUN_ERROR', runId: 'r1', message: 'The model timed out' },
        ),
      });
      const client = slackClient();
      client.apiCall
        .withArgs('chat.stopStream')
        .rejects(
          Object.assign(new Error('done'), {
            data: { error: 'message_not_in_streaming_state' },
          }),
        );
      await bot.events.get('app_mention')!({
        event: {
          type: 'app_mention',
          user: 'U1',
          channel: 'C1',
          ts: '100.1',
          text: 'how did revenue do?',
        },
        client,
        context: { teamId: 'T1', botUserId: 'UBOT' },
      });

      const posted = client.chat.postMessage
        .getCalls()
        .map((c) => (c.args[0] as AnyArgs).text as string);
      expect(posted.join('\n')).to.include('The model timed out');
    });

    it('does not show a tool waiting for approval as failed', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        {
          type: 'TOOL_CALL_START',
          runId: 'r1',
          toolCallId: 'c1',
          toolCallName: 'jira_create',
        },
        {
          type: 'TOOL_CALL_RESULT',
          runId: 'r1',
          toolCallId: 'c1',
          status: 'awaiting_approval',
        },
        finished(''),
      ]);

      const states = streamedChunks(client)
        .concat(
          apiCalls(client, 'chat.stopStream').flatMap(
            (stop) => (stop.chunks ?? []) as AnyArgs[],
          ),
        )
        .filter((c) => c.type === 'task_update');
      // Waiting is not failing: it stays in progress, saying why, even after the reply stops.
      // At the end of the reply no spinner is left: the row says what it waits for.
      expect(states.pop()).to.deep.include({
        status: 'complete',
        title: 'Waiting for your approval: Jira Create',
        details: 'Waiting for your approval',
      });
    });

    it('replaces the status message with the failure notice when the posted answer is empty', async () => {
      const client = await ask(
        [
          { type: 'RUN_STARTED', runId: 'r1' },
          {
            type: 'TOOL_CALL_START',
            runId: 'r1',
            toolCallId: 'c1',
            toolCallName: 'search',
          },
          {
            type: 'TOOL_CALL_RESULT',
            runId: 'r1',
            toolCallId: 'c1',
            status: 'completed',
          },
          finished(''),
        ],
        true,
      );

      const edits = client.chat.update
        .getCalls()
        .map((c) => c.args[0] as AnyArgs);
      expect(edits.pop()).to.include({
        ts: STATUS_TS,
        text: 'Something went wrong while generating the response. Please try again later.',
      });
    });

    it('posts an expanded answer under a status message it then deletes when Slack cannot stream', async () => {
      expectPostedAnswer(await ask(answerFrames('completed'), true));
    });
  });
}

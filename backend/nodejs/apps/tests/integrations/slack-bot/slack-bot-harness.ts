import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import { Readable } from 'stream';

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

export const sse = (...frames: Record<string, unknown>[]) =>
  Readable.from([
    frames
      .map((f) => `event: ${f.type}\ndata: ${JSON.stringify(f)}\n\n`)
      .join(''),
  ]);

export const ANSWER = 'Revenue grew 12% [1](/record/rec-1).';

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

export const answerFrames = (toolStatus: string, preamble = '') => [
  { type: 'RUN_STARTED', runId: 'r1' },
  ...(preamble
    ? [
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: preamble },
      ]
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
  { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: ANSWER },
  { type: 'TEXT_MESSAGE_END', runId: 'r1' },
  finished(ANSWER, CITATIONS),
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
      return { ok: true, ts: opened === 1 ? STREAM_TS : `300.${String(opened)}` };
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
  const deleted = new Set(apiCalls(client, 'chat.delete').map((d) => d.ts as string));
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

export function expectStreamedAnswer(
  client: ReturnType<typeof slackClient>,
  toolEnd: string,
): void {
  const [start] = apiCalls(client, 'chat.startStream');
  expect(start).to.deep.include({
    thread_ts: '100.1',
    task_display_mode: 'plan',
  });
  expect(start.chunks[0]).to.deep.equal({
    type: 'plan_update',
    title: 'Thinking',
  });
  const everyCall = [
    ...apiCalls(client, 'chat.startStream'),
    ...apiCalls(client, 'chat.appendStream'),
  ];
  expect(everyCall.every((call) => !('markdown_text' in call))).to.equal(true);

  const tasks = streamedChunks(client).filter((c) => c.type === 'task_update');
  expect(tasks.every((t) => t.id === 'c1')).to.equal(true);
  expect(tasks.at(-1)?.status).to.equal(toolEnd);
  // Raw markdown, with the record citation as a plain marker that matches the sources.
  expect(streamedText(client)).to.equal('Revenue grew 12% [1].');

  const [stop] = apiCalls(client, 'chat.stopStream');
  expect(stop.ts).to.equal(STREAM_TS);
  const sources = JSON.stringify(stop.blocks);
  expect(sources)
    .to.include('Sources')
    .and.include('[1] ')
    .and.include('Q3 report');

  expect(client.chat.update.called, 'no chat.update on the answer').to.equal(
    false,
  );
  expect(apiCalls(client, 'chat.delete')).to.deep.equal([]);
  expect(
    client.chat.postMessage.called,
    'nothing posted besides the stream',
  ).to.equal(false);
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

    it('streams the plan and the answer in one message and ends with the sources', async () => {
      expectStreamedAnswer(await ask(answerFrames('completed')), 'complete');
    });

    it('marks a failed tool as an error in the plan', async () => {
      expectStreamedAnswer(await ask(answerFrames('failed')), 'error');
    });

    it('streams the text before a table and puts the table below it as blocks', async () => {
      const intro = `${'Revenue by quarter, with the detail you asked for. '.repeat(10)}\n\n`;
      const answer = `${intro}| Quarter | Revenue |\n| --- | --- |\n| Q3 | 12% |\n\nThat is all.`;
      // Two deltas: the intro streams, then the table stops the text stream.
      const frames = answerFrames('completed').flatMap((f) => {
        if (f.type === 'TEXT_MESSAGE_CONTENT') {
          return [
            { ...f, delta: intro },
            { ...f, delta: answer.slice(intro.length) },
          ];
        }
        return [f.type === 'RUN_FINISHED' ? finished(answer) : f];
      });

      const client = await ask(frames);

      const text = apiCalls(client, 'chat.appendStream')
        .flatMap((a) => a.chunks as AnyArgs[])
        .filter((c) => c.type === 'markdown_text')
        .map((c) => c.text as string)
        .join('');
      expect(text)
        .to.include('Revenue by quarter')
        .and.not.include('Quarter |');
      const [stop] = apiCalls(client, 'chat.stopStream');
      const blocks = stop.blocks as AnyArgs[];
      expect(blocks.some((b) => b.type === 'table')).to.equal(true);
      expect(JSON.stringify(blocks))
        .to.include('That is all.')
        .and.not.include('Revenue by quarter');
      expect(client.chat.update.called).to.equal(false);
    });

    it('shows no plan for a run with no steps', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: 'Hello there.' },
        finished('Hello there.'),
      ]);

      expect(
        streamedChunks(client).some((c) => c.type === 'plan_update'),
      ).to.equal(false);
      expect(streamedText(client)).to.equal('Hello there.');
    });

    it('keeps a short opening before a tool call as that step, not answer text', async () => {
      const client = await ask(
        answerFrames('completed', 'Let me check the reports.'),
      );

      expect(streamedText(client)).to.equal('Revenue grew 12% [1].');
      const [step] = streamedChunks(client).filter(
        (c) => c.type === 'task_update',
      );
      expect(step.details).to.equal('Let me check the reports.');
    });

    it('leaves a long opening in place when a tool call follows it', async () => {
      const opening = `${'Here is what I know so far about revenue. '.repeat(12)}\n`;
      const client = await ask(answerFrames('completed', opening));

      expect(streamedText(client)).to.equal(
        `${opening}\n\nRevenue grew 12% [1].`,
      );
      expect(apiCalls(client, 'chat.delete')).to.deep.equal([]);
    });

    it('sends the end of a long opening, streamed token by token, before the tool call', async () => {
      const opening = `${'Here is what I know so far about revenue. '.repeat(11)}It grew in Q3`;
      const tokens = opening.match(/.{1,4}/gs) ?? [];
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens.map((delta) => ({ type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta })),
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'search' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: ANSWER },
        finished(ANSWER, CITATIONS),
      ]);

      expect(visibleText(client)).to.equal(`${opening}\n\nRevenue grew 12% [1].`);
    });

    it('takes back the text of a discarded model call and keeps the plan', async () => {
      const partial = 'Revenue fell, according to the old report. '.repeat(10);
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'search' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1', messageId: 'm1' },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: partial },
        { type: 'TEXT_MESSAGE_END', runId: 'r1', messageId: 'm1' },
        { type: 'CUSTOM', runId: 'r1', name: 'message_discarded', value: { messageId: 'm1' } },
        { type: 'TEXT_MESSAGE_START', runId: 'r1', messageId: 'm2' },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: ANSWER },
        finished(ANSWER, CITATIONS),
      ]);

      expect(visibleText(client)).to.equal('Revenue grew 12% [1].');
      expect(apiCalls(client, 'chat.delete')).to.deep.equal([{ channel: 'C1', ts: STREAM_TS }]);
      const [, reopened] = apiCalls(client, 'chat.startStream');
      expect(reopened.chunks[0]).to.deep.equal({ type: 'plan_update', title: 'Thinking' });
      expect(reopened.chunks[1]).to.deep.include({ id: 'c1', status: 'complete' });
    });

    it('never sends an artifact marker split across flushes', async () => {
      const intro = 'The report is ready for you to download. '.repeat(10);
      const marker = '::artifact[report.pdf](record:abc){application/pdf||abc}';
      const content = `${intro}${marker} Done.`;
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: `${intro}${marker.slice(0, 20)}` },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: marker.slice(20, 40) },
        { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: `${marker.slice(40)} Done.` },
        finished(content),
      ]);

      const text = visibleText(client);
      expect(text).to.not.include('::artifact').and.not.include('application/pdf').and.not.include('record:');
      expect(text).to.equal(`${intro} Done.`);
    });

    it('replaces the plan with the questions when the run asks one', async () => {
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
        { channel: 'C1', ts: '300.1' },
      ]);
      const posted = client.chat.postMessage
        .getCalls()
        .map((c) => (c.args[0] as AnyArgs).text as string);
      expect(posted.some((t) => t.includes('Which quarter?'))).to.equal(true);
    });

    it('posts the error itself when the stream has expired', async () => {
      const client = slackClient();
      client.apiCall
        .withArgs('chat.stopStream')
        .rejects(
          Object.assign(new Error('done'), {
            data: { error: 'message_not_in_streaming_state' },
          }),
        );
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
            (s) => (s.chunks ?? []) as AnyArgs[],
          ),
        )
        .filter((c) => c.type === 'task_update' && c.id === 'c1');
      expect(states.pop()).to.deep.include({
        status: 'pending',
        output: 'Waiting for approval',
      });
    });

    it('deletes the status message and says the answer failed when the posted answer is empty', async () => {
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

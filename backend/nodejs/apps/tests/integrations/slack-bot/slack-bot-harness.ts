import { expect } from 'chai';
import sinon from 'sinon';
import axios from 'axios';
import { readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { Readable } from 'stream';
import { SlackPlanStream } from '../../../src/integrations/slack-bot/src/utils/plan-stream';

// Loads a Slack bot index.ts with Slack, storage and the registry faked, so a
// test can drive one answer through it and check what lands in the thread.
//
// Golden transcripts (golden/*.txt) are the Slack view of whole runs, the same
// for every entry. Regenerate them after a deliberate change with:
//   UPDATE_SLACK_GOLDEN=1 npx mocha tests/integrations/slack-bot/answer-stream.wiring.test.ts

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
/** A fake clock for runs with a quiet gap: a `{ type: 'WAIT', ms }` frame advances it. */
let runClock: sinon.SinonFakeTimers | undefined;
export function useRunClock(): sinon.SinonFakeTimers {
  runClock = sinon.useFakeTimers({ toFake: ['Date', 'setInterval', 'clearInterval'] });
  return runClock;
}
export function restoreRunClock(): void {
  runClock?.restore();
  runClock = undefined;
}

export const sse = (...frames: Record<string, unknown>[]) =>
  Readable.from(
    (async function* live() {
      for (const f of frames) {
        if (f.type === 'WAIT') {
          await runClock?.tickAsync(Number(f.ms));
          continue;
        }
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
export const SHOWN_ANSWER = 'Revenue grew 12% [1](https://x.test/q3).';

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

/** A Jira answer shaped like a live run's (fictional data): an intro, a ten-row table, then a closing sentence. */
export const JIRA_TABLE_ANSWER = [
  'Here are your 10 most recently updated unresolved Jira tickets, ordered newest first:',
  '',
  '| Ticket | Summary | Status | Priority | Last updated |',
  '|---|---|---|---|---|',
  '| DEMO-101 | Update the setup guide | In review | Medium | Tue, Nov 3, 11:35 AM |',
  '| DEMO-102 | Add retries to the sync job | Testing | High | Tue, Nov 3, 10:02 AM |',
  '| DEMO-103 | Show progress while the agent works | Testing | High | Mon, Nov 2, 6:40 PM |',
  '| DEMO-104 | Tidy the sign-in scopes | Testing | Medium | Mon, Nov 2, 4:15 PM |',
  '| DEMO-105 | Back off when indexing is busy | In progress | Medium | Mon, Nov 2, 1:05 PM |',
  '| DEMO-106 | Write the chat app setup page | To do | Low | Sun, Nov 1, 9:30 PM |',
  '| DEMO-107 | Tune search ranking | In progress | Medium | Sun, Nov 1, 3:12 PM |',
  '| DEMO-108 | Index page attachments | To do | Medium | Sat, Oct 31, 8:44 PM |',
  '| DEMO-109 | Add a rate-limit chart | In progress | Low | Sat, Oct 31, 11:20 AM |',
  '| DEMO-110 | Time out slow tool servers | To do | High | Fri, Oct 30, 7:58 PM |',
  '',
  'The most immediate follow-ups by status are DEMO-101 (In review) and DEMO-102, DEMO-103, DEMO-104 (Testing).',
].join('\n');

/** The Asana research run: narration, knowledge-base searches (one failing), failing and blocked calls, a cited answer. */
export function asanaResearchFrames(): Record<string, unknown>[] {
  const call = (id: string, name: string, label: string, args: string, status: string, summary?: string) => [
    { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: id, toolCallName: name, displayName: label },
    { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: id, delta: '{}', argsSummary: args },
    { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: id, status, ...(summary ? { resultSummary: summary } : {}) },
  ];
  const kb = (id: string, query: string, status = 'completed', summary = '4 results\nAsana SOC 3 report.pdf') =>
    call(id, 'knowledgegraph__search', 'Searched the knowledge base', `Searched for "${query}"`, status, summary);
  const cites = [
    { citationData: { chunkIndex: 1, metadata: { recordId: 'rec-1', recordName: 'Asana SOC 3 report', webUrl: 'https://trust.asana.com/soc3' } } },
    { citationData: { chunkIndex: 2, metadata: { recordId: 'rec-2', recordName: 'Asana Q3 FY2026 results', webUrl: 'https://investors.asana.com/q3' } } },
    // Another passage of the SOC 3 report: its own [n] link, the same line in Sources.
    { citationData: { chunkIndex: 3, metadata: { recordId: 'rec-1', recordName: 'Asana SOC 3 report', webUrl: 'https://trust.asana.com/soc3#page=4' } } },
  ];
  const answer = [
    '## Asana overview',
    '',
    'Asana is a work-management platform [source](ref1).',
    '',
    '## Third-quarter revenue',
    '',
    'Revenue grew to $201M in Q3 FY2026 [source](ref2), and the SOC 3 report says controls were fairly stated in all material respects [source](ref3).',
  ].join('\n');
  return [
  { type: 'RUN_STARTED', runId: 'r1' },
  { type: 'TEXT_MESSAGE_START', runId: 'r1' },
  ...tokens("I'll check our internal documents for Asana background, its third-quarter revenue, and the SOC 3 report."),
  ...kb('c1', 'Asana company overview and third quarter revenue financial results'),
  ...kb('c2', 'SOC 3 report Asana'),
  ...kb('c3', 'Asana trust center SOC 3'),
  { type: 'TEXT_MESSAGE_START', runId: 'r1' },
  ...tokens("I found Asana's SOC 3 report and a historical Q3 result. I'm checking for a newer third-quarter release."),
  ...kb('c4', 'Asana Q3 fiscal year 2026 third quarter revenue earnings results'),
  ...kb('c5', 'Asana Q3 fiscal 2025 third quarter revenue earnings results', 'failed', 'Failed: The search took too long'),
  ...call('c6', 'artifacts__get_record_download_url', 'Got record download links',
    'Get Record Download Url — record_id: 8dc0d3d5-4b1e-4c2a-9f3d-2a6b7c8d9e0f', 'failed',
    'Failed: No download link for records from an external connector. Ask an admin to enable downloads.'),
  ...call('c7', 'fetch_tools', 'Fetch Tools', 'Loading tools for "mcp_tracker"', 'completed', 'Loaded 3 tools'),
  { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c8', toolCallName: 'mcp_tracker_create_ticket', displayName: 'Tracker · create_ticket' },
  { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: 'c8', delta: '{}', argsSummary: 'Create Ticket — title: "Asana Q3 follow-up password=hunter2"' },
  // A blocked call's reason comes as its content.
  { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c8', status: 'blocked',
    content: "Create_ticket on Tracker needs someone to approve it in the PipesHub app, so it wasn't run." },
  { type: 'STATE_DELTA', runId: 'r1', delta: [{ op: 'replace', path: '/citations', value: cites }] },
  { type: 'TEXT_MESSAGE_START', runId: 'r1' },
  ...tokens(answer, 5),
  finished('normalized', cites),
];
}

const GOLDEN_DIR = join(__dirname, 'golden');

const goldenCall = (id: string, name: string, label: string, args: string, status: string, summary?: string) => [
  { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: id, toolCallName: name, displayName: label },
  { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: id, delta: '{}', argsSummary: args },
  { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: id, status, ...(summary ? { resultSummary: summary } : {}) },
];

/**
 * A saved conversation's parts (golden/<file>) as the stream events that
 * produced them: text parts as text (empty ones too), tool calls with their
 * display name, argument summary, result summary, view and preview.
 */
export function conversationFrames(file: string): Record<string, unknown>[] {
  const saved = JSON.parse(readFileSync(join(GOLDEN_DIR, file), 'utf8')) as { parts: AnyArgs[] };
  const frames: Record<string, unknown>[] = [{ type: 'RUN_STARTED', runId: 'r1' }];
  let answer = '';
  for (const part of saved.parts) {
    if (part.type === 'text') {
      frames.push({ type: 'TEXT_MESSAGE_START', runId: 'r1' });
      if (String(part.isFinal) === 'True') answer = String(part.content);
      frames.push(...tokens(String(part.content ?? ''), 40));
    } else if (part.type === 'tool_call') {
      frames.push(
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: part.toolCallId, toolCallName: part.toolName,
          ...(part.displayName ? { displayName: part.displayName } : {}) },
        { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: part.toolCallId, delta: part.args ?? '{}',
          ...(part.argsSummary ? { argsSummary: part.argsSummary } : {}) },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: part.toolCallId, status: part.status,
          content: part.resultPreview,
          ...(part.resultSummary !== undefined ? { resultSummary: part.resultSummary } : {}),
          ...(part.resultView ? { resultView: part.resultView } : {}),
          // The saved conversation records the file; the live AG-UI result does not forward it yet.
          ...(part.artifactId ? { artifactId: part.artifactId } : {}) },
      );
    }
  }
  frames.push(finished(answer));
  return frames;
}

/** Whole runs whose Slack view is kept in golden/<name>.txt. `clock` runs them with a fake clock for WAIT frames. */
export const GOLDEN_RUNS: { name: string; clock?: boolean; frames: () => Record<string, unknown>[] }[] = [
  { name: 'asana-research', frames: asanaResearchFrames },
  {
    name: 'jira-table',
    frames: () => [
      { type: 'RUN_STARTED', runId: 'r1' },
      { type: 'TEXT_MESSAGE_START', runId: 'r1' },
      ...tokens('Let me pull your unresolved Jira tickets.'),
      ...goldenCall('c1', 'jira__search_issues', 'Searched Jira', 'Searched for "assignee = currentUser() AND resolution = Unresolved"', 'completed', '10 results'),
      { type: 'TEXT_MESSAGE_START', runId: 'r1' },
      ...tokens(JIRA_TABLE_ANSWER, 40),
      finished(JIRA_TABLE_ANSWER),
    ],
  },
  {
    name: 'mixed-tools-with-a-gap',
    clock: true,
    frames: () => [
      { type: 'RUN_STARTED', runId: 'r1' },
      ...goldenCall('c1', 'web_search', 'Searched the web', 'Searched for "Asana Q3 2026 revenue"', 'completed', '6 results'),
      { type: 'WAIT', ms: 13_000 },
      { type: 'TEXT_MESSAGE_START', runId: 'r1' },
      ...tokens('The web results are thin, so I will check Confluence and Jira.'),
      ...goldenCall('c2', 'confluence__search_pages', 'Searched Confluence', 'Searched for "Q3 revenue"', 'failed', 'Failed: Confluence is not connected.'),
      ...goldenCall('c3', 'jira__search_issues', 'Searched Jira', 'Searched for "revenue report"', 'completed', 'No results'),
      ...goldenCall('c4', 'jira__search_issues', 'Searched Jira', 'Searched for "Q3 finance"', 'completed', '2 results'),
      { type: 'TEXT_MESSAGE_START', runId: 'r1' },
      ...tokens(ANSWER),
      finished(NORMALIZED_ANSWER, CITATIONS),
    ],
  },
  {
    // Its "Something went wrong" ending predates this view: the run ends with no answer while it waits.
    name: 'approval-waiting',
    frames: () => [
      { type: 'RUN_STARTED', runId: 'r1' },
      { type: 'TEXT_MESSAGE_START', runId: 'r1' },
      ...tokens('I will open the ticket for you.'),
      { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'jira__create_issue', displayName: 'Created a Jira issue' },
      { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: 'c1', delta: '{}', argsSummary: 'Creating "OPS: rotate keys"' },
      { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'awaiting_approval' },
      finished(''),
    ],
  },
  { name: 'jira-conversation', frames: () => conversationFrames('jira-conversation.json') },
  {
    name: 'five-sources',
    frames: () => {
      const docs = ['Pricing page', 'Q3 report', 'Security overview', 'Release notes', 'Support policy'];
      const cites = docs.map((name, i) => ({
        citationData: { chunkIndex: i + 1, metadata: { recordId: `doc-${String(i)}`, recordName: name, webUrl: `https://docs.example.com/${String(i + 1)}` } },
      }));
      const answer = docs.map((name, i) => `${name} covers it [source](ref${String(i + 1)}).`).join(' ');
      return [
        { type: 'RUN_STARTED', runId: 'r1' },
        ...goldenCall('c1', 'knowledgegraph__search', 'Searched the knowledge base', 'Searched for "plan details"', 'completed', '5 results'),
        { type: 'STATE_DELTA', runId: 'r1', delta: [{ op: 'replace', path: '/citations', value: cites }] },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 20),
        finished('normalized', cites),
      ];
    },
  },
  {
    name: 'long-answer-rollover',
    frames: () => {
      const answer = [
        '## Findings',
        '',
        'Revenue grew in every quarter, led by new seats [source](ref1). '.repeat(90),
        '',
        '## Detail',
        '',
        'Costs fell as hosting moved to reserved capacity. '.repeat(120),
        '',
        'In short: growth held and margins improved.',
      ].join('\n');
      return [
        { type: 'RUN_STARTED', runId: 'r1' },
        ...goldenCall('c1', 'knowledgegraph__search', 'Searched the knowledge base', 'Searched for "Q3 results"', 'completed', '4 results'),
        { type: 'STATE_DELTA', runId: 'r1', delta: [{ op: 'replace', path: '/citations', value: CITATIONS }] },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 300),
        finished('normalized', CITATIONS),
      ];
    },
  },
];

/** Every block the reply posted after its text: the stop's blocks, then any posted below it. */
export const replyBlocks = (client: ReturnType<typeof slackClient>): AnyArgs[] => [
  ...apiCalls(client, 'chat.stopStream').flatMap((c) => (c.blocks ?? []) as AnyArgs[]),
  ...client.chat.postMessage.getCalls().flatMap((c) => ((c.args[0] as AnyArgs).blocks ?? []) as AnyArgs[]),
];

/** No markdown_text the reply streamed holds a table row outside a code fence. */
export function expectNoStreamedTableRow(client: ReturnType<typeof slackClient>): void {
  let fenced = false;
  for (const line of streamedText(client).split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    if (!fenced) expect(line, 'a table row streamed as text').to.not.match(/^\s*\|.*\|\s*$/);
  }
}

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

/** A row as Slack would show it after replaying every update. */
export interface ReplayedRow {
  id: string;
  title: string;
  status: string;
  details: string;
  output: string;
  sources: AnyArgs[];
  /** How many updates carried details, output or sources (Slack adds each to what it shows). */
  detailSends: number;
}

/**
 * Replays the reply the way Slack applies it: a row's title and status
 * replace, while its details, output and sources are added to what it
 * already shows. Deleted messages drop out.
 */
export function replayRows(
  client: { apiCall: sinon.SinonStub },
  tsFor: (opened: number) => string = (n) => (n === 1 ? STREAM_TS : `300.${String(n)}`),
  /** Calls Slack refused (by index among all calls): nothing of them shows. */
  refused: ReadonlySet<number> = new Set(),
): ReplayedRow[] {
  const deleted = new Set(
    client.apiCall.getCalls()
      .filter((c) => c.args[0] === 'chat.delete')
      .map((c) => (c.args[1] as AnyArgs).ts as string),
  );
  const byTs = new Map<string, Map<string, ReplayedRow>>();
  let opened = 0;
  for (const [index, call] of client.apiCall.getCalls().entries()) {
    if (refused.has(index)) continue;
    const [method, args] = call.args as [string, AnyArgs];
    let ts: string | undefined;
    if (method === 'chat.startStream') {
      opened += 1;
      ts = tsFor(opened);
    } else if (method === 'chat.appendStream' || method === 'chat.stopStream') {
      ts = args.ts as string;
    }
    if (!ts) continue;
    const rows = byTs.get(ts) ?? new Map<string, ReplayedRow>();
    byTs.set(ts, rows);
    for (const chunk of (args.chunks ?? []) as AnyArgs[]) {
      if (chunk.type !== 'task_update') continue;
      const row = rows.get(chunk.id) ?? {
        id: chunk.id, title: '', status: '', details: '', output: '', sources: [] as AnyArgs[], detailSends: 0,
      };
      row.title = chunk.title ?? row.title;
      row.status = chunk.status ?? row.status;
      if (chunk.details !== undefined || chunk.output !== undefined || chunk.sources !== undefined) {
        row.detailSends += 1;
      }
      row.details += String(chunk.details ?? '');
      row.output += String(chunk.output ?? '');
      row.sources.push(...((chunk.sources ?? []) as AnyArgs[]));
      rows.set(chunk.id, row);
    }
  }
  return [...byTs.entries()].filter(([ts]) => !deleted.has(ts)).flatMap(([, rows]) => [...rows.values()]);
}

/** The reply as text, as Slack would draw it: the plan, its rows, the answer, the Sources. */
export function renderReplay(client: ReturnType<typeof slackClient>): string {
  const lines: string[] = ['▾ Thinking'];
  for (const row of replayRows(client)) {
    const mark = row.status === 'complete' ? '●' : row.status === 'error' ? '✗' : '◐';
    const more = row.output || row.details || row.sources.length > 0;
    lines.push(`  ${mark} ${row.title}${more ? '  ⌄' : ''}`);
    if (row.details) lines.push(`      ${row.details}`);
    for (const line of row.output.split('\n').filter(Boolean)) lines.push(`      ${line}`);
    for (const source of row.sources) lines.push(`      ↗ ${String(source.text)} <${String(source.url)}>`);
  }
  const messages = apiCalls(client, 'chat.startStream').length;
  if (messages > 1) lines.push(`  [the reply went on in ${String(messages)} messages]`);
  lines.push('', visibleText(client));
  for (const block of replyBlocks(client)) {
    if (block.type === 'section') {
      lines.push('', ...(block.expand === false ? ['[folds behind "Show more" when long]'] : []), String(block.text?.text ?? '').replace(/\*/g, ''));
    }
    // Small grey text in Slack (the Sources list).
    if (block.type === 'context') {
      for (const element of (block.elements ?? []) as AnyArgs[]) {
        lines.push('', ...String(element.text ?? '').replace(/\*/g, '').split('\n').map((line) => `  ${line}`));
      }
    }
    if (block.type === 'table') {
      const cell = (c: AnyArgs): string =>
        c.type === 'raw_text'
          ? String(c.text)
          : ((c.elements ?? []) as AnyArgs[]).flatMap((e) => (e.elements ?? [e]) as AnyArgs[]).map((e) => String(e.text ?? e.url ?? '')).join('');
      const rows = (block.rows ?? []) as AnyArgs[][];
      lines.push('', `[table: ${rows[0]?.map(cell).join(' | ') ?? ''}; ${String(rows.length - 1)} rows; first: ${rows[1]?.map(cell).join(' | ') ?? ''}]`);
    }
    if (block.type === 'rich_text') {
      const text = ((block.elements?.[0]?.elements ?? []) as AnyArgs[])
        .map((e) => (e.type === 'link' ? `${String(e.text)} <${String(e.url)}>` : String(e.text)))
        .join('');
      lines.push(...text.split('\n').filter(Boolean));
    }
  }
  return `${lines.join('\n')}\n`;
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
    .to.include('*Sources*')
    .and.include('<https://x.test/q3|Q3 report>  [1]');

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

    it('reads like the dashboard in a realistic run: one detail send per row, no counts, clickable [n]', async () => {
      const client = await ask(asanaResearchFrames());

      const rendered = renderReplay(client);
      if (process.env.SLACK_REPLAY_OUT) writeFileSync(process.env.SLACK_REPLAY_OUT, `${rendered}\n`);
      const rows = replayRows(client);
      // No counts in titles, details or lines (a call's own outcome, after "→", is the tool's words), and no text shown twice.
      expect(rendered.replace(/ → .*/g, '')).to.not.match(/\b\d+ (calls?|tools|searches|running|failed)\b/);
      expect(rendered).to.not.match(/Ran \d|Explored \d|\(continued\)|Arguments:|Result:/);
      expect(rows.every((row) => row.detailSends <= 1)).to.equal(true);
      // Narration reads as a sentence; every tool row has its lines.
      const notes = rows.filter((row) => row.id.startsWith('note-'));
      const tools = rows.filter((row) => row.id.startsWith('tools-'));
      expect(notes.every((row) => row.details === '' && row.output === '' && row.status === 'complete')).to.equal(true);
      expect(tools.every((row) => row.output !== '')).to.equal(true);
      expect(tools.map((row) => [row.title, row.details, row.status])).to.deep.equal([
        ['Searched the knowledge base', 'Knowledgegraph', 'complete'],
        ['Searched the knowledge base', 'Knowledgegraph', 'complete'],
        ['Get record download links', 'Artifacts', 'error'],
        ['Loaded tools', '', 'complete'],
        ['Create ticket', 'Tracker', 'error'],
      ]);
      // Apps and actions in plain words; a call blocked for approval says where to answer.
      expect(tools[3]!.output).to.equal('✓ Tracker → Loaded 3 tools');
      expect(tools[4]!.output).to.equal('✗ "Asana Q3 follow-up password=[redacted]" → Needs approval in the PipesHub app');
      expect(rendered).to.not.match(/hunter2|mcp_tracker|create_ticket|\bMCP\b/);
      expect(tools[1]!.output.split('\n')).to.deep.equal([
        '✓ "Asana Q3 fiscal year 2026 third quarter revenue earnings results" → 4 results',
        '✗ "Asana Q3 fiscal 2025 third quarter revenue earnings results" → The search took too long',
      ]);
      expect(tools[2]!.output).to.equal('✗ record 8dc0d3d5 → No download link for records from an external connector');
      // Citations are clickable [n] to absolute links, numbered like the Sources list.
      const text = visibleText(client);
      expect(text).to.include('platform [1](https://trust.asana.com/soc3).');
      expect(text).to.include('Q3 FY2026 [2](https://investors.asana.com/q3)');
      expect(text).to.not.match(/\]\(\/record\//);
      expect(rendered).to.include('• <https://trust.asana.com/soc3|Asana SOC 3 report>  [1] [3]');
      expect(rendered).to.include('• <https://investors.asana.com/q3|Asana Q3 FY2026 results>  [2]');
    });

    it('shows reasoning that came after the last step as a row, though no text followed it', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'web_search', displayName: 'Searched the web' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed', resultSummary: '4 results' },
        { type: 'REASONING_MESSAGE_CONTENT', runId: 'r1', delta: 'That answers it.' },
        { type: 'REASONING_END', runId: 'r1' },
        finished('Done.'),
      ]);

      expect(replayRows(client).map((r) => [r.title, r.output])).to.deep.equal([
        ['Searched the web', '✓ 4 results'],
        ['Reasoning', 'That answers it.'],
      ]);
    });

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
        'Revenue grew [1](https://x.test/q3). The Asana Trust Center [2](https://asana.com/trust) lists the report [1](https://x.test/q3).',
      );
      const [stop] = apiCalls(client, 'chat.stopStream');
      const listed = JSON.stringify(stop.blocks);
      expect(listed)
        .to.include('<https://x.test/q3|Q3 report>  [1]')
        .and.include('<https://asana.com/trust|Trust at Asana>  [2]');
      const everything = JSON.stringify(
        client.apiCall.getCalls().map((c) => c.args),
      );
      expect(everything).to.not.match(/ref\d+\.xyz|\(ref\d+\)/);
    });

    it('starts streaming a multi-paragraph answer after its first paragraph', async () => {
      const answer = [
        'Asana grew revenue in the third quarter.',
        '',
        'Operating margin improved for the fourth quarter in a row.',
        '',
        'Cash flow was positive.',
      ].join('\n');
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 3),
        finished(answer),
      ]);

      const pieces = [...apiCalls(client, 'chat.startStream'), ...apiCalls(client, 'chat.appendStream')]
        .flatMap((c) => (c.chunks as AnyArgs[]).filter((ch) => ch.type === 'markdown_text').map((ch) => ch.text as string));
      expect(pieces.length).to.be.greaterThan(1);
      expect(pieces[0]!.startsWith('Asana grew revenue in the third quarter.\n\n')).to.equal(true);
      expect(pieces[0]!.length).to.be.lessThan(answer.length);
      expect(visibleText(client)).to.equal(answer);
    });

    it('streams an answer that starts with a heading right away', async () => {
      const answer = '## Asana overview\nAsana is a work-management platform used by many teams.';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 3),
        finished(answer),
      ]);

      const pieces = [...apiCalls(client, 'chat.startStream'), ...apiCalls(client, 'chat.appendStream')]
        .flatMap((c) => (c.chunks as AnyArgs[]).filter((ch) => ch.type === 'markdown_text').map((ch) => ch.text as string));
      expect(pieces.length).to.be.greaterThan(1);
      expect(pieces[0]!.startsWith('## ')).to.equal(true);
      expect(visibleText(client)).to.equal(answer);
    });

    it('keeps a one-paragraph opening, even ending in a blank line, as a row when a tool follows', async () => {
      const client = await ask(answerFrames('completed', 'I will look up the third-quarter filing first.\n\n'));

      const rows = [...finalSteps(client).values()];
      expect(rows[0]).to.deep.include({ title: 'I will look up the third-quarter filing first.', status: 'complete' });
      expect(visibleText(client)).to.equal(SHOWN_ANSWER);
    });

    it('does not let a discarded model call shift the [n] numbering', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1', messageId: 'm1' },
        ...tokens('An old figure [source](ref9).\n\nMore on it below, from the filing.'),
        { type: 'TEXT_MESSAGE_END', runId: 'r1', messageId: 'm1' },
        { type: 'CUSTOM', runId: 'r1', name: 'message_discarded', value: { messageId: 'm1' } },
        { type: 'TEXT_MESSAGE_START', runId: 'r1', messageId: 'm2' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);

      expect(visibleText(client)).to.equal(SHOWN_ANSWER);
    });

    it('never gives two different citations the same [n] in one reply', async () => {
      const opening = 'The older filing [source](ref9) covers this. '.repeat(40);
      const older = [{ citationData: { chunkIndex: 1, metadata: { recordId: 'rec-9', recordName: 'Older filing', webUrl: 'https://x.test/older' } } }];
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(opening.slice(0, 80), 40),
        { type: 'STATE_DELTA', runId: 'r1', delta: [{ op: 'replace', path: '/citations', value: older }] },
        // An empty snapshot (the backend clears its live citations before a tool call) keeps a link already known.
        { type: 'STATE_DELTA', runId: 'r1', delta: [{ op: 'replace', path: '/citations', value: [] }] },
        ...tokens(opening.slice(80), 40),
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'search' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);

      const text = visibleText(client);
      expect(text).to.include('The older filing [1](https://x.test/older) covers this.');
      expect(text.endsWith('Revenue grew 12% [2](https://x.test/q3).')).to.equal(true);
      const sources = JSON.stringify(apiCalls(client, 'chat.stopStream').slice(-1)[0]?.blocks);
      expect(sources).to.include('https://x.test/older').and.include('https://x.test/q3');
    });

    it('skips citation snapshots of a discarded call that come after the discard', async () => {
      const cite = (url: string) => [{ citationData: { chunkIndex: 1, metadata: { recordId: 'r', recordName: 'Doc', webUrl: url } } }];
      const delta = (value: unknown[], discarded = false) => ({
        type: 'STATE_DELTA', runId: 'r1',
        delta: [{ op: 'replace', path: '/citations', value }, ...(discarded ? [{ op: 'replace', path: '/discarded', value: true }] : [])],
      });
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens('Wrong idea. '.repeat(10)),
        { type: 'CUSTOM', runId: 'r1', name: 'message_discarded', value: { messageId: 'm1' } },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens('Right [source](ref1).'),
        delta(cite('https://x.test/wrong')),
        delta([], true),
        delta(cite('https://x.test/right')),
        finished('Right [source](ref1).', cite('https://x.test/right')),
      ]);

      const text = visibleText(client);
      expect(text).to.include('Right [1](https://x.test/right).');
      expect(JSON.stringify(apiCalls(client, 'chat.appendStream'))).to.not.include('x.test/wrong');
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

    it('puts each run of the same tool in one row, titled by what it did, and a different tool in its own row', async () => {
      const call = (id: string, name: string, args: string, status = 'completed', summary?: string, label?: string) => [
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: id, toolCallName: name, ...(label ? { displayName: label } : {}) },
        { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: id, delta: '{}', argsSummary: args },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: id, status, ...(summary ? { resultSummary: summary } : {}) },
      ];
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        ...call('c1', 'knowledgegraph__search', 'Searched for "Asana SOC 3"', 'completed', '3 results\nDoc A', 'Searched the knowledge base'),
        ...call('c2', 'knowledgegraph__search', 'Searched for "Asana revenue"', 'failed', 'Failed: no results', 'Searched the knowledge base'),
        ...call('c3', 'fetch_url', 'Reading https://asana.com/trust', 'completed', 'Read “Trust” (asana.com)', 'Read a web page'),
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);

      const rows = replayRows(client);
      expect(rows.map((r) => [r.title, r.status, r.details])).to.deep.equal([
        ['Searched the knowledge base', 'complete', 'Knowledgegraph'],
        ['Read a web page', 'complete', ''],
      ]);
      expect(rows[0]!.output).to.equal('✓ "Asana SOC 3" → 3 results\n✗ "Asana revenue" → No results');
      expect(rows[1]!.output).to.equal('✓ Reading https://asana.com/trust → Read “Trust” (asana.com)');
      expect(rows.every((r) => r.detailSends === 1)).to.equal(true);
    });

    it('shows six calls of a row and sums up the rest', async () => {
      const frames = Array.from({ length: 8 }, (_, i) => [
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: `c${String(i)}`, toolCallName: 'web_search', displayName: 'Searched the web' },
        { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: `c${String(i)}`, delta: '{}', argsSummary: `Searched for "query ${String(i)}"` },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: `c${String(i)}`, status: 'completed', resultSummary: `${String(i)} results` },
      ]).flat();
      const client = await ask([{ type: 'RUN_STARTED', runId: 'r1' }, ...frames, finished('')]);

      const [row] = replayRows(client);
      expect(row!.title).to.equal('Searched the web');
      expect(row!.output.split('\n')).to.deep.equal([
        ...Array.from({ length: 6 }, (_, i) => `✓ "query ${String(i)}" → ${String(i)} results`),
        '+2 more',
      ]);
      expect(row!.detailSends).to.equal(1);
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
        ['Searched', 'complete'],
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

    describe('the Jira table answer from a live run', () => {
      const intro = JIRA_TABLE_ANSWER.slice(0, JIRA_TABLE_ANSWER.indexOf('|'));
      const expectTable = (client: ReturnType<typeof slackClient>) => {
        // The streamed text is the intro only: no part of the header row goes out as text.
        expect(streamedText(client).trim()).to.equal(intro.trim());
        expectNoStreamedTableRow(client);
        const blocks = replyBlocks(client);
        const table = blocks.findIndex((b) => b.type === 'table');
        expect(table, 'the table is a table block').to.be.at.least(0);
        expect(blocks[table].rows).to.have.length(11);
        const closing = blocks.findIndex((b) => JSON.stringify(b).includes('The most immediate follow-ups'));
        expect(closing, 'the closing sentence follows the table').to.be.greaterThan(table);
      };

      // 40 is the chunking of the live reproduction.
      for (const size of [1, 3, 7, 16, 40, 64]) {
        it(`streams the intro and puts the table as blocks, in chunks of ${String(size)}`, async () => {
          expectTable(await ask([
            { type: 'RUN_STARTED', runId: 'r1' },
            { type: 'TEXT_MESSAGE_START', runId: 'r1' },
            ...tokens(JIRA_TABLE_ANSWER, size),
            finished(JIRA_TABLE_ANSWER),
          ]));
        });
      }

      it('when one chunk carries the intro and the first rows', async () => {
        const cut = JIRA_TABLE_ANSWER.indexOf('| DEMO-102');
        expectTable(await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: JIRA_TABLE_ANSWER.slice(0, cut) },
          ...tokens(JIRA_TABLE_ANSWER.slice(cut), 9),
          finished(JIRA_TABLE_ANSWER),
        ]));
      });

      it('when the intro streams before a pause and the header row arrives alone', async () => {
        expectTable(await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: intro },
          { type: 'TEXT_MESSAGE_CONTENT', runId: 'r1', delta: '| Ticket | Summary | Status | Priority | Last updated |\n' },
          ...tokens(JIRA_TABLE_ANSWER.slice(JIRA_TABLE_ANSWER.indexOf('|---')), 5),
          finished(JIRA_TABLE_ANSWER),
        ]));
      });

      it('after a tool call, when the answer comes only with the end of the run', async () => {
        expectTable(await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'jira__search_issues', displayName: 'Searched Jira' },
          { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed', resultSummary: '10 results' },
          finished(JIRA_TABLE_ANSWER),
        ]));
      });

      it('after a tool call, streamed', async () => {
        expectTable(await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          ...tokens('Let me look up your tickets.'),
          { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'jira__search_issues', displayName: 'Searched Jira' },
          { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed', resultSummary: '10 results' },
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          ...tokens(JIRA_TABLE_ANSWER, 4),
          finished(JIRA_TABLE_ANSWER),
        ]));
      });
    });

    it('keeps a table written before a tool call, and still streams the final answer', async () => {
      const preamble = `${'Here is what I have so far, before I check one more source. '.repeat(30)}\n\n| A | B |\n|---|---|\n| 1 | 2 |\n`;
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(preamble, 40),
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'web_search', displayName: 'Searched the web' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed', resultSummary: '4 results' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(ANSWER),
        finished(NORMALIZED_ANSWER, CITATIONS),
      ]);
      expectNoStreamedTableRow(client);
      expect(streamedText(client), 'the final answer streams').to.include('Revenue grew 12% [1](https://x.test/q3).');
      expect(replyBlocks(client).some((b) => b.type === 'table'), 'the earlier table is kept').to.equal(true);
    });

    it('never streams a header row the run ended on', async () => {
      const answer = 'The fields are:\n\n| Name | Type |\n';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 5),
        finished(answer),
      ]);
      expectNoStreamedTableRow(client);
      expect(streamedText(client)).to.include('The fields are:');
      expect(JSON.stringify(replyBlocks(client))).to.include('Name');
    });

    it('puts two tables of one answer both as blocks', async () => {
      const answer = 'First:\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\nThen:\n\n| C | D |\n|---|---|\n| 3 | 4 |\n\nDone.';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 3),
        finished(answer),
      ]);
      expectNoStreamedTableRow(client);
      expect(replyBlocks(client).filter((b) => b.type === 'table')).to.have.length(2);
      expect(JSON.stringify(replyBlocks(client))).to.include('Done.');
    });

    it('keeps a table inside a code fence as code', async () => {
      const answer = 'Example:\n\n```\n| A | B |\n|---|---|\n| 1 | 2 |\n```\n\nThat is the format.';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 3),
        finished(answer),
      ]);
      expect(visibleText(client)).to.equal(answer);
      expect(replyBlocks(client).some((b) => b.type === 'table')).to.equal(false);
    });

    it('puts a table after citations as blocks, its citations numbered like the Sources list', async () => {
      const answer = 'Revenue grew [source](ref1).\n\n| Quarter | Revenue |\n|---|---|\n| Q3 | 12% |\n\nSee above.';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 4),
        finished(answer, CITATIONS),
      ]);
      expectNoStreamedTableRow(client);
      expect(streamedText(client)).to.include('Revenue grew [1](https://x.test/q3).');
      expect(replyBlocks(client).some((b) => b.type === 'table')).to.equal(true);
      expect(JSON.stringify(replyBlocks(client))).to.include('https://x.test/q3');
    });

    it('puts a table that comes after the answer rolled over into a new message as blocks', async () => {
      const long = `${'Revenue grew in every quarter of the year. '.repeat(320)}\n\n`;
      const answer = `${long}| Quarter | Revenue |\n|---|---|\n| Q3 | 12% |\n\nThat is all.`;
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(answer, 400),
        finished(answer),
      ]);
      expect(apiCalls(client, 'chat.startStream').length).to.be.greaterThan(1);
      expectNoStreamedTableRow(client);
      expect(replyBlocks(client).some((b) => b.type === 'table')).to.equal(true);
      expect(JSON.stringify(replyBlocks(client))).to.include('That is all.');
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

    it('shows the citations of an answer that came only with the end of the run as [n], keeping other numbered links', async () => {
      const answer = 'Revenue grew 12% [1](/record/rec-1). See item [7](https://example.com/list).';
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'search' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed' },
        finished(answer, CITATIONS),
      ]);

      expect(visibleText(client)).to.equal('Revenue grew 12% [1](https://x.test/q3). See item [7](https://example.com/list).');
      const [stop] = apiCalls(client, 'chat.stopStream');
      expect(JSON.stringify(stop.blocks)).to.include('<https://x.test/q3|Q3 report>  [1]');
    });

    it('numbers a final-only answer\'s citations like the markers shown before it, matching the Sources list', async () => {
      const doc = (n: number, name: string, url: string) => ({ citationData: { chunkIndex: n, metadata: { recordId: name, recordName: name, webUrl: url } } });
      const opening = 'B explains it [source](ref1). '.repeat(60);
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens(opening.slice(0, 60), 40),
        { type: 'STATE_DELTA', runId: 'r1', delta: [{ op: 'replace', path: '/citations', value: [doc(1, 'B', 'https://x.test/b')] }] },
        ...tokens(opening.slice(60), 40),
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'search' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed' },
        // The answer comes only with the run's end, numbered by the backend: A first, then B.
        finished('A says so [1](/record/A). B agrees [2](/record/B). See item [7](https://example.com/list).', [
          doc(1, 'A', 'https://x.test/a'),
          doc(2, 'B', 'https://x.test/b'),
        ]),
      ]);

      const text = visibleText(client);
      expect(text).to.include('B explains it [1](https://x.test/b).');
      expect(text).to.include('A says so [2](https://x.test/a). B agrees [1](https://x.test/b). See item [7](https://example.com/list).');
      const sources = String(renderReplay(client).split('Sources')[1]).trim();
      expect(sources).to.equal('• <https://x.test/b|B>  [1]\n• <https://x.test/a|A>  [2]');
    });

    describe('golden transcripts: what users see, for whole runs', () => {
      for (const run of GOLDEN_RUNS) {
        it(run.name, async () => {
          if (run.clock) useRunClock();
          try {
            const rendered = renderReplay(await ask(run.frames()));
            const file = join(GOLDEN_DIR, `${run.name}.txt`);
            if (process.env.UPDATE_SLACK_GOLDEN) writeFileSync(file, rendered);
            expect(rendered).to.equal(readFileSync(file, 'utf8'));
            // Slack shows a bracketed link text, `[[1]](url)`, as raw markdown.
            expect(rendered).to.not.match(/\[\[\d+\]\]\(/);
          } finally {
            restoreRunClock();
          }
        });
      }
    });

    describe('a quiet gap between steps', () => {
      beforeEach(() => useRunClock());
      afterEach(() => restoreRunClock());
      const search = (id: string, summary = '4 results') => [
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: id, toolCallName: 'web_search', displayName: 'Searched the web' },
        { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: id, delta: '{}', argsSummary: 'Searched for "Q3"' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: id, status: 'completed', resultSummary: summary },
      ];
      const expectSettled = (client: ReturnType<typeof slackClient>) => {
        const rows = replayRows(client);
        expect(rows.every((r) => r.status !== 'in_progress'), 'nothing spins after the reply').to.equal(true);
        expect(rows.every((r) => r.title !== 'Thinking…' && r.detailSends <= 1)).to.equal(true);
        expect(rows.map((r) => r.details + r.output).join(''), 'ticker titles never reach details or output').to.not.match(/\d+s\b/);
        return rows;
      };
      const opensThinking = (client: ReturnType<typeof slackClient>) =>
        apiCalls(client, 'chat.appendStream').some((a) => (a.chunks as AnyArgs[]).some((c) => c.title === 'Thinking…'));

      it('shows "Thinking…" in the gap, then the narration in its place', async () => {
        const client = await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          ...search('c1'),
          { type: 'WAIT', ms: 3000 },
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          ...tokens('Now I will read the filing.'),
          ...search('c2'),
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          ...tokens(ANSWER),
          finished(NORMALIZED_ANSWER, CITATIONS),
        ]);
        expect(opensThinking(client)).to.equal(true);
        expect(expectSettled(client).map((r) => r.title)).to.deep.equal([
          'Searched the web', 'Now I will read the filing.', 'Searched the web',
        ]);
      });

      it('turns "Thinking…" into the next step\'s row', async () => {
        const client = await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          ...search('c1'),
          { type: 'WAIT', ms: 3000 },
          ...search('c2', '2 results'),
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          ...tokens(ANSWER),
          finished(NORMALIZED_ANSWER, CITATIONS),
        ]);
        expect(opensThinking(client)).to.equal(true);
        expect(expectSettled(client).map((r) => [r.title, r.output])).to.deep.equal([
          ['Searched the web', '✓ "Q3" → 4 results'],
          ['Searched the web', '✓ "Q3" → 2 results'],
        ]);
      });

      it('turns "Thinking…" into "Writing the answer", which ends with the reply', async () => {
        const client = await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          ...search('c1'),
          { type: 'WAIT', ms: 3000 },
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          ...tokens(`${'Revenue grew in every quarter. '.repeat(10)}\n\nThat is all.`),
          finished('Revenue grew.'),
        ]);
        expect(expectSettled(client).map((r) => [r.title, r.status])).to.deep.equal([
          ['Searched the web', 'complete'],
          ['Writing the answer', 'complete'],
        ]);
        // It said so while the answer streamed, not only at the end.
        const live = apiCalls(client, 'chat.appendStream').flatMap((c) => c.chunks as AnyArgs[]);
        expect(live).to.deep.include({ type: 'task_update', id: 'next-2', title: 'Writing the answer', status: 'in_progress' });
      });

      it('ends "Thinking…" as stopped, not "Writing the answer", when the run fails before anything came', async () => {
        const client = await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          ...search('c1'),
          { type: 'WAIT', ms: 3000 },
          { type: 'RUN_ERROR', runId: 'r1', message: 'The model is not available.' },
        ]);
        const rows = replayRows(client);
        expect(rows.map((r) => [r.title, r.status])).to.deep.equal([
          ['Searched the web', 'complete'],
          ['Stopped', 'error'],
        ]);
      });

      it('shows how long a long step has taken, and its final title once it ends', async () => {
        const client = await ask([
          { type: 'RUN_STARTED', runId: 'r1' },
          { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'web_search', displayName: 'Searched the web' },
          { type: 'WAIT', ms: 21_000 },
          { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'completed', resultSummary: '4 results' },
          { type: 'TEXT_MESSAGE_START', runId: 'r1' },
          ...tokens(ANSWER),
          finished(NORMALIZED_ANSWER, CITATIONS),
        ]);
        const titles = apiCalls(client, 'chat.appendStream').flatMap((a) => (a.chunks as AnyArgs[]).map((c) => c.title as string | undefined));
        expect(titles).to.include.members(['Searched the web · 10s', 'Searched the web · 15s', 'Searched the web · 20s']);
        expect(expectSettled(client)[0]).to.deep.include({ title: 'Searched the web', output: '✓ 4 results' });
      });
    });

    it('lists unnumbered citations the same way: one line per document, no numbers', async () => {
      const unnumbered = [1, 2].map(() => ({ citationData: { metadata: { recordId: 'r1', recordName: 'Handbook', webUrl: 'https://x.test/h' } } }));
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens('See the handbook.'),
        finished('See the handbook.', unnumbered),
      ]);
      const [stop] = apiCalls(client, 'chat.stopStream');
      expect(stop.blocks).to.deep.equal([
        { type: 'section', expand: false, text: { type: 'mrkdwn', text: '*Sources*\n• <https://x.test/h|Handbook>' } },
      ]);
    });

    it('posts no Sources when nothing was cited', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TEXT_MESSAGE_START', runId: 'r1' },
        ...tokens('Hello.'),
        finished('Hello.'),
      ]);
      expect(JSON.stringify(replyBlocks(client))).to.not.include('Sources');
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

    it('does not show a tool waiting for approval as failed, and sends its line once', async () => {
      const client = await ask([
        { type: 'RUN_STARTED', runId: 'r1' },
        { type: 'TOOL_CALL_START', runId: 'r1', toolCallId: 'c1', toolCallName: 'jira__create_issue', displayName: 'Created a Jira issue' },
        { type: 'TOOL_CALL_ARGS', runId: 'r1', toolCallId: 'c1', delta: '{}', argsSummary: 'Creating "OPS: rotate keys"' },
        { type: 'TOOL_CALL_RESULT', runId: 'r1', toolCallId: 'c1', status: 'awaiting_approval' },
        finished(''),
      ]);

      const [row] = replayRows(client);
      // At the end of the reply no spinner is left: the row says what it waits for.
      expect(row).to.deep.include({
        title: 'Waiting for your approval: Created a Jira issue',
        status: 'complete',
        details: 'Jira',
        output: '• "OPS: rotate keys" → Waiting for your approval',
        detailSends: 1,
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

import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import {
  RunProjector,
  WAITING_PROVIDER,
} from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';

/** EU-5 and EU-10 (ERR-UX): a failed call reaches the person by its code; a provider backoff by its wait. */

const RUN = 'run-errux';
const DETAIL = 'ConnectError: All connection attempts failed (bolt://10.0.0.7:7687)';

let seq = 0;
const event = (
  type: string,
  data: Record<string, unknown>,
  writer: 'worker' | 'node' = 'worker',
): JournalEvent =>
  ({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: 'ses-1',
    seq: (seq += 1),
    runId: RUN,
    attemptId: `${RUN}.a1`,
    fence: 1,
    type,
    schemaVersion: 1,
    critical: !type.startsWith('x.'),
    ts: `2026-10-10T12:00:${String(seq).padStart(2, '0')}.000Z`,
    writer,
    visibility: 'owner',
    data,
  }) as JournalEvent;

const result = (doc: Record<string, unknown>) =>
  inlineContentRef(canonicalJsonBytes(doc));

const journal = (): JournalEvent[] => {
  seq = 0;
  return [
    event('run.claimed', { fence: 1 }),
    event('turn.started', { turn: 1 }),
    event('model.response', {
      messageRef: result({
        text: '',
        toolCalls: [
          { id: 'c1', name: 'graph_lookup', args: {} },
          { id: 'c2', name: 'slack__send_message', args: {} },
        ],
      }),
      toolCalls: [
        { callId: 'c1', modelName: 'graph_lookup', argsDigest: `sha256:${'c'.repeat(64)}` },
        { callId: 'c2', modelName: 'slack__send_message', argsDigest: `sha256:${'d'.repeat(64)}` },
      ],
      usage: { inputTokens: 0, outputTokens: 0 },
      provider: 'p',
      transport: 't',
    }),
    event('tool.intent', { invocationId: 't1.0', toolId: 'graph', argsDigest: 'x', effect: 'read' }),
    event('tool.outcome', {
      invocationId: 't1.0',
      status: 'error',
      resultRef: result({ content: DETAIL, isError: true, errorCode: 'unavailable' }),
    }),
    event('x.pipeshub.tool.result_presented', { callId: 'c1', status: 'failed', content: '' }),
    event('tool.intent', { invocationId: 't1.1', toolId: 'slack', argsDigest: 'y', effect: 'write' }),
    event('tool.outcome', {
      invocationId: 't1.1',
      status: 'error',
      resultRef: result({ content: 'channel_not_found', isError: true, errorCode: 'not_found' }),
    }),
  ];
};

const context = {
  runId: RUN,
  sessionId: 'ses-1',
  conversationId: 'conv-1',
  goal: 'status',
  audience: 'owner' as const,
};

const resultFrames = (frames: unknown[]): Record<string, unknown>[] =>
  frames
    .map((f) => (f as { frame?: Record<string, unknown> }).frame ?? (f as Record<string, unknown>))
    .filter((f) => f['type'] === 'TOOL_CALL_RESULT');

describe('ERR-UX EU-5: a failed tool call is shown by its code, never its detail', () => {
  it('the stream carries the code, nothingSent for the write only, and no detail', () => {
    const frames = new RunProjector(context).project(journal());
    const results = resultFrames(frames);
    expect(results.map((f) => [f['toolCallId'], f['status'], f['content'], f['errorCode'], f['nothingSent']])).to.deep.equal([
      ['c1', 'failed', '', 'unavailable', undefined],
      ['c2', 'failed', '', 'not_found', true],
    ]);
    expect(JSON.stringify(frames)).to.not.include('ConnectError');
    expect(JSON.stringify(frames)).to.not.include('channel_not_found');
  });

  it('the saved parts carry the same, without a preview of the detail', () => {
    const parts = buildParts(foldTimeline(journal(), RUN)).filter((p) => p['type'] === 'tool_call');
    expect(parts.map((p) => [p['errorCode'], p['nothingSent'], p['resultPreview']])).to.deep.equal([
      ['unavailable', undefined, ''],
      ['not_found', true, ''],
    ]);
  });

  it("the owner's activity panel names the code and the redacted detail", () => {
    const tools = foldActivity(journal(), { runId: RUN, runStatus: 'running' }).tools;
    expect(tools.map((t) => t.failure)).to.deep.equal([
      { code: 'unavailable', detail: DETAIL },
      { code: 'not_found', detail: 'channel_not_found' },
    ]);
  });

  it("an older worker's outcome without a code keeps today's preview", () => {
    const events = journal().map((e) =>
      e.type === 'tool.outcome' && e.data?.['invocationId'] === 't1.0'
        ? { ...e, data: { ...e.data, resultRef: result({ content: 'old text', isError: true }) } }
        : e,
    );
    const [first] = resultFrames(new RunProjector(context).project(events as JournalEvent[]));
    expect(first).to.include({ status: 'failed' });
    expect(first!['errorCode']).to.equal(undefined);
  });
});

describe('ERR-UX EU-10: a provider backoff is on the stream', () => {
  const snapshots = (frames: unknown[]): Record<string, unknown>[] =>
    frames
      .map((f) => (f as { frame?: Record<string, unknown> }).frame ?? (f as Record<string, unknown>))
      .filter((f) => f['type'] === 'STATE_SNAPSHOT')
      .map((f) => f['snapshot'] as Record<string, unknown>);

  it('llm_backoff shows waiting_provider with the retry time; the re-claim returns to calling_llm', () => {
    seq = 0;
    const events = [
      event('run.claimed', { fence: 1 }),
      event('turn.started', { turn: 1 }),
      event('run.yielded', { notBefore: '2026-10-10T12:00:25.000Z', reason: 'llm_backoff' }, 'node'),
      event('run.claimed', { fence: 2 }),
    ];
    const statuses = snapshots(new RunProjector(context).project(events)).map((s) => [s['status'], s['retry_at']]);
    expect(statuses).to.deep.equal([
      ['starting', undefined],
      ['calling_llm', undefined],
      [WAITING_PROVIDER, '2026-10-10T12:00:25.000Z'],
      ['calling_llm', undefined],
    ]);
  });

  it('another yield reason adds no status', () => {
    seq = 0;
    const events = [
      event('run.claimed', { fence: 1 }),
      event('turn.started', { turn: 1 }),
      event('run.yielded', { notBefore: '2026-10-10T12:00:25.000Z', reason: 'drain' }, 'node'),
    ];
    expect(snapshots(new RunProjector(context).project(events)).map((s) => s['status'])).to.deep.equal([
      'starting',
      'calling_llm',
    ]);
  });
});

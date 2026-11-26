import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { foldActivity } from '../../../../src/modules/agent-harness/projection/activity';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';

/**
 * WP56-FRAME-5: a code_task hand-off's result as a run journaled it. Its outcome document is the
 * child's result as JSON (ids, the fork's workspace id); the worker now presents it in plain words, and that is
 * what the stream, the saved parts and the activity panel show.
 */

const RUN = 'run-wp56';
const CHILD = '1b543f14-99f4-02de-6eca-3e489dbbb693';
const FOLDER = 'Unfinished work/2026-11-01 17.52.38 UTC';
const SUMMARY = `The coding engine stopped with an error before it finished. Its saved work is in the folder “${FOLDER}”.`;

let seq = 0;
const event = (type: string, data: Record<string, unknown>): JournalEvent =>
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
    ts: `2026-11-01T17:52:${String(seq).padStart(2, '0')}.000Z`,
    writer: 'worker',
    visibility: 'owner',
    data,
  }) as JournalEvent;

const ref = (doc: Record<string, unknown>) => inlineContentRef(canonicalJsonBytes(doc));

const journal = (presented: boolean): JournalEvent[] => {
  seq = 0;
  return [
    event('run.claimed', { fence: 1 }),
    event('turn.started', { turn: 1 }),
    event('model.response', {
      messageRef: ref({ text: '', toolCalls: [{ id: 't1.0', name: 'code_task', args: { goal: 'Research' } }] }),
      toolCalls: [{ callId: 't1.0', modelName: 'code_task', argsDigest: `sha256:${'c'.repeat(64)}` }],
      usage: { inputTokens: 0, outputTokens: 0 },
      provider: 'p',
      transport: 't',
    }),
    event('tool.intent', { invocationId: 't1.0', toolId: 'delegate@default/code_task', argsDigest: 'x', effect: 'read' }),
    event('tool.outcome', {
      invocationId: 't1.0',
      status: 'error',
      resultRef: ref({
        content: {
          error: 'commit_rejected',
          output: '',
          success: false,
          workspace: {
            fork: { revision: 25, workspaceId: `ws:6ae77b84165c60cace330ae5:fork:${CHILD}` },
            reason: 'child_failed',
            status: 'not_merged',
            unfinished: { folder: FOLDER, files: ['out/report.md'] },
          },
        },
        isError: true,
      }),
    }),
    ...(presented
      ? [event('x.pipeshub.tool.result_presented', { callId: 't1.0', status: 'failed', content: '', resultSummary: SUMMARY })]
      : []),
  ];
};

const context = { runId: RUN, sessionId: 'ses-1', conversationId: 'conv-1', goal: 'research', audience: 'owner' as const };

const results = (frames: unknown[]): Record<string, unknown>[] =>
  frames
    .map((f) => (f as { frame?: Record<string, unknown> }).frame ?? (f as Record<string, unknown>))
    .filter((f) => f['type'] === 'TOOL_CALL_RESULT');

describe('WP56-FRAME-5: a code_task result reads in plain words, no ids or JSON', () => {
  it('without the worker presenting it, the fallback was the raw document (why the worker presents it)', () => {
    const [frame] = results(new RunProjector(context).project(journal(false)));
    expect(String(frame!['content'])).to.include('{');
  });

  it('the stream carries the plain line', () => {
    const frames = new RunProjector(context).project(journal(true));
    const [frame] = results(frames);
    expect(frame).to.include({ toolCallId: 't1.0', status: 'failed', content: '', resultSummary: SUMMARY });
    for (const leak of [CHILD, 'ws:', 'commit_rejected', 'not_merged']) {
      expect(JSON.stringify(results(frames))).to.not.include(leak);
    }
  });

  it('the saved parts carry the same', () => {
    const [part] = buildParts(foldTimeline(journal(true), RUN)).filter((p) => p['type'] === 'tool_call');
    expect(part!['resultSummary']).to.equal(SUMMARY);
    const preview = part!['resultPreview'] ?? '';
    expect(typeof preview === 'string' ? preview : JSON.stringify(preview)).to.not.include('{');
  });

  it('the activity panel shows no raw document either (it never read the outcome document)', () => {
    const tools = foldActivity(journal(true), { runId: RUN, runStatus: 'completed' }).tools;
    expect(JSON.stringify(tools)).to.not.include(CHILD);
    expect(JSON.stringify(tools)).to.not.include('"workspaceId"');
  });
});

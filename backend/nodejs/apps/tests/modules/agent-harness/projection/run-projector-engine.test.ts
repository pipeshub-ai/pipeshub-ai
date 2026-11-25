/**
 * PR-2..PR-4 (E1): a durable engine run on the AG-UI stream. Its progress lines
 * (`x.pipeshub.engine.progress`) are the `code_task` card's `engine_progress`
 * frames, the same the request stream carries (U1); an engine approval is a
 * card naming its call; the engine's answer is the turn's text; its final
 * artifacts are cards as they are presented, drafts stay rows of the task card.
 */
import { expect } from 'chai';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import {
  AguiFrame,
  Audience,
} from '../../../../src/modules/agent-harness/projection/frames';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';

const RUN = 'run-engine';
const SESSION = 'ses-engine';
const IX = 'ix.approve.k0123456789abcdef0123456789abcdef01234567';

const inline = (value: unknown) =>
  inlineContentRef(
    Buffer.from(typeof value === 'string' ? value : JSON.stringify(value)),
  );

let seq = 0;
const event = (
  type: string,
  data: Record<string, unknown>,
  writer: 'node' | 'worker' = 'worker',
  critical = true,
): JournalEvent =>
  ({
    orgId: 'org-1',
    sessionId: SESSION,
    streamId: SESSION,
    seq: (seq += 1),
    runId: RUN,
    attemptId: `${RUN}.a1`,
    fence: 1,
    type,
    schemaVersion: 1,
    critical,
    ts: '2026-10-03T12:00:00.000Z',
    writer,
    visibility: 'owner',
    data,
  }) as JournalEvent;

const line = (
  kind: string,
  text: string,
  data: Record<string, unknown> = {},
): JournalEvent =>
  event(
    'x.pipeshub.engine.progress',
    {
      tool: 'code_task',
      taskId: RUN,
      kind,
      text,
      data,
      turnId: 'trn_1',
      seq: seq + 1,
    },
    'worker',
    false,
  );

const card = (lifecycle: string, version: number): JournalEvent =>
  event(
    'x.pipeshub.artifact.presented',
    {
      artifactId: 'art-1',
      fileName: 'report.md',
      mimeType: 'text/markdown',
      sizeBytes: 10,
      artifactType: 'file',
      isTemporary: false,
      recordId: 'art-1',
      version,
      visibility: 'VISIBLE',
      card: true,
      lifecycle,
    },
    'worker',
    false,
  );

function journal(): JournalEvent[] {
  seq = 0;
  return [
    event(
      'run.admitted',
      { runKind: 'interactive', principalRef: 'p_user' },
      'node',
    ),
    event(
      'run.claimed',
      {
        attempt: 1,
        fence: 1,
        workerId: 'w-1',
        leaseExpiresAt: '2026-10-03T12:01:00.000Z',
      },
      'node',
    ),
    event('message.completed', {
      role: 'user',
      textRef: inline('Write the Q3 report'),
      isFinal: false,
    }),
    event('turn.started', { turn: 1 }),
    line('started', 'Write the Q3 report'),
    line('command', 'python report.py', { callId: 'c1' }),
    line('output', 'wrote out/report.md', {
      callId: 'c1',
      stream: 'stdout',
      truncated: false,
    }),
    card('draft', 1),
    line('artifact', 'report.md', {
      artifactId: 'art-1',
      version: 1,
      lifecycle: 'draft',
    }),
    line('approval', 'create_issue', {
      interactionId: IX,
      toolName: 'create_issue',
      argsJson: '{"title":"Q3"}',
    }),
    event('interaction.requested', {
      interactionId: IX,
      kind: 'approval',
      bindingDigest: `sha256:${'a'.repeat(64)}`,
    }),
    event(
      'interaction.resolved',
      { interactionId: IX, decision: 'approved', by: 'p_user', channel: 'web' },
      'node',
    ),
    event('model.response', {
      messageRef: inline({
        text: 'The report is in out/report.md.',
        toolCalls: [],
      }),
      toolCalls: [],
      usage: {
        inputTokens: 10,
        outputTokens: 5,
        cacheReadTokens: 0,
        cacheWriteTokens: 0,
      },
      provider: 'engine',
      transport: 'opencode',
    }),
    event('turn.completed', { turn: 1 }),
    card('final', 1),
    line('artifact', 'report.md', {
      artifactId: 'art-1',
      version: 1,
      lifecycle: 'final',
    }),
    event('message.completed', {
      role: 'assistant',
      textRef: inline('The report is in out/report.md.'),
      isFinal: true,
    }),
    line('finished', 'The report is in out/report.md.', { success: true }),
    event(
      'x.pipeshub.answer.finalized',
      {
        outcome: 'answered',
        answer: 'The report is in out/report.md.',
        partTexts: ['The report is in out/report.md.'],
        citations: [],
      },
      'worker',
      false,
    ),
    event('run.completed', { outcome: 'answered' }, 'node'),
  ];
}

const project = (
  events: JournalEvent[],
  audience: Audience = 'owner',
): AguiFrame[] => {
  const projector = new RunProjector({
    runId: RUN,
    sessionId: SESSION,
    conversationId: SESSION,
    goal: 'Write the Q3 report',
    audience,
  });
  projector.begin();
  return projector.project(events).map((f) => f.frame);
};

const customs = (
  frames: AguiFrame[],
  name: string,
): Array<Record<string, unknown>> =>
  frames
    .filter((f) => f.type === 'CUSTOM' && f['name'] === name)
    .map((f) => f['value'] as Record<string, unknown>);

const cards = (frames: AguiFrame[]): Array<Record<string, unknown>> =>
  frames.flatMap((frame) =>
    frame.type === 'STATE_DELTA'
      ? (
          frame['delta'] as Array<{
            path: string;
            value: Record<string, unknown>;
          }>
        )
          .filter((op) => op.path === '/artifacts/-')
          .map((op) => op.value)
      : [],
  );

describe('agent-harness projection: durable engine runs (E1)', () => {
  it('PR-2: each progress line is an engine_progress frame with the value stage 1 sends', () => {
    const frames = customs(project(journal()), 'engine_progress');
    expect(frames.map((f) => f['kind'])).to.deep.equal([
      'started',
      'command',
      'output',
      'artifact',
      'approval',
      'artifact',
      'finished',
    ]);
    expect(frames[1]).to.deep.equal({
      tool: 'code_task',
      taskId: RUN,
      kind: 'command',
      text: 'python report.py',
      data: { callId: 'c1' },
    });
    expect(frames.every((f) => !('turnId' in f) && !('seq' in f))).to.equal(
      true,
    );
  });

  it('PR-2: the frames are deterministic, so a resumed stream replays them identically', () => {
    const events = journal();
    const first = new RunProjector({
      runId: RUN,
      sessionId: SESSION,
      conversationId: SESSION,
      goal: 'g',
      audience: 'owner',
    });
    first.begin();
    const second = new RunProjector({
      runId: RUN,
      sessionId: SESSION,
      conversationId: SESSION,
      goal: 'g',
      audience: 'owner',
    });
    second.begin();
    expect(second.project(events)).to.deep.equal(first.project(events));
  });

  it('PR-3: an engine approval is a card naming the call, then its resolution', () => {
    const frames = project(journal());
    expect(customs(frames, 'approval_requested')).to.deep.equal([
      {
        interactionId: IX,
        toolCallId: IX,
        toolName: 'create_issue',
        args: '{"title":"Q3"}',
        argsSummary: null,
        seq: 11,
        afterTurns: 0,
        decidedBefore: 0,
        requestedAt: '2026-10-03T12:00:00.000Z',
      },
    ]);
    expect(customs(frames, 'approval_resolved')).to.deep.equal([
      { interactionId: IX, decision: 'approved' },
    ]);
  });

  it("PR-4: the engine's answer is the turn's text, its final artifact one card, and the run finishes", () => {
    const frames = project(journal());
    const text = frames
      .filter((f) => f.type === 'TEXT_MESSAGE_CONTENT')
      .map((f) => String(f['delta']))
      .join('');
    expect(text).to.contain('The report is in out/report.md.');
    expect(
      cards(frames).map((c) => [c['artifactId'], c['lifecycle']]),
    ).to.deep.equal([['art-1', 'final']]);
    expect(frames.some((f) => f.type === 'RUN_ERROR')).to.equal(false);
    const snapshot = frames.find(
      (f) =>
        f.type === 'STATE_SNAPSHOT' &&
        (f['snapshot'] as Record<string, unknown>)['final'] === true,
    );
    // U3 TL-8: the saved answer names the final file, so it is there after a reload.
    expect(
      (snapshot?.['snapshot'] as Record<string, unknown>)['answer'],
    ).to.equal(
      'The report is in out/report.md.\n\n::artifact[report.md](record:art-1){text/markdown||art-1|file|1}',
    );
  });

  it("PR-4: anyone but the owner gets no progress lines (commands, output, diffs are the owner's)", () => {
    for (const audience of ['shared', 'external', 'slack'] as const) {
      expect(
        customs(project(journal(), audience), 'engine_progress'),
        audience,
      ).to.deep.equal([]);
    }
  });
});

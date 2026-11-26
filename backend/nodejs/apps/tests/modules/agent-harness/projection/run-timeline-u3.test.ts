/**
 * U3 TL-1..TL-9: what a durable run leaves in its conversation for the UI after
 * a reload: an engine's task (its progress lines), approvals and what became of
 * them, children as `sub_agent` parts, and an engine's final files as markers.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import {
  ArtifactAccess,
  markerArtifactIds,
} from '../../../../src/modules/agent-harness/projection/artifact-access';
import {
  foldTimeline,
  MAX_PERSISTED_ENGINE_CHARS,
} from '../../../../src/modules/agent-harness/projection/timeline';
import {
  buildCompletion,
  buildParts,
  runTranscript,
} from '../../../../src/modules/agent-harness/projection/transcript';

const RUN = 'run-u3';
const SESSION = 'ses-u3';
const IX = 'ix.approve.k0123456789abcdef0123456789abcdef01234567';

let seq = 0;
const event = (
  type: string,
  data: Record<string, unknown>,
  writer: 'node' | 'worker' = 'worker',
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
    critical: !type.startsWith('x.'),
    ts: `2026-10-04T12:00:${String(seq).padStart(2, '0')}.000Z`,
    writer,
    visibility: 'owner',
    data,
  }) as JournalEvent;

const line = (
  kind: string,
  text: string,
  data: Record<string, unknown> = {},
): JournalEvent =>
  event('x.pipeshub.engine.progress', {
    tool: 'code_task',
    taskId: RUN,
    kind,
    text,
    data,
    turnId: 'trn_1',
    seq: seq + 1,
  });

const presented = (
  artifactId: string,
  lifecycle: string,
  extra: Record<string, unknown> = {},
): JournalEvent =>
  event('x.pipeshub.artifact.presented', {
    artifactId,
    fileName: `${artifactId}.md`,
    mimeType: 'text/markdown',
    sizeBytes: 10,
    artifactType: 'file',
    isTemporary: false,
    recordId: artifactId,
    version: 1,
    visibility: 'VISIBLE',
    card: true,
    lifecycle,
    ...extra,
  });

const engineAnswer = (text: string) =>
  event('model.response', {
    messageRef: inlineContentRef(canonicalJsonBytes({ text, toolCalls: [] })),
    toolCalls: [],
    usage: { inputTokens: 0, outputTokens: 0 },
    provider: 'engine',
    transport: 'engine',
  });

function engineJournal(
  end: 'completed' | 'cancelled' | 'failed' = 'completed',
  decided: string | null = 'approved',
): JournalEvent[] {
  seq = 0;
  return [
    event('run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }, 'node'),
    event('turn.started', { turn: 1 }),
    line('started', 'Write the Q3 report'),
    line('command', 'python report.py', { callId: 'c1' }),
    line('output', 'wrote out/report.md', { callId: 'c1', stream: 'stdout' }),
    presented('art-draft', 'draft'),
    presented('art-final', 'draft'),
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
    ...(decided !== null
      ? [
          event(
            'interaction.resolved',
            { interactionId: IX, decision: decided, by: 'p_user', channel: 'web' },
            'node',
          ),
        ]
      : []),
    presented('art-final', 'final', { version: 2 }),
    presented('art-draft', 'discarded'),
    presented('art-norecord', 'final', { recordId: undefined }),
    engineAnswer('The report is in out/report.md.'),
    ...(end === 'completed'
      ? [
          line('finished', 'The report is in out/report.md.', { success: true }),
          event('x.pipeshub.answer.finalized', {
            outcome: 'answered',
            answer: 'The report is in out/report.md.',
            partTexts: ['The report is in out/report.md.'],
            citations: [],
          }),
          event('run.completed', { outcome: 'answered' }),
        ]
      : end === 'cancelled'
        ? [event('run.cancelled', {}, 'node')]
        : [
            line('finished', '', { success: false, reason: 'budget_exhausted' }),
            event('run.failed', { errorType: 'budget_exhausted' }),
          ]),
  ];
}

function parentJournal(): JournalEvent[] {
  seq = 0;
  const spawn = (child: string, callId: string, role: string) => ({
    id: callId,
    name: 'spawn_agent',
    args: { role, goal: `do ${role}` },
    child,
  });
  const calls = [spawn('run-c1', 'c1', 'jira'), spawn('run-c2', 'c2', 'docs')];
  return [
    event('run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }, 'node'),
    event('turn.started', { turn: 1 }),
    event('model.response', {
      messageRef: inlineContentRef(
        canonicalJsonBytes({
          text: 'Delegating.',
          toolCalls: calls.map(({ id, name, args }) => ({ id, name, args })),
        }),
      ),
      toolCalls: calls.map((call) => ({
        callId: call.id,
        modelName: call.name,
        argsDigest: `sha256:${'c'.repeat(64)}`,
      })),
      usage: { inputTokens: 0, outputTokens: 0 },
      provider: 'p',
      transport: 't',
    }),
    ...calls.map((call, index) =>
      event('child.spawned', {
        childRunId: call.child,
        invocationId: `t1.${String(index)}`,
        grantDigest: `sha256:${'a'.repeat(64)}`,
      }),
    ),
    event('child.completed', {
      childRunId: 'run-c1',
      resultRef: { ref: 'inline:e30=', hash: `sha256:${'b'.repeat(64)}`, size: 2 },
      labels: { integrity: 'internal' },
      outcome: 'answered',
    }),
    event('child.completed', {
      childRunId: 'run-c2',
      resultRef: { ref: 'inline:e30=', hash: `sha256:${'b'.repeat(64)}`, size: 2 },
      labels: { integrity: 'internal' },
      outcome: 'failed',
      errorType: 'budget_exhausted',
    }),
  ];
}

describe('agent-harness projection: the durable run after a reload (U3)', () => {
  it('TL-1: an engine run keeps its progress lines in journal order', () => {
    const timeline = foldTimeline(engineJournal(), RUN);
    expect(timeline.engine?.taskId).to.equal(RUN);
    expect(timeline.engine?.frames.map((f) => f.kind)).to.deep.equal([
      'started',
      'command',
      'output',
      'approval',
      'finished',
    ]);
    expect(timeline.engine?.frames[1]).to.deep.equal({
      tool: 'code_task',
      taskId: RUN,
      kind: 'command',
      text: 'python report.py',
      data: { callId: 'c1' },
    });
    expect(timeline.engine?.truncated).to.equal(false);
  });

  it('TL-2: output and diffs past the bound are left out; the card’s structure never is', () => {
    seq = 0;
    const big = 'x'.repeat(30_000);
    const events = [
      line('started', 'goal'),
      ...Array.from({ length: 10 }, (_, i) => [
        line('command', `step ${String(i)}`),
        line('output', big),
        line('diff', 'out/a.md', { change: 'modified', diff: big }),
        line('version', '', { revision: i + 1 }),
      ]).flat(),
      line('artifact', 'a.md', { artifactId: 'a', lifecycle: 'final' }),
      line('finished', 'done', { success: true }),
    ];
    const engine = foldTimeline(events, RUN).engine;
    expect(engine?.truncated).to.equal(true);
    const kinds = engine?.frames.map((f) => f.kind) ?? [];
    expect(kinds.filter((k) => k === 'command')).to.have.length(10);
    expect(kinds.filter((k) => k === 'version')).to.have.length(10);
    expect(kinds.filter((k) => k === 'output').length).to.be.lessThan(10);
    expect(kinds).to.include.members(['started', 'artifact', 'finished']);
    const kept = (engine?.frames ?? [])
      .filter((f) => f.kind === 'output' || f.kind === 'diff')
      .reduce((sum, f) => sum + f.text.length + JSON.stringify(f.data).length, 0);
    expect(kept).to.be.at.most(MAX_PERSISTED_ENGINE_CHARS);
  });

  it('TL-3: approvals: decided, closed, and expired when the run ends undecided', () => {
    const status = (journal: JournalEvent[]) =>
      foldTimeline(journal, RUN).approvals.map((a) => a.status);
    expect(status(engineJournal('completed', 'approved'))).to.deep.equal(['approved']);
    expect(status(engineJournal('completed', 'denied'))).to.deep.equal(['denied']);
    expect(status(engineJournal('completed', 'cancelled'))).to.deep.equal(['closed']);
    for (const end of ['completed', 'cancelled', 'failed'] as const) {
      expect(status(engineJournal(end, null)), end).to.deep.equal(['expired']);
    }
    const [view] = foldTimeline(engineJournal(), RUN).approvals;
    expect(view).to.include({
      interactionId: IX,
      toolCallId: IX,
      toolName: 'create_issue',
      args: '{"title":"Q3"}',
    });
  });

  it('TL-3: a loop call’s approval names the call', () => {
    seq = 0;
    const events = [
      event('turn.started', { turn: 1 }),
      event('model.response', {
        messageRef: inlineContentRef(
          canonicalJsonBytes({
            text: '',
            toolCalls: [{ id: 'c9', name: 'jira_create', args: { summary: 'x' } }],
          }),
        ),
        toolCalls: [{ callId: 'c9', modelName: 'jira_create', argsDigest: `sha256:${'c'.repeat(64)}` }],
        usage: { inputTokens: 0, outputTokens: 0 },
        provider: 'p',
        transport: 't',
      }),
      event('x.pipeshub.tool.call_presented', {
        callId: 'c9',
        toolName: 'jira.create_issue',
        argsJson: '{"summary":"x"}',
        argsSummary: 'Create “x”',
        displayName: 'Created a Jira issue',
      }),
      event('interaction.requested', {
        interactionId: 'ix.approve.t1.0',
        kind: 'approval',
        bindingDigest: `sha256:${'a'.repeat(64)}`,
      }),
    ];
    const [view] = foldTimeline(events, RUN).approvals;
    expect(view).to.include({
      interactionId: 'ix.approve.t1.0',
      toolCallId: 'c9',
      toolName: 'jira.create_issue',
      args: '{"summary":"x"}',
      argsSummary: 'Create “x”',
      displayName: 'Created a Jira issue',
      status: 'pending',
      afterTurns: 1,
    });
  });

  it('TL-4: children: role from the spawn call, status and error type', () => {
    const children = foldTimeline(parentJournal(), RUN).children;
    expect(
      children.map((c) => [c.runId, c.role, c.status, c.errorType ?? null]),
    ).to.deep.equal([
      ['run-c1', 'pipeshub-subagent-jira', 'completed', null],
      ['run-c2', 'pipeshub-subagent-docs', 'failed', 'budget_exhausted'],
    ]);
  });

  it('TL-5: an engine run’s parts: the task first, the approval after the turn it was asked in', () => {
    const parts = buildParts(foldTimeline(engineJournal(), RUN));
    expect(parts.map((p) => p['type'])).to.deep.equal([
      'engine_task',
      'approval',
      'text',
    ]);
    expect(parts[0]).to.include({ type: 'engine_task', runId: RUN, taskId: RUN });
    expect((parts[0]?.['frames'] as unknown[]).length).to.equal(5);
    expect(parts[1]).to.deep.equal({
      type: 'approval',
      interactionId: IX,
      toolCallId: IX,
      toolName: 'create_issue',
      args: '{"title":"Q3"}',
      status: 'approved',
      seq: 9,
      afterTurns: 0,
      // WP-113: no ask of the run was decided before it.
      decidedBefore: 0,
      // WP-97: when it was asked, so a reload still says since when it waits.
      requestedAt: '2026-10-04T12:00:09.000Z',
      runId: RUN,
    });
  });

  it('TL-6: a loop parent’s children are sub_agent parts after the delegating turn', () => {
    const parts = buildParts(foldTimeline(parentJournal(), RUN));
    expect(parts.map((p) => p['type'])).to.deep.equal([
      'text',
      'tool_call',
      'tool_call',
      'sub_agent',
      'sub_agent',
    ]);
    expect(parts[3]).to.deep.equal({
      type: 'sub_agent',
      runId: 'run-c1',
      parentRunId: RUN,
      roleName: 'pipeshub-subagent-jira',
      // CJFIX-B: named by its spawn call's goal, so its job card never reads the role id.
      goal: 'do jira',
      status: 'completed',
      parts: [],
    });
    expect(parts[4]).to.include({ status: 'failed', errorType: 'budget_exhausted' });
  });

  it('TL-6: a cancelled child is a failed part that says it was cancelled', () => {
    const journal = parentJournal();
    const last = journal[journal.length - 1] as JournalEvent;
    (last.data as Record<string, unknown>)['outcome'] = 'cancelled';
    delete (last.data as Record<string, unknown>)['errorType'];
    const parts = buildParts(foldTimeline(journal, RUN));
    expect(parts[4]).to.include({ status: 'failed', errorType: 'cancelled' });
  });

  it('TL-7: every golden journal’s parts are unchanged', () => {
    const dir = join(__dirname, '..', '..', '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals');
    // Legacy goldens only: the U3 journals are this WP's own engine and child runs.
    const scenarios = readdirSync(dir).filter((f) => f.endsWith('.parts.json') && !f.startsWith('u3_'));
    expect(scenarios.length).to.be.greaterThan(10);
    for (const file of scenarios) {
      const journalFile = join(dir, file.replace('.parts.json', '.journal.json'));
      const golden = JSON.parse(readFileSync(join(dir, file), 'utf8')) as {
        runId?: string;
        parts: unknown[];
      };
      const journal = JSON.parse(readFileSync(journalFile, 'utf8')) as {
        runId?: string;
        events: JournalEvent[];
      };
      const runId = golden.runId ?? journal.runId ?? journal.events[0]?.runId ?? '';
      const timeline = foldTimeline(journal.events, runId);
      expect(timeline.engine, file).to.equal(null);
      expect(timeline.approvals, file).to.deep.equal([]);
      expect(timeline.children, file).to.deep.equal([]);
    }
  });

  it('TL-8: an engine’s final files are markers in the saved answer; drafts, discards and record-less cards are not', () => {
    const completion = buildCompletion(foldTimeline(engineJournal(), RUN));
    const answer = String(completion?.['answer']);
    expect(markerArtifactIds(answer)).to.deep.equal(['art-final']);
    expect(answer).to.equal(
      'The report is in out/report.md.\n\n::artifact[art-final.md](record:art-final){text/markdown||art-final|file|2}',
    );
    const parts = completion?.['parts'] as Array<Record<string, unknown>>;
    const final = parts.find((p) => p['isFinal'] === true);
    expect(markerArtifactIds(final?.['content'])).to.deep.equal(['art-final']);
  });

  it('TL-8: a loop run’s answer is unchanged', () => {
    const journal = parentJournal();
    journal.push(
      presented('art-loop', 'final', { callId: 'c1' }),
      event('x.pipeshub.answer.finalized', {
        outcome: 'answered',
        answer: 'Done.',
        partTexts: ['Done.'],
        citations: [],
      }),
      event('run.completed', { outcome: 'answered' }),
    );
    expect(buildCompletion(foldTimeline(journal, RUN))?.['answer']).to.equal('Done.');
  });

  it('TL-8: a failed engine run keeps its task, approval and the user’s messages with the failure', () => {
    const transcript = runTranscript(engineJournal('failed', null), RUN);
    expect(transcript?.kind).to.equal('failure');
    const parts = (transcript as { parts?: Array<Record<string, unknown>> }).parts ?? [];
    expect(parts.map((p) => p['type'])).to.deep.equal(['engine_task', 'approval']);
    expect(parts[1]?.['status']).to.equal('expired');
    expect(transcript).to.include({
      code: 'budget_exhausted',
      message: 'This task reached its spending limit and was stopped.',
    });
  });

  it('TL-9: another viewer of the saved conversation gets no task or approval parts, and a hidden file’s marker is replaced', () => {
    const completion = buildCompletion(foldTimeline(engineJournal(), RUN));
    const message = {
      messageType: 'bot_response',
      content: completion?.['answer'],
      parts: completion?.['parts'],
    };
    const [shared] = ArtifactAccess.hideAll().messages([message]) as Array<
      Record<string, unknown>
    >;
    expect(String(shared?.['content'])).to.contain('::artifact_unavailable');
    expect(String(shared?.['content'])).not.to.contain('art-final');
    const types = (shared?.['parts'] as Array<Record<string, unknown>>).map((p) => p['type']);
    expect(types).to.deep.equal(['text']);
    const [owner] = ArtifactAccess.showing(['art-final']).messages([message]) as Array<
      Record<string, unknown>
    >;
    expect(String(owner?.['content'])).to.contain('record:art-final');
  });
});

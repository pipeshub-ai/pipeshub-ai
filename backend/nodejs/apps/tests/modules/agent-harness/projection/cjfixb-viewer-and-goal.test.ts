/**
 * CJFIX-B (ADR-CJ-01 D4, D5): a loop sub-agent is named by its `spawn_agent` goal on its live RUN_STARTED, its saved
 * `sub_agent` part and its activity step; a shared viewer's live stream says a child started and ended, as the
 * viewer's saved answer does, and nothing of what it did.
 */
import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { AGUI } from '../../../../src/modules/agent-harness/projection/frames';
import { frameFor } from '../../../../src/modules/agent-harness/projection/audiences';
import { RunProjector } from '../../../../src/modules/agent-harness/projection/run-projector';
import { foldTimeline } from '../../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../../src/modules/agent-harness/projection/transcript';

const PARENT = 'run-parent';
const CHILD = 'run-child';

const event = (
  seq: number,
  type: string,
  data: Record<string, unknown>,
  runId = PARENT,
  streamId = 'session-1',
): JournalEvent => ({
  orgId: 'org-1',
  sessionId: 'session-1',
  streamId,
  seq,
  runId,
  type,
  schemaVersion: 1,
  critical: true,
  visibility: 'owner',
  ts: `2026-10-03T12:00:0${String(seq)}.000Z`,
  writer: 'node',
  data,
});

const context = (runId: string, parentRunId?: string) => ({
  runId,
  sessionId: 'session-1',
  conversationId: 'session-1',
  goal: 'goal',
  audience: 'owner' as const,
  ...(parentRunId !== undefined ? { parentRunId } : {}),
});


const GOAL = 'Read the Q3 tickets and release them';

const spawned = (name: string, args: Record<string, unknown>) => [
  event(1, 'run.claimed', { attempt: 1, fence: 1, leaseMs: 1000 }),
  event(2, 'turn.started', { turn: 1 }),
  event(3, 'model.response', {
    messageRef: inlineContentRef(canonicalJsonBytes({ text: '', toolCalls: [{ id: 'c1', name, args }] })),
    toolCalls: [{ callId: 'c1', modelName: name, argsDigest: `sha256:${'c'.repeat(64)}` }],
    usage: { inputTokens: 0, outputTokens: 0 },
    provider: 'director',
    transport: 'synthetic',
  }),
  event(4, 'child.spawned', { childRunId: CHILD, invocationId: 't1.0', grantDigest: `sha256:${'a'.repeat(64)}` }),
  event(5, 'child.completed', {
    childRunId: CHILD,
    resultRef: { ref: 'inline:e30=', hash: `sha256:${'b'.repeat(64)}`, size: 2 },
    labels: { integrity: 'internal' },
    outcome: 'answered',
  }),
];

const frames = (events: JournalEvent[], audience: 'owner' | 'shared' | 'slack' | 'external' = 'owner') =>
  new RunProjector({ ...context(PARENT), audience }).project(events).map((f) => f.frame);

describe('CJFIX-B: a loop sub-agent is named by its goal (F10)', () => {
  it("the child's view, its saved part and its live RUN_STARTED carry its spawn call's goal", () => {
    // A scripted or long goal: the name is its first line, as the Jobs list titles the job (`jobTitle`).
    const events = spawned('spawn_agent', { role: 'general', goal: `  ${GOAL}  \ntool search_tools {"query":"x"}\nhold` });
    const timeline = foldTimeline(events, PARENT);
    expect(timeline.children[0]).to.include({ goal: GOAL, role: 'pipeshub-subagent-general' });
    expect(timeline.children[0]).to.not.have.property('codeTaskGoal');
    const part = buildParts(timeline).find((p) => p['type'] === 'sub_agent');
    expect(part).to.include({ goal: GOAL, roleName: 'pipeshub-subagent-general' });
    expect(frames(events).find((f) => f.type === AGUI.RUN_STARTED && f['runId'] === CHILD)).to.include({ goal: GOAL });
  });

  it('a code_task child keeps codeTaskGoal and gets no loop goal; a spawn with no goal gets none', () => {
    const coded = foldTimeline(spawned('code_task', { goal: 'Write it' }), PARENT).children[0];
    expect(coded).to.include({ codeTaskGoal: 'Write it' });
    expect(coded).to.not.have.property('goal');
    for (const args of [{ role: 'general' }, { role: 'general', goal: '   ' }, { role: 'general', goal: 7 }]) {
      expect(foldTimeline(spawned('spawn_agent', args), PARENT).children[0]).to.not.have.property('goal');
    }
  });
});

describe('CJFIX-B: a shared viewer sees its jobs live (F3, D14)', () => {
  const lifecycle = (audience: 'owner' | 'shared' | 'slack' | 'external') =>
    frames(spawned('spawn_agent', { role: 'general', goal: GOAL }), audience).filter(
      (f) => f['parentRunId'] !== undefined || (typeof f['stepName'] === 'string' && f['stepName'].startsWith('sub_agent:')),
    );

  it('gets the child’s RUN_STARTED with its goal and its RUN_FINISHED, and no step frame', () => {
    const shown = lifecycle('shared');
    expect(shown.map((f) => f.type)).to.deep.equal([AGUI.RUN_STARTED, AGUI.RUN_FINISHED]);
    expect(shown[0]).to.include({ runId: CHILD, parentRunId: PARENT, goal: GOAL });
    expect(shown[1]).to.deep.equal({ type: AGUI.RUN_FINISHED, runId: CHILD, parentRunId: PARENT });
  });

  it('the owner keeps the step frames around them; Slack and MCP get none of it', () => {
    expect(lifecycle('owner').map((f) => f.type)).to.deep.equal([AGUI.STEP_STARTED, AGUI.RUN_STARTED, AGUI.RUN_FINISHED, AGUI.STEP_FINISHED]);
    expect(lifecycle('slack')).to.deep.equal([]);
    expect(lifecycle('external')).to.deep.equal([]);
  });

  it("a child's end frame is passed as it is, while the answer's end is still checked", () => {
    const context = {
      checkedCitations: () => ({ citations: () => [], messages: (m: unknown) => m }),
      checkedArtifacts: () => ({ card: (c: unknown) => c, text: (t: string) => t, messages: (m: unknown) => m, completion: (c: Record<string, unknown>) => c }),
      asksUser: () => false,
    };
    const child = { type: AGUI.RUN_FINISHED, runId: CHILD, parentRunId: PARENT };
    expect(frameFor('shared', child, context as never)).to.deep.equal(child);
    const answer = { type: AGUI.RUN_FINISHED, runId: PARENT, result: { conversation: { messages: [{ id: 'm', parts: [{ type: 'tool_call' }] }] } } };
    const checked = frameFor('shared', answer, context as never);
    expect(checked).to.have.nested.property('result.conversation.messages[0].parts').that.deep.equals([]);
  });
});

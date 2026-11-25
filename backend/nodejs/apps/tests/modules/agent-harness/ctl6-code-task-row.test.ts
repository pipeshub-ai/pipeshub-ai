/**
 * CTL-6: the loop's handoff is a visible step. A child spawned by a `code_task` call is the sub-agent row
 * "Code task: <goal>", its goal read from the call that spawned it; any other child keeps its role.
 */
import { expect } from 'chai';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { project, journalOnlySteps } from '../../contracts/support/durable';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion } from '../../../src/modules/agent-harness/projection/transcript';

const RUN = 'run-parent';
const sha = (c: string): string => `sha256:${c.repeat(64)}`;
const inline = (value: unknown) => inlineContentRef(canonicalJsonBytes(value));

function journal(call: { name: string; args: Record<string, unknown> }): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ['turn.started', { turn: 1 }, 'worker'],
    [
      'model.response',
      {
        messageRef: inline({ text: '', toolCalls: [{ id: 'call-1', name: call.name, args: call.args }] }),
        toolCalls: [{ callId: 'call-1', modelName: call.name, argsDigest: sha('c') }],
        usage: { inputTokens: 1, outputTokens: 1 },
        provider: 'p',
        transport: 't',
      },
      'worker',
    ],
    ['child.spawned', { childRunId: 'run-child', invocationId: 't1.0', taskId: 'policy', grantDigest: sha('d') }, 'node'],
    ['child.completed', { childRunId: 'run-child', resultRef: { ref: 'inline:e30=', hash: sha('e'), size: 2 }, labels: { integrity: 'internal' }, outcome: 'answered' }, 'node'],
    ['x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'Done.', partTexts: ['Done.'], citations: [] }, 'worker'],
    ['run.completed', { outcome: 'answered' }, 'node'],
  ];
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: 'org-1',
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        attemptId: writer === 'worker' ? `${RUN}.a1` : undefined,
        fence: writer === 'worker' ? 1 : undefined,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: '2026-10-15T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const child = (events: JournalEvent[]) =>
  (buildCompletion(foldTimeline(events, RUN)) as { parts: Array<Record<string, unknown>> }).parts.find(
    (part) => part['type'] === 'sub_agent',
  );

describe('CTL-6: the code_task handoff row', () => {
  it('names the child by the goal of the code_task call that spawned it', () => {
    const row = child(journal({ name: 'code_task', args: { goal: 'Write the travel policy to travel_policy.md' } }));
    expect(row).to.include({ codeTaskGoal: 'Write the travel policy to travel_policy.md', runId: 'run-child' });
  });

  it('QAFIX-8: the live step names the engine the code task hands to, so it never reads "pipeshub-subagent"', () => {
    const pinned = {
      ...journal({ name: 'code_task', args: { goal: 'Write it' } })[1]!,
      type: 'run.spec_pinned',
      writer: 'node',
      data: { spec: { extensions: { 'x.pipeshub': { codeTask: { profile: { engine: { kind: 'codex' } } } } } } },
    } as JournalEvent;
    const steps = (events: JournalEvent[]) => {
      const journalled = { scenario: 'qafix8', query: 'q', runId: RUN, sessionId: 'ses-1', events, live: [] };
      return project(journalled, journalOnlySteps(journalled))
        .map((f) => f.frame)
        .filter((f) => f['type'] === 'STEP_STARTED' && String(f['stepName']).startsWith('sub_agent:'));
    };
    const handed = journal({ name: 'code_task', args: { goal: 'Write it' } });
    handed.splice(2, 0, pinned);
    expect(steps(handed.map((e, i) => ({ ...e, seq: i + 1 }) as JournalEvent))[0]).to.include({ childEngine: 'codex' });
    expect(steps(journal({ name: 'spawn_agent', args: { role: 'analysis' } }))[0]).to.not.have.property('childEngine');
  });

  it('any other delegated child keeps its role and no goal', () => {
    const row = child(journal({ name: 'spawn_agent', args: { role: 'analysis', goal: 'Analyse it' } }));
    expect(row).to.include({ roleName: 'pipeshub-subagent-analysis' });
    expect(row).to.not.have.property('codeTaskGoal');
  });

  describe('a refused hand-off the person can act on (the CTL-5b notice)', () => {
    const refused = (code: string): JournalEvent[] => {
      const events = journal({ name: 'code_task', args: { goal: 'Write it' } }).filter(
        (e) => e.type !== 'child.spawned' && e.type !== 'child.completed',
      );
      const at = events.findIndex((e) => e.type === 'x.pipeshub.answer.finalized');
      const notice = {
        ...events[at]!,
        type: 'x.pipeshub.run.notice',
        critical: false,
        data: { code },
      } as JournalEvent;
      return [...events.slice(0, at), notice, ...events.slice(at)].map((e, i) => ({ ...e, seq: i + 1 }) as JournalEvent);
    };

    it('the worker may journal it, with the codes the chat words', () => {
      for (const code of ['account_missing', 'model_access', 'engine_profile', 'engine_unavailable']) {
        expect(() =>
          validateJournalAppend({
            orgId: 'org-1',
            sessionId: 'ses-1',
            streamId: 'ses-1',
            runId: RUN,
            writer: 'worker',
            events: [{ type: 'x.pipeshub.run.notice', schemaVersion: 1, critical: false, visibility: 'owner', data: { code } }],
          } as never),
        ).to.not.throw();
      }
    });

    it('the answer carries it, saved and live', () => {
      const events = refused('account_missing');
      const parts = (buildCompletion(foldTimeline(events, RUN)) as { parts: Array<Record<string, unknown>> }).parts;
      expect(parts.filter((p) => p['type'] === 'notice')).to.deep.equal([{ type: 'notice', code: 'account_missing', runId: RUN }]);
      const journalled = { scenario: 'ctl6', query: 'q', runId: RUN, sessionId: 'ses-1', events, live: [] };
      const frames = project(journalled, journalOnlySteps(journalled)).map((f) => f.frame);
      expect(frames.filter((f) => f['name'] === 'notice').map((f) => f['value'])).to.deep.equal([{ code: 'account_missing' }]);
    });
  });
});

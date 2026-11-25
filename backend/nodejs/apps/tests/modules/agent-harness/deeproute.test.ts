/**
 * DEEPROUTE: work bigger than a chat reply goes to the coding engine. Node's part: the org's switch ("Hand long work
 * to the coding engine", on by default) is read at admission and pinned with the engine authority
 * (`x.pipeshub.codeTask.autoRoute: false` when off); the worker's `x.pipeshub.run.handoff` names the code_task child
 * that follows it, saved and live, so the chat can say why the run handed off.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion } from '../../../src/modules/agent-harness/projection/transcript';
import { journalOnlySteps, project } from '../../contracts/support/durable';
import { testControlPlane } from '../control-plane/support/in-memory';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const ORG = 'org-1';
const USER = 'user-1';
const RUN = 'run-parent';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const sha = (c: string): string => `sha256:${c.repeat(64)}`;
const inline = (value: unknown) => inlineContentRef(canonicalJsonBytes(value));

const codeTaskOf = (spec: unknown): Record<string, unknown> | undefined =>
  (spec as { extensions?: Record<string, Record<string, unknown>> }).extensions?.['x.pipeshub']?.['codeTask'] as
    | Record<string, unknown>
    | undefined;

function journal(
  call: { name: string; args: Record<string, unknown> },
  handoff?: Record<string, unknown>,
): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ...(handoff !== undefined ? [['x.pipeshub.run.handoff', handoff, 'worker'] as [string, Record<string, unknown>, 'worker']] : []),
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
    ['child.spawned', { childRunId: 'run-child', invocationId: 't1.0', grantDigest: sha('d') }, 'node'],
    ['child.completed', { childRunId: 'run-child', resultRef: { ref: 'inline:e30=', hash: sha('e'), size: 2 }, labels: { integrity: 'internal' }, outcome: 'answered' }, 'node'],
    ['x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'Merge PR 41 first.', partTexts: ['Merge PR 41 first.'], citations: [] }, 'worker'],
    ['run.completed', { outcome: 'answered' }, 'node'],
  ];
  return specs.map(
    ([type, data, writer], index) =>
      ({
        orgId: ORG,
        sessionId: 'ses-1',
        streamId: 'ses-1',
        seq: index + 1,
        runId: RUN,
        attemptId: writer === 'worker' ? `${RUN}.a1` : undefined,
        fence: writer === 'worker' ? 1 : undefined,
        type,
        schemaVersion: 1,
        critical: !type.startsWith('x.'),
        ts: '2026-10-22T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const childRow = (events: JournalEvent[]) =>
  (buildCompletion(foldTimeline(events, RUN)) as { parts: Array<Record<string, unknown>> }).parts.find(
    (part) => part['type'] === 'sub_agent',
  );

const liveChildStart = (events: JournalEvent[]) => {
  const journalled = { scenario: 'deeproute', query: 'q', runId: RUN, sessionId: 'ses-1', events, live: [] };
  return project(journalled, journalOnlySteps(journalled))
    .map((f) => f.frame)
    .find((f) => f['type'] === 'RUN_STARTED' && f['runId'] === 'run-child');
};

describe('DEEPROUTE: the hand-off to the coding engine', () => {
  describe('the hand-off row', () => {
    const handed = { trigger: 'escalation', reason: 'many_steps' };

    it('DR-N1: the worker may journal x.pipeshub.run.handoff with the reasons the chat words, nothing else', () => {
      const append = (data: Record<string, unknown>) => () =>
        validateJournalAppend({
          orgId: ORG,
          sessionId: 'ses-1',
          streamId: 'ses-1',
          runId: RUN,
          writer: 'worker',
          events: [{ type: 'x.pipeshub.run.handoff', schemaVersion: 1, critical: false, visibility: 'owner', data }],
        } as never);
      for (const reason of ['research', 'analysis', 'planning', 'long_report', 'many_steps', 'context', 'budget']) {
        expect(append({ trigger: 'upfront', reason })).to.not.throw();
      }
      expect(append({ trigger: 'upfront', reason: 'vibes' })).to.throw();
    });

    it('DR-N2: the code_task child that follows names why, saved and live', () => {
      const events = journal({ name: 'code_task', args: { goal: 'Triage the PRs' } }, handed);
      expect(childRow(events)).to.deep.include({ codeTaskGoal: 'Triage the PRs', codeTaskHandoff: handed });
      expect(liveChildStart(events)).to.deep.include({ codeTaskGoal: 'Triage the PRs', codeTaskHandoff: handed });
    });

    it('DR-N2b: the row names the engine the run pinned for its hand-offs', () => {
      const events = journal({ name: 'code_task', args: { goal: 'Triage the PRs' } }, handed);
      const pinned = {
        ...events[1]!,
        type: 'run.spec_pinned',
        writer: 'node',
        data: { spec: { extensions: { 'x.pipeshub': { codeTask: { profile: { engine: { kind: 'claude_code' } } } } } } },
      } as JournalEvent;
      events.splice(2, 0, pinned);
      const renumbered = events.map((e, i) => ({ ...e, seq: i + 1 }) as JournalEvent);
      expect(childRow(renumbered)).to.deep.include({ codeTaskHandoff: { ...handed, engine: 'claude_code' } });
    });

    it('DR-N3: a code_task the model chose itself, and any other child, carry none', () => {
      expect(childRow(journal({ name: 'code_task', args: { goal: 'Write it' } }))).to.not.have.property('codeTaskHandoff');
      const other = journal({ name: 'spawn_agent', args: { role: 'analysis' } }, handed);
      expect(childRow(other)).to.not.have.property('codeTaskHandoff');
    });
  });

  describe("the org's switch", () => {
    let settings: FakeEngineCeilings;
    let preferences: InMemoryCodingEnginePreferences;
    let lines: Array<[string, Record<string, unknown> | undefined]>;

    const logger: HarnessLogger = {
      debug: () => undefined,
      info: (event, meta) => void lines.push([event, meta]),
      warn: (event, meta) => void lines.push([event, meta]),
      error: (event, meta) => void lines.push([event, meta]),
    };

    const engines = () =>
      new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences,
        transaction: testControlPlane(),
        availability: { unavailable: async () => new Set<string>() },
        logger,
      });

    beforeEach(() => {
      lines = [];
      preferences = new InMemoryCodingEnginePreferences();
      settings = new FakeEngineCeilings();
    });

    it('DR-N4: on by default; an admin turns it off and on, logged with ids only', async () => {
      expect((await engines().view(ORG, USER)).autoRoute).to.equal(true);
      expect(await engines().autoRoute(ORG, USER)).to.equal(true);
      expect((await engines().setOrgAutoRoute(ORG, 'admin-1', false)).autoRoute).to.equal(false);
      expect(await engines().autoRoute(ORG, USER)).to.equal(false);
      expect(lines).to.deep.include([
        'coding_engine.org_auto_route_set',
        { event: 'coding_engine.org_auto_route_set', orgId: ORG, actorId: 'admin-1', autoRoute: false },
      ]);
      expect((await engines().setOrgAutoRoute(ORG, 'admin-1', true)).autoRoute).to.equal(true);
    });

    it('DR-N5: a setting that cannot be read is the default, never a refusal', async () => {
      const down = new CodingEngineService({
        ceilings: settings,
        access: testModelAccess(),
        preferences: Object.assign(Object.create(preferences) as InMemoryCodingEnginePreferences, {
          read: () => Promise.reject(new Error('mongo down')),
        }) as never,
        transaction: testControlPlane(),
      });
      expect(await down.autoRoute(ORG, USER)).to.equal(true);
    });

    describe('admission pins it with the engine authority', () => {
      let now: number;
      let store: InMemoryExecutionStore;
      let execution: ExecutionService;

      const admitAndPin = async (): Promise<{ run: HarnessRun; codeTask: Record<string, unknown> | undefined }> => {
        now += 1;
        const outcome = await new RunAdmissionService({
          store,
          rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
          clock: { now: () => now },
          agentAccess: grantingAccess((key) => agentDefinition({ agentKey: key })),
          logger,
          executors: profileExecutor,
          engineProfiles: settings,
          modelAccess: testModelAccess(),
          codingEngine: engines(),
          workerFeatures: { live: async () => true, metrics: { withheld: () => undefined } } as never,
        }).submit({
          orgId: ORG,
          actorId: USER,
          authorization: 'Bearer t',
          durableRequested: true,
          idempotencyKey: `k-${String(now)}`,
          deadlineMs: 60_000,
          input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
        });
        if (outcome.route !== 'durable') return expect.fail(`expected a durable run, got ${JSON.stringify(outcome)}`);
        const run = outcome.admission.run;
        const claimed = await execution.claim({
          orgId: run.orgId,
          runId: run.runId,
          workerId: 'w',
          attemptId: `attempt-${run.runId}`,
          leaseDurationMs: 1_000,
        });
        return { run, codeTask: codeTaskOf(runSpecSchema.parse(claimed.pinned?.spec)) };
      };

      beforeEach(async () => {
        now = 1_000;
        store = new InMemoryExecutionStore({ now: () => now });
        execution = new ExecutionService({
          store,
          journal: store.journal,
          journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
          capabilities: { current: () => DURABLE },
          resolveSpec: runSpecResolver(profileExecutor),
        });
        await preferences.writeOrg(ORG, 'opencode');
      });

      it('DR-N6: on (the default), the authority is pinned as before, with no autoRoute', async () => {
        const { codeTask } = await admitAndPin();
        expect(codeTask).to.have.property('profile');
        expect(codeTask).to.not.have.property('autoRoute');
      });

      it('DR-N7: off, the authority still pins (the model can choose the engine) with autoRoute: false', async () => {
        await preferences.writeOrgAutoRoute(ORG, false);
        const { codeTask } = await admitAndPin();
        expect(codeTask).to.have.property('profile');
        expect(codeTask).to.include({ autoRoute: false });
      });
    });
  });

  describe("the engine's report as the run's answer (FIX-UI1 item 1)", () => {
    // A hand-off turn: the model's turn 1 calls code_task; the director's synthetic turn 2 delivers the report. The
    // worker's finalizer counts model turns only, so its partTexts has one entry; the transcript must not open a text
    // part for the synthetic turn, or the report lands on turn 1's part and the `isFinal` part is empty.
    const report = (
      JSON.parse(
        readFileSync(
          path.join(__dirname, '../../../../../../frontend/app/(main)/chat/components/message-area/__tests__/data/engine-report-turn.json'),
          'utf8',
        ),
      ) as { answer: string }
    ).answer;
    const delivered = (): JournalEvent[] => {
      const events = journal({ name: 'code_task', args: { goal: 'IPL 2026 report' } }, { trigger: 'upfront', reason: 'long_report' });
      const at = events.findIndex((e) => e.type === 'x.pipeshub.answer.finalized');
      const synthetic = {
        ...events[at - 1]!,
        type: 'model.response',
        critical: true,
        writer: 'worker',
        attemptId: `${RUN}.a1`,
        fence: 1,
        data: {
          messageRef: inline({ text: '', toolCalls: [] }),
          toolCalls: [],
          usage: { inputTokens: 0, outputTokens: 0 },
          provider: 'director',
          transport: 'synthetic',
        },
      } as JournalEvent;
      const finalized = { ...events[at]!, data: { outcome: 'answered', answer: report, partTexts: [report], citations: [] } } as JournalEvent;
      return [...events.slice(0, at), synthetic, finalized, ...events.slice(at + 1)].map(
        (e, index) => ({ ...e, seq: index + 1 }) as JournalEvent,
      );
    };

    it('the isFinal text part carries the report, and no empty text part is left', () => {
      const parts = (buildCompletion(foldTimeline(delivered(), RUN)) as { parts: Array<Record<string, unknown>> }).parts;
      const texts = parts.filter((part) => part['type'] === 'text');
      expect(texts).to.have.length(1);
      expect(texts[0]).to.include({ content: report, isFinal: true });
      expect(parts.map((part) => part['type'])).to.deep.equal(['text', 'tool_call', 'sub_agent']);
    });

    it('the answer the classic view reads is unchanged', () => {
      const completion = buildCompletion(foldTimeline(delivered(), RUN)) as { answer: string };
      expect(completion.answer).to.equal(report);
    });
  });
});

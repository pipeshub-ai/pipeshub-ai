/**
 * GA-02, GA-03, GA-04 (G-03..G-05, C20.04..C20.06) on Node: a turn's run gates are kept on the run, pinned as
 * `RunSpec.gates`, required of the worker (`run_gates`, for the session too), held by every child, refused rather than
 * run ungated on today's loop, and what the gates journal reads in the conversation: each dry-run call marked, a
 * notice that the run was a dry run, and the run's verification with its first reason.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HarnessRollout } from '../../../src/modules/agent-harness/application/harness-rollout';
import {
  RunAdmissionService,
  SubmitDurableRun,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import { RunSpec, runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import {
  ASSISTANT_AGENT_DEFINITION_KEY,
  assistantModesWith,
} from '../../../src/modules/agent-harness/domain/rollout';
import { agentDefinition } from './support/agent-definition';
import { agentRunDefinitionSchema } from '../../../src/modules/agent-harness/domain/agent-definition';
import { childGrants, childRunSpec } from '../../../src/modules/agent-harness/domain/child-runs';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import {
  admittedGates,
  inheritedGates,
  requestedGates,
  RUN_GATES_FEATURE,
  type RunGates,
} from '../../../src/modules/agent-harness/domain/run-gates';
import { PinnedRunSpec, resolveRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../src/modules/agent-harness/projection/transcript';

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const DRY: RunGates = { dryRun: true };
const ALL: RunGates = {
  dryRun: true,
  planApproval: 'required',
  verify: { checks: [{ kind: 'file', path: 'out/report.pdf', format: 'pdf' }], onFail: 'continue', maxRounds: 2 },
};

let turns = 0;
const turn = (overrides: Partial<SubmitDurableRun> = {}): SubmitDurableRun => ({
  orgId: 'org-1',
  actorId: 'user-1',
  idempotencyKey: `gates-${String((turns += 1))}`,
  firstSubmission: true,
  deadlineMs: 60_000,
  authorization: 'Bearer caller',
  input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
  durableRequested: true,
  ...overrides,
});

const world = (agentGates?: Record<string, unknown>) => {
  const store = new InMemoryExecutionStore({ now: () => 1_000 });
  const admissions = new RunAdmissionService({
    store,
    rollout: new HarnessRollout(
      {
        read: async () => ({
          infrastructureReady: true,
          killSwitch: false,
          orgEnabled: true,
          modelFamilies: null,
          assistantModes: assistantModesWith(true),
        }),
      },
      { resolve: async () => null },
    ),
    clock: { now: () => 1_000 },
    agentAccess: {
      authorizeRun: async () =>
        agentDefinition({
          agentKey: ASSISTANT_AGENT_DEFINITION_KEY,
          definitionVersion: 1,
          models: [],
          defaultReasoningEffort: null,
          skills: [],
          webSearch: null,
          ...(agentGates !== undefined ? { gates: agentGates } : {}),
        }),
    },
    logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
  });
  return { store, admissions };
};

describe('GA-02..GA-04: run gates on Node', () => {
  describe('the request', () => {
    it('reads the gates a body names, and nothing when it names none', () => {
      expect(requestedGates({ gates: ALL })).to.deep.equal({ kind: 'gates', gates: ALL });
      expect(requestedGates({})).to.deep.equal({ kind: 'none' });
      expect(requestedGates({ gates: {} })).to.deep.equal({ kind: 'none' });
    });

    it('refuses gates it cannot read, naming the field', () => {
      for (const gates of [{ dryRun: false }, { planApproval: 'maybe' }, { verify: { checks: [{ kind: 'file', path: '../x' }], onFail: 'flag' } }]) {
        const read = requestedGates({ gates });
        expect(read.kind).to.equal('invalid');
        if (read.kind === 'invalid') expect(read.message).to.match(/^gates/);
      }
    });
  });

  describe('admission and the pin', () => {
    it('keeps the gates on the run, requires run_gates of the worker, and the claim pins them', async () => {
      const { store, admissions } = world();
      const outcome = await admissions.submit(turn({ gates: ALL }));
      expect(outcome.route).to.equal('durable');
      if (outcome.route !== 'durable') return;
      const run = outcome.admission.run;
      expect(run.requires?.features).to.include(RUN_GATES_FEATURE);
      const subject = await store.readClaimSubject(run.orgId, run.runId);
      expect(subject?.gates).to.deep.equal(ALL);
      const spec = resolveRunSpec(subject!, DURABLE);
      expect(spec.gates).to.deep.equal(ALL);
      expect(runSpecSchema.safeParse(spec).success).to.equal(true);
    });

    it('a later turn of the session requires run_gates too: it folds what the gates journaled', async () => {
      const { admissions } = world();
      const first = await admissions.submit(turn({ gates: DRY }));
      if (first.route !== 'durable') throw new Error('expected durable');
      const second = await admissions.submit(turn({ sessionId: first.admission.run.sessionId }));
      if (second.route !== 'durable') throw new Error('expected durable');
      expect(second.admission.run.requires?.features).to.include(RUN_GATES_FEATURE);
    });

    it('a turn without gates pins none and requires nothing new', async () => {
      const { store, admissions } = world();
      const outcome = await admissions.submit(turn());
      if (outcome.route !== 'durable') throw new Error('expected durable');
      expect(outcome.admission.run.requires?.features).not.to.include(RUN_GATES_FEATURE);
      const subject = await store.readClaimSubject('org-1', outcome.admission.run.runId);
      expect(resolveRunSpec(subject!, DURABLE).gates).to.equal(undefined);
    });
  });

  describe("WP-51: the agent's own gates (WP-31 D)", () => {
    const AGENT = {
      planApproval: 'required',
      verify: { checks: [{ kind: 'command', argv: ['pytest', '-q'], timeoutS: 120 }], onFail: 'continue', maxRounds: 2 },
    } as const;

    it("GA-03, GA-04: every run of the agent is held to its plan approval and checks, with no gate of the turn's own", async () => {
      const { store, admissions } = world(AGENT);
      const outcome = await admissions.submit(turn());
      if (outcome.route !== 'durable') throw new Error('expected durable');
      expect(outcome.admission.run.requires?.features).to.include(RUN_GATES_FEATURE);
      const subject = await store.readClaimSubject('org-1', outcome.admission.run.runId);
      expect(resolveRunSpec(subject!, DURABLE).gates).to.deep.equal(AGENT);
    });

    it("GA-02: a turn adds to the agent's gates and never drops one: its dry run and checks join the agent's", async () => {
      const { store, admissions } = world(AGENT);
      const outcome = await admissions.submit(
        turn({ gates: { dryRun: true, verify: { checks: [{ kind: 'citations' }], onFail: 'flag' } } }),
      );
      if (outcome.route !== 'durable') throw new Error('expected durable');
      const subject = await store.readClaimSubject('org-1', outcome.admission.run.runId);
      expect(resolveRunSpec(subject!, DURABLE).gates).to.deep.equal({
        dryRun: true,
        planApproval: 'required',
        verify: { ...AGENT.verify, checks: [...AGENT.verify.checks, { kind: 'citations' }] },
      });
    });

    it('the same check twice is checked once, and an agent without gates leaves the turn as it was', () => {
      const once = admittedGates({ verify: { checks: [{ kind: 'citations' }], onFail: 'flag' } }, {
        verify: { checks: [{ kind: 'citations' }], onFail: 'continue' },
      });
      expect(once?.verify).to.deep.equal({ checks: [{ kind: 'citations' }], onFail: 'flag' });
      expect(admittedGates(undefined, DRY)).to.deep.equal(DRY);
      expect(admittedGates(undefined, undefined)).to.equal(undefined);
    });

    it("the authority's gates are read, and ones it could not read, or a dry run, refuse the agent", () => {
      const base = agentDefinition({ agentKey: 'a1', definitionVersion: 1, models: [], defaultReasoningEffort: null, skills: [], webSearch: null });
      expect(agentRunDefinitionSchema.safeParse({ ...base, gates: AGENT }).success).to.equal(true);
      expect(agentRunDefinitionSchema.safeParse({ ...base, gates: { invalid: true } }).success).to.equal(false);
      expect(agentRunDefinitionSchema.safeParse({ ...base, gates: { dryRun: true } }).success).to.equal(false);
    });
  });

  describe('children', () => {
    const table = JSON.parse(
      readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/child-grants.json'), 'utf8'),
    ) as { parentSpec: RunSpec };

    it("a child is held to its parent's dry run and plan gate, never its verify gate", () => {
      const parent: PinnedRunSpec = {
        spec: { ...table.parentSpec, gates: ALL },
        specDigest: `sha256:${'a'.repeat(64)}`,
        capabilities: {},
        capabilitiesDigest: `sha256:${'b'.repeat(64)}`,
      };
      const child = childRunSpec(parent, childGrants(parent.spec, {}, 1).grants!);
      expect(child.spec.gates).to.deep.equal({ dryRun: true, planApproval: 'required' });
      expect(inheritedGates(null, null)).to.equal(undefined);
    });
  });

  describe('the conversation', () => {
    let seq = 0;
    const event = (type: string, data: Record<string, unknown>, writer = 'worker'): JournalEvent =>
      ({
        orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId: 'run-1',
        type, schemaVersion: 1, critical: !type.startsWith('x.'), ts: '2026-10-31T12:00:00.000Z',
        writer, visibility: 'owner', data,
      }) as JournalEvent;

    it('GA-02: a dry run says so, and each call it recorded is marked as never made', () => {
      const timeline = foldTimeline(
        [
          event('run.spec_pinned', { spec: { runKind: 'interactive', gates: DRY } }, 'node'),
          event('turn.started', { turn: 1 }),
          event('model.response', {
            messageRef: inlineContentRef(
              canonicalJsonBytes({ text: '', toolCalls: [{ id: 'c1', name: 'send_email', args: { to: 'ops@acme.test' } }] }),
            ),
            toolCalls: [{ callId: 'c1', modelName: 'send_email', argsDigest: `sha256:${'d'.repeat(64)}` }],
            usage: { inputTokens: 0, outputTokens: 0 },
            provider: 'p',
            transport: 't',
          }),
          event('tool.outcome', { invocationId: 't1.0', status: 'ok', labels: { integrity: 'internal' }, shadow: { wouldAsk: true } }),
        ],
        'run-1',
      );
      expect(timeline.gates).to.deep.equal({ dryRun: true, planApproval: false, verify: false });
      const parts = buildParts(timeline);
      expect(parts.filter((p) => p['type'] === 'notice')).to.deep.equal([{ type: 'notice', code: 'dry_run', runId: 'run-1' }]);
      const tool = parts.find((p) => p['type'] === 'tool_call');
      expect(tool?.['dryRun']).to.deep.equal({ wouldAsk: true });
    });

    it('GA-04: the run reads as unverified with its first reason and every check of the round that settled it', () => {
      const failed = { kind: 'command', target: 'pytest', passed: false, reason: 'The command pytest exited with code 1.' };
      const timeline = foldTimeline(
        [
          event('verification.recorded', { status: 'unverified', round: 1, continuing: true, checks: [failed] }),
          event('verification.recorded', { status: 'unverified', round: 2, continuing: false, checks: [failed] }),
        ],
        'run-1',
      );
      expect(timeline.verification).to.deep.equal({ status: 'unverified', round: 2, checks: [failed] });
      expect(buildParts(timeline).filter((p) => p['type'] === 'notice')).to.deep.equal([
        // WP-51: the answer's verification panel lists every check, and that it was checked twice.
        { type: 'notice', code: 'unverified', item: failed.reason, checks: [failed], round: 2, runId: 'run-1' },
      ]);
    });

    it('GA-04: a run whose checks pass reads as verified', () => {
      const timeline = foldTimeline(
        [event('verification.recorded', { status: 'verified', round: 1, continuing: false, checks: [{ kind: 'file', passed: true }] })],
        'run-1',
      );
      expect(buildParts(timeline).filter((p) => p['type'] === 'notice')).to.deep.equal([
        { type: 'notice', code: 'verified', checks: [{ kind: 'file', passed: true }], round: 1, runId: 'run-1' },
      ]);
    });
  });
});

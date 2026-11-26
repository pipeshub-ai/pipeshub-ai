/**
 * FU4: a durable run started in the web app shows main's MCP sign-in card. Admission records where the run was
 * started and pins it (`x.pipeshub.surface`) only while a live worker reads it (`run_surface`); the worker records
 * the servers on `x.pipeshub.answer.finalized`, and the owner's transcript ends with main's `mcp_sign_in` part.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { RunSurface } from '../../../src/modules/agent-harness/domain/admission';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent, validateJournalAppend } from '../../../src/modules/agent-harness/domain/journal';
import { resolveRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { missingFor } from '../../../src/modules/agent-harness/domain/worker-compat';
import { sharedCompletion } from '../../../src/modules/agent-harness/projection/audiences';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildCompletion, buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { currentWorker } from './support/worker-capabilities';

const RUN = 'run-1';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const JIRA = { instanceId: 'inst-1', serverName: 'Jira MCP', scopes: ['write'] };

function journal(answer: Record<string, unknown>): JournalEvent[] {
  const specs: Array<[string, Record<string, unknown>, 'node' | 'worker']> = [
    ['run.admitted', { runKind: 'interactive', principalRef: 'p_user' }, 'node'],
    ['run.claimed', { attempt: 1, fence: 1, leaseMs: 60000 }, 'node'],
    ['x.pipeshub.answer.finalized', answer, 'worker'],
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
        ts: '2026-10-18T12:00:00.000Z',
        writer,
        visibility: 'owner',
        data,
      }) as JournalEvent,
  );
}

const answered = (extra: Record<string, unknown> = {}) => ({
  outcome: 'answered',
  answer: 'Jira needs more permission.',
  partTexts: ['Jira needs more permission.'],
  citations: [],
  ...extra,
});

const appended = (data: Record<string, unknown>) => () =>
  validateJournalAppend({
    orgId: 'org-1',
    sessionId: 'ses-1',
    streamId: 'ses-1',
    runId: RUN,
    writer: 'worker',
    events: [{ type: 'x.pipeshub.answer.finalized', schemaVersion: 1, critical: false, visibility: 'owner', data }],
  } as never);

describe('FU4: main MCP sign-in card on a durable run', () => {
  describe('the answer', () => {
    it('the worker may record the servers on the answer', () => {
      expect(appended(answered({ mcpSignIn: [JIRA, { ...JIRA, instanceId: 'inst-2', agentKey: 'agent-7' }] }))).to.not.throw();
      expect(appended(answered({ mcpSignIn: [] }))).to.throw();
      expect(appended(answered({ mcpSignIn: [{ instanceId: 'inst-1' }] }))).to.throw();
    });

    it("the owner's transcript ends with main's part, as AnswerFinalizer appends it", () => {
      const parts = buildParts(foldTimeline(journal(answered({ mcpSignIn: [JIRA] })), RUN));
      expect(parts[parts.length - 1]).to.deep.equal({ type: 'mcp_sign_in', servers: [JIRA] });
      expect(buildCompletion(foldTimeline(journal(answered({ mcpSignIn: [JIRA] })), RUN))?.['parts']).to.deep.include({
        type: 'mcp_sign_in',
        servers: [JIRA],
      });
    });

    it('no servers recorded: no part', () => {
      const parts = buildParts(foldTimeline(journal(answered()), RUN));
      expect(parts.map((part) => part['type'])).to.not.include('mcp_sign_in');
    });

    it('an audience other than the owner (Slack, MCP, a shared viewer) never gets the card', () => {
      const completion = buildCompletion(foldTimeline(journal(answered({ mcpSignIn: [JIRA] })), RUN));
      const seen = sharedCompletion(
        { ...completion, final: true },
        { citations: (c) => c, messages: (m) => m },
        { card: (c) => c, text: (t) => t, messages: (m) => m, completion: (c) => c },
      );
      expect((seen['parts'] as Array<Record<string, unknown>>).map((part) => part['type'])).to.deep.equal(['text']);
    });
  });

  describe('the spec', () => {
    const run = (features: string[]): HarnessRun =>
      ({
        runId: RUN,
        orgId: 'org-1',
        sessionId: 'ses-1',
        actorId: 'user-1',
        agentKey: 'agent-1',
        userMessageKey: 'm-1',
        idempotencyKey: 'k-1',
        payloadHash: 'h',
        harnessMajor: 2,
        profile: 'dispatched',
        status: 'queued',
        outcome: null,
        revision: 0,
        fence: 0,
        attemptId: null,
        workerId: null,
        leaseExpiresAt: null,
        absoluteDeadlineAt: Date.UTC(2026, 9, 18, 12),
        checkpoint: null,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
        createdAt: Date.UTC(2026, 9, 18, 11),
        updatedAt: Date.UTC(2026, 9, 18, 11),
        requires: { foldVersion: 1, features: features as never },
      }) as HarnessRun;
    const pinned = (features: string[], surface: RunSurface | null) =>
      resolveRunSpec(
        { run: run(features), agentDefinition: agentDefinition(), via: 'session', surface },
        DURABLE,
      ).extensions?.['x.pipeshub'] as Record<string, unknown>;

    it('pins the surface of a run that requires run_surface', () => {
      expect(pinned(['run_surface'], 'web')['surface']).to.equal('web');
      expect(pinned(['run_surface'], 'slack')['surface']).to.equal('slack');
    });

    it('old workers: a run admitted without the requirement (or with none recorded) carries no surface', () => {
      expect(pinned([], 'web')).to.not.have.property('surface');
      expect(pinned(['run_surface'], null)).to.not.have.property('surface');
    });

    it('old workers: one that does not read the surface never claims a run that carries it', () => {
      const old = currentWorker({ features: ['chat'] });
      expect(missingFor(run(['run_surface']), old)).to.include('feature:run_surface');
      expect(missingFor(run(['run_surface']), currentWorker())).to.deep.equal([]);
    });
  });

  describe('admission', () => {
    const admit = async (live: boolean, surface?: RunSurface, seen = true) => {
      const withheld: string[] = [];
      const store = new InMemoryExecutionStore({ now: () => 1_000 });
      const outcome = await new RunAdmissionService({
        store,
        rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
        clock: { now: () => 1_000 },
        agentAccess: grantingAccess((key) => agentDefinition({ agentKey: key })),
        workerFeatures: {
          // A live worker (`chat`) that does or does not advertise run_surface; `seen` false: no worker seen yet.
          live: async (feature) => (feature === 'run_surface' ? live : seen && feature === 'chat'),
          metrics: {
            withheld: (feature) => void withheld.push(feature),
            live: () => undefined,
            stranded: () => undefined,
          },
        },
      }).submit({
        orgId: 'org-1',
        actorId: 'user-1',
        agentKey: 'agent-1',
        authorization: 'Bearer t',
        idempotencyKey: `k-${Math.random()}`,
        deadlineMs: 60_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
        ...(surface !== undefined ? { surface } : {}),
      });
      if (outcome.route !== 'durable') expect.fail('expected a durable admission');
      const subject = await store.readClaimSubject('org-1', outcome.admission.run.runId);
      return { run: outcome.admission.run, surface: subject?.surface, withheld };
    };

    it('a live worker reads it: the run requires run_surface and keeps where it was started', async () => {
      const web = await admit(true);
      expect(web.run.requires?.features).to.include('run_surface');
      expect(web.surface).to.equal('web');
      expect((await admit(true, 'slack')).surface).to.equal('slack');
    });

    it('none live: admitted as before FU4, without the requirement, and counted', async () => {
      const admitted = await admit(false);
      expect(admitted.run.requires?.features).to.not.include('run_surface');
      expect(admitted.withheld).to.include('run_surface');
    });

    it('no worker seen yet: unknown, required (ADR-CAP-01 D2), not counted as withheld', async () => {
      const admitted = await admit(false, undefined, false);
      expect(admitted.run.requires?.features).to.include('run_surface');
      expect(admitted.withheld).to.not.include('run_surface');
    });
  });
});

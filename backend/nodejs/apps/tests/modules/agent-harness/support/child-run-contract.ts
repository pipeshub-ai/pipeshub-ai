/**
 * Child runs (P6.1, P6.3) on every store pair: the in-memory adapter
 * (`execution-store.contract.test.ts`) and MongoDB on a replica set
 * (`execution-store.mongo.itest.ts`), so admission, join and cancellation
 * cannot drift between them.
 */
import { expect } from 'chai';
import { ChildRunService } from '../../../../src/modules/agent-harness/application/child-run-service';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import {
  ChildGrantRequest,
  ChildResult,
  childRunIdFor,
} from '../../../../src/modules/agent-harness/domain/child-runs';
import { HarnessConflictError, HarnessUnavailableError } from '../../../../src/modules/agent-harness/domain/errors';
import {
  HarnessRun,
  StoreClock,
} from '../../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../../src/modules/agent-harness/domain/journal';
import { inlineContentRef } from '../../../../src/modules/agent-harness/domain/run-content';
import { readPinnedSpec } from '../../../../src/modules/agent-harness/domain/run-spec';
import { isRunKeyDigest } from '../../../../src/modules/agent-harness/domain/run-key-digest';
import { ChildRunStore } from '../../../../src/modules/agent-harness/ports/child-runs';
import {
  InMemoryWorkspaceKeyStore,
  InMemoryWorkspaceRepository,
  InMemoryWorkspaceWriterStore,
} from '../../../../src/modules/agent-harness/adapters/in-memory-workspace-store';
import { SessionWorkspaceKeys } from '../../../../src/modules/agent-harness/application/session-workspace-keys';
import { WorkspaceService } from '../../../../src/modules/agent-harness/application/workspace-service';
import {
  childForkId,
  sessionWorkspaceId,
} from '../../../../src/modules/agent-harness/domain/workspace';
import { BudgetLedgerStore } from '../../../../src/modules/agent-harness/ports/budget-ledger';
import { agentDefinition } from './agent-definition';
import type { AgentRunDefinition } from '../../../../src/modules/agent-harness/domain/agent-definition';
import { dataKeyRing, testCipher } from './data-protection';
import type { RunLists } from '../../../../src/modules/agent-harness/ports/run-lists';
import { compareNewestFirst } from '../../../../src/modules/agent-harness/domain/run-lists';
import {
  contractClock,
  ContractBackend,
  ContractHarness,
  ContractHooks,
  createContractHarness,
  revisionsOf,
} from './execution-store-contract';
import { HARNESS_ENABLED } from './rollout';
import { ChildPinning } from '../../../../src/modules/agent-harness/application/admission/child-pinning';
import { PolicyPinning } from '../../../../src/modules/agent-harness/application/admission/policy-pinning';
import { aPin, pinnerOf } from './a-pin';
import { ENGINE_HOLDER, engineOffService, heldEngineChild } from './engine-child';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const LEASE_MS = 5_000;

export const CHILD_RESULT: ChildResult = {
  resultRef: inlineContentRef(
    Buffer.from(JSON.stringify({ output: 'child answer', success: true })),
  ),
  labels: { integrity: 'internal' },
};

const conflictCode = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessConflictError);
    return (error as HarnessConflictError).code;
  }
  expect.fail('Expected a HarnessConflictError');
};

interface Family {
  h: ContractHarness;
  children: ChildRunStore;
  service: ChildRunService;
  /** The parent as its latest claim left it. */
  parent: HarnessRun;
}

export function describeChildRunContract(
  name: string,
  createBackend: (clock: StoreClock) => ContractBackend,
  hooks: ContractHooks = {},
): void {
  describe(`child runs (P6.1): ${name}`, function () {
    this.timeout(30_000);
    let f: Family;

    if (hooks.before) before(hooks.before);
    if (hooks.after) after(hooks.after);

    const family = async (
      maxChildren = 2,
      roleModels?: AgentRunDefinition['roleModels'],
    ): Promise<Family> => {
      const clock = contractClock();
      const backend = createBackend(clock);
      await backend.reset?.();
      const h = createContractHarness(backend, clock);
      const children = backend.childRuns;
      if (children === undefined) expect.fail('backend has no child store');
      h.setRollout(V2);
      h.setAgentDefinition((agentKey) =>
        agentDefinition({ agentKey, maxChildren, ...(roleModels !== undefined ? { roleModels } : {}) }),
      );
      const { run } = await h.admit({
        agentKey: 'agent-1',
        authorization: 'Bearer user',
      });
      const parent = await h.claim(run, 'worker-p', LEASE_MS);
      const service = new ChildRunService({
        runs: backend.executionStore,
        children,
        journals: new JournalService(backend.payloads, testCipher()),
      });
      return { h, children, service, parent };
    };

    describe('a pinned child (CPD-6, PIN-28)', () => {
      const PARENT_PIN = aPin(3_600);
      const CHILD_PIN = aPin(1_200);
      const SILENT = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };
      /** Written between a child's pin and its admission, once per entry: a control-plane write. */
      let afterChildPin: Array<(backend: ContractBackend, orgId: string) => Promise<void>> = [];
      let childPins = 0;

      /** A parent admitted with its pin and claimed, and a child service that pins children; `live`: a worker reads pins. */
      const pinnedParent = async (live: boolean) => {
        const clock = contractClock();
        const backend = createBackend(clock);
        await backend.reset?.();
        childPins = 0;
        const pinner = pinnerOf(
          async (subject, basis) => {
            if (subject.parent === undefined) return PARENT_PIN;
            childPins += 1;
            await afterChildPin.shift()?.(backend, subject.orgId);
            return aPin(1_200, basis.headRevision);
          },
          (orgId) => revisionsOf(backend).heads(orgId),
        );
        const h = createContractHarness(backend, clock);
        const children = backend.childRuns;
        if (children === undefined) expect.fail('backend has no child store');
        h.setRollout(V2);
        h.setAgentDefinition((agentKey) => agentDefinition({ agentKey, maxChildren: 2 }));
        // A live worker (`chat`) that does or does not read pins (ADR-CAP-01 D2).
        const workerFeatures = { live: async (feature: string) => live || feature === 'chat', metrics: { withheld: () => undefined } };
        h.setPolicySteps([
          new PolicyPinning({ mode: 'on', pinner, metrics: { mismatch: () => undefined }, logger: SILENT, workerFeatures }),
        ]);
        const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
        const parent = await h.claim(run, 'worker-p', LEASE_MS);
        const service = new ChildRunService({
          runs: backend.executionStore,
          children,
          journals: new JournalService(backend.payloads, testCipher()),
          pinning: new ChildPinning({ mode: 'on', pinner, metrics: { mismatch: () => undefined }, logger: SILENT, workerFeatures }),
        });
        const spawn = () =>
          service.spawn(
            { orgId: parent.orgId, runId: parent.runId, workerId: parent.workerId as string, attemptId: parent.attemptId as string, fence: parent.fence },
            { invocationId: 't1.0', goal: 'look into it', grants: {} },
          );
        return { backend, parent, spawn };
      };

      /** The same, with its child spawned. */
      const pinnedFamily = async (live: boolean) => {
        const { backend, parent, spawn } = await pinnedParent(live);
        const { childRunId } = await spawn();
        const child = await backend.executionStore.readClaimSubject(parent.orgId, childRunId);
        const pinOf = (spec: unknown): { digest?: string } | undefined =>
          (spec as { extensions?: Record<string, { resolved?: { digest?: string } }> } | undefined)?.extensions?.['x.pipeshub']?.resolved;
        return { child, inSpec: pinOf(child?.pinned?.spec), backend, parent };
      };
      const aiModelSave = (backend: ContractBackend) => revisionsOf(backend).moveDeployment();
      const orgWrite = (backend: ContractBackend, orgId: string) => revisionsOf(backend).commit(orgId);

      afterEach(() => {
        afterChildPin = [];
      });

      for (const [what, write] of [
        ['an AI-model save (the deployment revision)', aiModelSave],
        ['a control-plane write of the org (its head)', orgWrite],
      ] as const) {
        it(`CPD10-41: ${what} commits between a child's pin and its admission: pinned again once, and admitted at the new revisions`, async () => {
          afterChildPin = [write];
          const { child, backend, parent } = await pinnedFamily(true);
          expect(childPins).to.equal(2);
          const at = await revisionsOf(backend).heads(parent.orgId);
          expect(child?.resolved).to.deep.equal(aPin(1_200, at.org));
          expect((await backend.executionStore.get(parent.orgId, parent.runId))?.children?.spawned).to.equal(1);
        });
      }

      it('CPD10-41: revisions that keep moving refuse the spawn as unavailable; no child is written and the parent counts none', async () => {
        afterChildPin = [aiModelSave, aiModelSave];
        const { backend, parent, spawn } = await pinnedParent(true);
        const refused = await spawn().catch((error: unknown) => error);
        expect(refused).to.be.instanceOf(HarnessUnavailableError);
        expect(childPins).to.equal(2);
        expect(await backend.executionStore.get(parent.orgId, childRunIdFor(parent.orgId, parent.runId, 't1.0'))).to.equal(null);
        expect((await backend.executionStore.get(parent.orgId, parent.runId))?.children?.spawned ?? 0).to.equal(0);
      });

      it('a worker reads pins: the child requires resolved_config and its spec carries its own pin, not its parent’s', async () => {
        const { child, inSpec } = await pinnedFamily(true);
        expect(child?.run.requires?.features).to.include('resolved_config');
        expect(inSpec?.digest).to.equal(CHILD_PIN.digest);
        expect(child?.resolved?.digest).to.equal(CHILD_PIN.digest);
      });

      it('no worker reads pins: the child requires nothing new, its spec carries no pin, and its pin stays on its run', async () => {
        const { child, inSpec } = await pinnedFamily(false);
        expect(child?.run.requires?.features).to.not.include('resolved_config');
        expect(inSpec).to.equal(undefined);
        expect(child?.resolved?.digest).to.equal(CHILD_PIN.digest);
      });
    });

    const spawn = (
      invocationId: string,
      grants: ChildGrantRequest = {},
      parent: HarnessRun = f.parent,
      goal = 'look into it',
    ) =>
      f.service.spawn(
        {
          orgId: parent.orgId,
          runId: parent.runId,
          workerId: parent.workerId as string,
          attemptId: parent.attemptId as string,
          fence: parent.fence,
        },
        { invocationId, goal, grants },
      );

    const run = async (runId: string): Promise<HarnessRun> => {
      const found = await f.h.service.get(f.parent.orgId, runId);
      if (found === null) expect.fail(`run ${runId} not found`);
      return found;
    };

    const stream = (streamId: string): Promise<JournalEvent[]> =>
      f.h.backend.journal.readStream(f.parent.orgId, streamId, 0, 500);

    const types = async (streamId: string): Promise<string[]> =>
      (await stream(streamId)).map((event) => event.type);

    const commitParent = (
      commandId: string,
      transition: Record<string, unknown>,
      parent: HarnessRun = f.parent,
      family: Family = f,
    ) =>
      family.h.service.commit({
        commandId,
        orgId: parent.orgId,
        runId: parent.runId,
        workerId: parent.workerId as string,
        attemptId: parent.attemptId as string,
        fence: parent.fence,
        events: [],
        legacyEvents: [],
        transition: transition as never,
        outbox: [],
      });

    const suspendJoin = (commandId = 'join-1') =>
      commitParent(commandId, {
        status: 'paused',
        detail: { kind: 'suspend', reason: 'join' },
      });

    const finishChild = async (
      childRunId: string,
      transition: Record<string, unknown> = {
        status: 'completed',
        outcome: 'answer',
        childResult: CHILD_RESULT,
      },
    ) => {
      const claimed = await f.h.claim(await run(childRunId), 'worker-c', LEASE_MS);
      return f.h.service.commit({
        commandId: `end-${childRunId}`,
        orgId: claimed.orgId,
        runId: claimed.runId,
        workerId: 'worker-c',
        attemptId: claimed.attemptId as string,
        fence: claimed.fence,
        events: [],
        legacyEvents: [],
        transition: transition as never,
        outbox: [],
      });
    };

    describe('admission', () => {
      beforeEach(async () => {
        f = await family();
      });

      it("UIB1-04 (ADR-UI-01 D1): a job lists as its person's, under `kind: job`, naming its parent; the parent under `kind: root`", async () => {
        const spawned = await spawn('t1.uib1', { tools: ['jira@inst-42/search_issues'] });
        const lists = f.h.backend.executionStore as unknown as RunLists;
        const ids = async (kind: 'job' | 'root') =>
          (await lists.personRuns({ orgId: f.parent.orgId, actorId: f.parent.actorId, kind, limit: 10 })).map(
            ({ run: listed, engine }) => [listed.runId, listed.parent?.runId ?? null, engine],
          );
        expect(await ids('job')).to.deep.equal([[spawned.childRunId, f.parent.runId, null]]);
        expect(await ids('root')).to.deep.equal([[f.parent.runId, null, null]]);
      });

      // Another org's call is refused by the store's org binding on PostgreSQL and filtered on the others (QA-26).
      it("UIB5-S1 (ADR-UI-01 D5, D6): a conversation's jobs, newest first, with their parent; never its root runs or another conversation's", async () => {
        const first = await spawn('t1.uib5a', { tools: ['jira@inst-42/search_issues'] }, f.parent, 'Fix the failing test');
        const second = await spawn('t1.uib5b', { tools: ['jira@inst-42/search_issues'] }, f.parent, 'Then the refund path');
        const lists = f.h.backend.executionStore as unknown as RunLists;
        const jobs = await lists.sessionJobs(f.parent.orgId, f.parent.sessionId, 10);
        const stored = [await run(first.childRunId), await run(second.childRunId)].sort(compareNewestFirst);
        expect(jobs.map(({ run: job, engine, continuesRunId }) => [job.runId, job.parent?.runId, engine, continuesRunId])).to.deep.equal(
          stored.map((job) => [job.runId, f.parent.runId, null, null]),
        );
        expect((await lists.sessionJobs(f.parent.orgId, f.parent.sessionId, 1)).map(({ run: job }) => job.runId)).to.deep.equal([
          stored[0]?.runId,
        ]);
        expect(await lists.sessionJobs(f.parent.orgId, 'ses-uib5-none', 10)).to.deep.equal([]);
      });

      it('CA-1: admits the child, its pinned spec, both streams and the wake in one transaction', async () => {
        const spawned = await spawn('t1.0', {
          tools: ['jira@inst-42/search_issues'],
        });
        expect(spawned.created).to.equal(true);
        const child = await run(spawned.childRunId);
        expect(spawned.childRunId).to.equal(
          childRunIdFor(f.parent.orgId, f.parent.runId, 't1.0'),
        );
        expect(child.status).to.equal('queued');
        expect(child.profile).to.equal('dispatched');
        expect(child.sessionId).to.equal(f.parent.sessionId);
        expect(child.parent).to.deep.equal({
          runId: f.parent.runId,
          invocationId: 't1.0',
          depth: 1,
        });
        expect(child.absoluteDeadlineAt).to.equal(f.parent.absoluteDeadlineAt);
        expect(child.requires?.features).to.include('child_runs');
        expect(child.input?.query).to.equal('look into it');

        const childStream = await stream(spawned.childRunId);
        expect(childStream.map((e) => e.type)).to.deep.equal([
          'run.admitted',
          'run.spec_pinned',
        ]);
        expect(childStream[0]?.data).to.include({
          runKind: 'child',
          parentRunId: f.parent.runId,
        });
        const pinned = readPinnedSpec(childStream[1]?.data?.['spec']);
        expect(pinned.runKind).to.equal('child');
        expect(pinned.grants.tools.map((tool) => tool.toolId)).to.deep.equal([
          'jira@inst-42/search_issues',
        ]);
        expect(pinned.budgets.maxChildren).to.equal(0);

        const spawnedEvent = (await stream(f.parent.sessionId)).find(
          (event) => event.type === 'child.spawned',
        );
        expect(spawnedEvent?.runId).to.equal(f.parent.runId);
        expect(spawnedEvent?.data).to.include({
          childRunId: spawned.childRunId,
          invocationId: 't1.0',
        });
        expect(String(spawnedEvent?.data?.['grantDigest'])).to.match(
          /^sha256:[0-9a-f]{64}$/,
        );
        expect(
          await f.h.backend.dispatches(f.parent.orgId, spawned.childRunId),
        ).to.deep.equal([
          { commandId: `child:${spawned.childRunId}`, kind: 'run_ready' },
        ]);
        const parent = await run(f.parent.runId);
        expect(parent.children).to.deep.equal({ spawned: 1, open: 1 });
        expect(parent.requires?.features).to.include('child_runs');
      });

      it('CR-M1 (K-L): a sub-agent child pins its definition and token share and needs deep_mode', async () => {
        const subAgent = { role: 'jira', domainAgent: 'jira_agent', toolNames: ['jira_agent'] };
        const spawned = await f.service.spawn(
          {
            orgId: f.parent.orgId,
            runId: f.parent.runId,
            workerId: f.parent.workerId as string,
            attemptId: f.parent.attemptId as string,
            fence: f.parent.fence,
          },
          { invocationId: 't1.0', goal: 'find it', grants: { maxTokens: 250_000 }, subAgent },
        );
        const child = await run(spawned.childRunId);
        expect(child.requires?.features).to.include.members(['child_runs', 'deep_mode']);
        const pinned = readPinnedSpec((await stream(spawned.childRunId))[1]?.data?.['spec']);
        expect(pinned.extensions?.['x.pipeshub']?.['subAgent']).to.deep.equal(subAgent);
        expect(pinned.budgets.maxTokens).to.equal(250_000);
        const claimed = await f.h.claim(child, 'worker-c', LEASE_MS);
        expect(claimed.status).to.equal('running');
      });

      it("ANSWER-CONTRACT-FU-3: a job of a person's run pins that its parent can ask and needs child_asks", async () => {
        const spawned = await spawn('t1.fu3');
        const child = await run(spawned.childRunId);
        expect(child.requires?.features).to.include('child_asks');
        const pinned = readPinnedSpec((await stream(spawned.childRunId))[1]?.data?.['spec']);
        expect(pinned.extensions?.['x.pipeshub']?.['parentAsks']).to.equal(true);
      });

      it('ANSWER-CONTRACT-FU-3: while no live worker reads the pin, the job pins nothing and needs nothing new', async () => {
        const withheld: string[] = [];
        const service = new ChildRunService({
          runs: f.h.backend.executionStore,
          children: f.children,
          journals: new JournalService(f.h.backend.payloads, testCipher()),
          workerFeatures: { live: async () => false, metrics: { live: () => undefined, withheld: (feature) => { withheld.push(feature); }, stranded: () => undefined } },
        });
        const spawned = await service.spawn(
          {
            orgId: f.parent.orgId,
            runId: f.parent.runId,
            workerId: f.parent.workerId as string,
            attemptId: f.parent.attemptId as string,
            fence: f.parent.fence,
          },
          { invocationId: 't1.fu3w', goal: 'look into it', grants: {} },
        );
        expect((await run(spawned.childRunId)).requires?.features).to.not.include('child_asks');
        const pinned = readPinnedSpec((await stream(spawned.childRunId))[1]?.data?.['spec']);
        expect(pinned.extensions?.['x.pipeshub']).to.not.have.property('parentAsks');
        expect(withheld).to.deep.equal(['child_asks']);
      });

      it('QA37-K8 (MA1 D5): a child its role set the model of, its parent and the session need child_models; an inherited one does not', async () => {
        const roles = await family(3, { researcher: { modelKey: 'model-2', modelName: 'claude-x', provider: 'anthropic' } });
        const attempt = {
          orgId: roles.parent.orgId,
          runId: roles.parent.runId,
          workerId: roles.parent.workerId as string,
          attemptId: roles.parent.attemptId as string,
          fence: roles.parent.fence,
        };
        const inherited = await roles.service.spawn(attempt, {
          invocationId: 't1.0', goal: 'write it', grants: {}, subAgent: { role: 'writer', toolNames: [] },
        });
        const features = async (runId: string) =>
          (await roles.h.backend.executionStore.get(roles.parent.orgId, runId))?.requires?.features ?? [];
        expect(await features(inherited.childRunId)).to.not.include('child_models');
        expect(await features(roles.parent.runId)).to.not.include('child_models');
        const roleSet = await roles.service.spawn(attempt, {
          invocationId: 't1.1', goal: 'look it up', grants: {}, subAgent: { role: 'researcher', toolNames: [] },
        });
        expect(await features(roleSet.childRunId)).to.include('child_models');
        expect(await features(roles.parent.runId)).to.include('child_models');
        const sibling = await roles.service.spawn(attempt, {
          invocationId: 't1.2', goal: 'write more', grants: {}, subAgent: { role: 'writer', toolNames: [] },
        });
        // Read from the session once the role-set spawn is on its stream, whatever the child itself names.
        expect(await features(sibling.childRunId)).to.include('child_models');
        await commitParent('fail-parent', { status: 'failed', errorType: 'model_error', detail: { kind: 'fail' } }, roles.parent, roles);
        const next = await roles.h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', sessionId: roles.parent.sessionId });
        expect(next.run.runId).to.not.equal(roles.parent.runId);
        expect(next.run.requires?.features).to.include.members(['child_runs', 'child_models']);
      });

      it('CA-1: a later root run of the session inherits child_runs (session feature)', async () => {
        await spawn('t1.0');
        await commitParent('fail-parent', {
          status: 'failed',
          errorType: 'model_error',
          detail: { kind: 'fail' },
        });
        const next = await f.h.admit({
          agentKey: 'agent-1',
          authorization: 'Bearer user',
          sessionId: f.parent.sessionId,
        });
        expect(next.run.requires?.features).to.include('child_runs');
      });

      it('CA-2: the same invocation replays its child and writes nothing; another request conflicts', async () => {
        const first = await spawn('t1.0');
        const childHead = await types(first.childRunId);
        const parentHead = await types(f.parent.sessionId);
        const replay = await spawn('t1.0');
        expect(replay).to.deep.equal({
          childRunId: first.childRunId,
          created: false,
        });
        expect(await types(first.childRunId)).to.deep.equal(childHead);
        expect(await types(f.parent.sessionId)).to.deep.equal(parentHead);
        expect(
          await f.h.backend.dispatches(f.parent.orgId, first.childRunId),
        ).to.have.length(1);
        expect((await run(f.parent.runId)).children).to.deep.equal({
          spawned: 1,
          open: 1,
        });
        expect(
          await conflictCode(spawn('t1.0', {}, f.parent, 'another goal')),
        ).to.equal('IDEMPOTENCY_CONFLICT');
      });

      it('QA15F-2: the child stores a digest of its derived key, never the key, and a replay is still the same child', async () => {
        const first = await spawn('t1.0');
        const stored = (await run(first.childRunId)).idempotencyKey;
        expect(isRunKeyDigest(stored), stored).to.equal(true);
        expect(stored).to.not.include(f.parent.runId);
        expect(await spawn('t1.0')).to.deep.equal({ childRunId: first.childRunId, created: false });
        const second = await spawn('t2.0');
        expect((await run(second.childRunId)).idempotencyKey).to.not.equal(stored);
      });

      it('CA-3: maxChildren bounds new children, never a replay', async () => {
        await spawn('t1.0');
        await spawn('t1.1');
        expect(await conflictCode(spawn('t1.2'))).to.equal(
          'CHILD_LIMIT_REACHED',
        );
        expect((await spawn('t1.1')).created).to.equal(false);
        expect((await run(f.parent.runId)).children?.spawned).to.equal(2);
      });

      it('CA-3: a run granted no children is refused', async () => {
        f = await family(0);
        expect(await conflictCode(spawn('t1.0'))).to.equal(
          'CHILDREN_NOT_GRANTED',
        );
        expect(await types(f.parent.sessionId)).to.not.include(
          'child.spawned',
        );
      });

      it('CA-4: a stale attempt admits nothing', async () => {
        const stale = { ...f.parent, fence: f.parent.fence - 1 };
        expect(await conflictCode(spawn('t1.0', {}, stale))).to.equal(
          'STALE_ATTEMPT',
        );
        const expired = f.parent;
        f.h.setNow(expired.leaseExpiresAt as number);
        expect(await conflictCode(spawn('t1.0', {}, expired))).to.equal(
          'STALE_ATTEMPT',
        );
        expect(
          await f.h.service.get(
            f.parent.orgId,
            childRunIdFor(f.parent.orgId, f.parent.runId, 't1.0'),
          ),
        ).to.equal(null);
      });

      it('CA-5: children are not root runs: own stream, own claim, the session keeps its root', async () => {
        const one = await spawn('t1.0');
        const two = await spawn('t1.1');
        expect(
          (await f.h.service.getActive(f.parent.orgId, f.parent.sessionId))
            ?.runId,
        ).to.equal(f.parent.runId);
        const claimed = await f.h.service.claim({
          orgId: f.parent.orgId,
          runId: one.childRunId,
          workerId: 'worker-c',
          attemptId: 'attempt-c',
          leaseDurationMs: LEASE_MS,
        });
        expect(Object.keys(claimed.journal.lastSeq)).to.deep.equal([
          one.childRunId,
        ]);
        expect(claimed.pinned?.spec.runKind).to.equal('child');
        expect(await types(one.childRunId)).to.deep.equal([
          'run.admitted',
          'run.spec_pinned',
          'run.claimed',
        ]);
        expect((await run(two.childRunId)).status).to.equal('queued');
      });

      it('CA-6: a widening request is refused and writes nothing', async () => {
        expect(
          await conflictCode(spawn('t1.0', { tools: ['slack@inst-1/post'] })),
        ).to.equal('CHILD_GRANT_TOO_LARGE');
        expect(
          await conflictCode(spawn('t1.1', { sandboxProfile: 'agent-code' })),
        ).to.equal('CHILD_GRANT_TOO_LARGE');
        expect(await types(f.parent.sessionId)).to.not.include(
          'child.spawned',
        );
        expect((await run(f.parent.runId)).children).to.equal(undefined);
      });
    });

    describe('join', () => {
      beforeEach(async () => {
        f = await family();
      });

      it('CJ-1: the last child ending wakes a joining parent with its result', async () => {
        const { childRunId } = await spawn('t1.0');
        await suspendJoin();
        expect((await run(f.parent.runId)).status).to.equal('paused');
        await finishChild(childRunId);
        const parent = await run(f.parent.runId);
        expect(parent.status).to.equal('queued');
        expect(parent.children).to.deep.equal({ spawned: 1, open: 0 });
        const tail = (await stream(f.parent.sessionId)).slice(-2);
        expect(tail.map((e) => e.type)).to.deep.equal([
          'child.completed',
          'run.resumed',
        ]);
        expect(tail[0]?.data).to.deep.equal({
          childRunId,
          resultRef: CHILD_RESULT.resultRef,
          labels: CHILD_RESULT.labels,
          outcome: 'answered',
        });
        expect(tail[1]?.data).to.deep.equal({ cause: 'child_done' });
        expect(
          (await f.h.backend.dispatches(f.parent.orgId, f.parent.runId)).map(
            (d) => d.commandId,
          ),
        ).to.include(`join:${f.parent.runId}:${childRunId}`);
      });

      it('CJ-2: with two children only the second wakes the parent', async () => {
        const one = await spawn('t1.0');
        const two = await spawn('t1.1');
        await suspendJoin();
        await finishChild(one.childRunId);
        expect((await run(f.parent.runId)).status).to.equal('paused');
        await finishChild(two.childRunId);
        expect((await run(f.parent.runId)).status).to.equal('queued');
        const kinds = await types(f.parent.sessionId);
        expect(kinds.filter((t) => t === 'child.completed')).to.have.length(2);
        expect(kinds.filter((t) => t === 'run.resumed')).to.have.length(1);
      });

      it('CJ-3: a child ending before the parent joins: no wake, and the join is refused JOIN_READY', async () => {
        const { childRunId } = await spawn('t1.0');
        await finishChild(childRunId);
        const parent = await run(f.parent.runId);
        expect(parent.status).to.equal('running');
        expect(await types(f.parent.sessionId)).to.include('child.completed');
        expect(await conflictCode(suspendJoin())).to.equal('JOIN_READY');
        expect((await run(f.parent.runId)).status).to.equal('running');
      });

      it('CJ-4: a failed child joins with Node failure result', async () => {
        const { childRunId } = await spawn('t1.0');
        await suspendJoin();
        await finishChild(childRunId, {
          status: 'failed',
          errorType: 'model_error',
          detail: { kind: 'fail' },
        });
        const completed = (await stream(f.parent.sessionId)).find(
          (e) => e.type === 'child.completed',
        );
        expect(completed?.data).to.include({
          childRunId,
          outcome: 'failed',
          errorType: 'model_error',
        });
        const ref = completed?.data?.['resultRef'] as { ref: string };
        expect(
          JSON.parse(
            Buffer.from(ref.ref.slice('inline:'.length), 'base64url').toString(),
          ),
        ).to.deep.equal({ error: 'model_error', output: '', success: false });
        expect((await run(f.parent.runId)).status).to.equal('queued');
      });

      it('CR-M1 (K-L): a quarantined child joins with Node failure result and wakes its parent', async () => {
        const { childRunId } = await spawn('t1.0');
        await suspendJoin();
        await finishChild(childRunId, {
          status: 'failed',
          errorType: 'crash_loop',
          detail: { kind: 'quarantine', attempts: 4, lastErrorType: 'crash_loop' },
        });
        expect((await run(childRunId)).status).to.equal('failed');
        const completed = (await stream(f.parent.sessionId)).find(
          (e) => e.type === 'child.completed',
        );
        expect(completed?.data).to.include({ childRunId, outcome: 'failed' });
        expect(await types(childRunId)).to.include('run.quarantined');
        expect((await run(f.parent.runId)).status).to.equal('queued');
      });

      it('CJ-4: an idle child cancelled by control joins as cancelled', async () => {
        const { childRunId } = await spawn('t1.0');
        await suspendJoin();
        const child = await run(childRunId);
        await f.h.service.control({
          commandId: 'cancel-child',
          dispatchCommandId: 'cancel-child-dispatch',
          orgId: child.orgId,
          runId: childRunId,
          expectedRevision: child.revision,
          action: 'cancel',
        });
        expect((await run(childRunId)).status).to.equal('cancelled');
        const completed = (await stream(f.parent.sessionId)).find(
          (e) => e.type === 'child.completed',
        );
        expect(completed?.data).to.include({
          outcome: 'cancelled',
          errorType: 'cancelled',
        });
        expect((await run(f.parent.runId)).status).to.equal('queued');
      });

      it('CJ-4: a child the sweeper cancels after a lapsed cancel joins as cancelled', async () => {
        const { childRunId } = await spawn('t1.0');
        await suspendJoin();
        const claimed = await f.h.claim(await run(childRunId), 'worker-c', LEASE_MS);
        await f.h.service.control({
          commandId: 'cancel-running-child',
          dispatchCommandId: 'cancel-running-child-dispatch',
          orgId: claimed.orgId,
          runId: childRunId,
          expectedRevision: claimed.revision,
          action: 'cancel',
        });
        expect((await run(childRunId)).status).to.equal('cancelling');
        f.h.setNow(claimed.leaseExpiresAt as number);
        await f.h.service.sweep(100);
        expect((await run(childRunId)).status).to.equal('cancelled');
        expect(await types(f.parent.sessionId)).to.include.members([
          'child.completed',
          'run.resumed',
        ]);
        expect((await run(f.parent.runId)).status).to.equal('queued');
      });

      it('CJ-4: a child failed by the store (failRun) joins as failed', async () => {
        const { childRunId } = await spawn('t1.0');
        await suspendJoin();
        expect(
          await f.h.backend.executionStore.failRun(
            f.parent.orgId,
            childRunId,
            'journal_payload_missing',
            [],
          ),
        ).to.equal(true);
        const completed = (await stream(f.parent.sessionId)).find(
          (e) => e.type === 'child.completed',
        );
        expect(completed?.data).to.include({
          outcome: 'failed',
          errorType: 'journal_payload_missing',
        });
        expect((await run(f.parent.runId)).status).to.equal('queued');
      });

      it('CJ-5: a child never writes the conversation', async () => {
        const { childRunId } = await spawn('t1.0');
        await finishChild(childRunId);
        const probe = f.h.backend.transcripts;
        if (probe !== undefined) {
          expect(await probe.written(await run(childRunId))).to.deep.equal([]);
          expect(await probe.written(f.parent)).to.deep.equal([]);
        }
      });

      it('CJ-6: a child ending after its parent ended appends nothing to the parent', async () => {
        const { childRunId } = await spawn('t1.0');
        await commitParent('parent-fails', {
          status: 'failed',
          errorType: 'model_error',
          detail: { kind: 'fail' },
        });
        const before = await types(f.parent.sessionId);
        await finishChild(childRunId);
        expect(await types(f.parent.sessionId)).to.deep.equal(before);
      });

      it('CJ-7: a child completes only with its result; a root run never names one', async () => {
        const { childRunId } = await spawn('t1.0');
        expect(
          await conflictCode(
            finishChild(childRunId, { status: 'completed', outcome: 'answer' }),
          ),
        ).to.equal('INVALID_COMMIT');
        expect(
          await conflictCode(
            commitParent('root-with-result', {
              status: 'completed',
              outcome: 'answer',
              childResult: CHILD_RESULT,
            }),
          ),
        ).to.equal('INVALID_COMMIT');
      });
    });

    describe('cancellation', () => {
      beforeEach(async () => {
        f = await family();
      });

      it('CC-1: cancelling the parent cancels its children, once', async () => {
        const idle = await spawn('t1.0');
        const busy = await spawn('t1.1');
        await f.h.claim(await run(busy.childRunId), 'worker-c', LEASE_MS);
        await suspendJoin();
        const parent = await run(f.parent.runId);
        const cancel = {
          commandId: 'cancel-parent',
          dispatchCommandId: 'cancel-parent-dispatch',
          orgId: parent.orgId,
          runId: parent.runId,
          expectedRevision: parent.revision,
          action: 'cancel' as const,
        };
        await f.h.service.control(cancel);
        expect((await run(f.parent.runId)).status).to.equal('cancelled');
        expect((await run(idle.childRunId)).status).to.equal('cancelled');
        expect((await run(busy.childRunId)).status).to.equal('cancelling');
        expect(
          (await f.h.backend.dispatches(f.parent.orgId, busy.childRunId)).map(
            (d) => d.kind,
          ),
        ).to.include('run_cancel');
        const before = await types(f.parent.sessionId);
        await f.h.service.control(cancel);
        expect(await types(f.parent.sessionId)).to.deep.equal(before);
        expect(
          await f.children.openChildren(f.parent.orgId, f.parent.runId),
        ).to.have.length(1);
      });

      it('CC-2: the sweep cancels a child whose parent ended without it', async () => {
        const { childRunId } = await spawn('t1.0');
        await commitParent('parent-fails', {
          status: 'failed',
          errorType: 'model_error',
          detail: { kind: 'fail' },
        });
        const { run: otherRun } = await f.h.admit({
          agentKey: 'agent-1',
          authorization: 'Bearer user',
        });
        const otherParent = await f.h.claim(otherRun, 'worker-q', LEASE_MS);
        const living = await spawn('t1.0', {}, otherParent);
        expect(
          (await f.children.orphanedChildren(10)).map((r) => r.runId),
        ).to.deep.equal([childRunId]);
        await f.h.service.sweep(100);
        expect((await run(childRunId)).status).to.equal('cancelled');
        expect((await run(living.childRunId)).status).to.equal('queued');
      });
    });

    describe('workspace forks (P6.4)', () => {
      let workspaces: WorkspaceService;
      let forking: ChildRunService;

      /** Admission reads the parent's pin with `/workspace` mounted (the pin Node writes today mounts nothing). */
      const mounting = (store: ContractBackend['executionStore']) =>
        new Proxy(store, {
          get: (target, property) => {
            if (property !== 'readClaimSubject') {
              const value = (target as unknown as Record<PropertyKey, unknown>)[
                property
              ];
              return typeof value === 'function' ? value.bind(target) : value;
            }
            return async (orgId: string, runId: string) => {
              const subject = await target.readClaimSubject(orgId, runId);
              if (subject?.pinned == null) return subject;
              const spec = structuredClone(subject.pinned.spec);
              const extension = spec.extensions?.['x.pipeshub'] as
                | { mounts: string[] }
                | undefined;
              if (extension !== undefined && !extension.mounts.includes('/workspace')) {
                extension.mounts = [...extension.mounts, '/workspace'];
              }
              return { ...subject, pinned: { ...subject.pinned, spec } };
            };
          },
        });

      const attemptOf = (r: HarnessRun) => ({
        orgId: r.orgId,
        runId: r.runId,
        workerId: r.workerId as string,
        attemptId: r.attemptId as string,
        fence: r.fence,
      });

      const forkSpawn = (
        invocationId: string,
        grants: ChildGrantRequest = {},
        parent: HarnessRun = f.parent,
      ) =>
        forking.spawn(attemptOf(parent), {
          invocationId,
          goal: 'edit it',
          grants,
        });

      const pinOf = async (childRunId: string) => {
        const subject = await f.h.backend.executionStore.readClaimSubject(
          f.parent.orgId,
          childRunId,
        );
        const pinned = subject?.pinned;
        if (pinned == null) expect.fail('the child has no pin');
        return readPinnedSpec(pinned.spec).extensions?.['x.pipeshub'] as
          | {
              mounts: string[];
              workspace?: {
                workspaceId: string;
                forkOf: { workspaceId: string; revision: number };
              };
            }
          | undefined;
      };

      const write = (r: HarnessRun, workspaceId: string, text: string) =>
        workspaces
          .head(attemptOf(r), workspaceId)
          .then((head) =>
            workspaces.commit(attemptOf(r), workspaceId, {
              baseRevision: head.revision,
              reason: 'autosave',
              changes: {
                upserts: [
                  {
                    path: `${text}.txt`,
                    objectId: `obj-${text}`,
                    contentHash: `hmac-sha256:${text}`,
                    sizeBytes: text.length,
                    mimeType: 'text/plain',
                  },
                ],
                deletes: [],
              },
            }),
          );

      beforeEach(async () => {
        f = await family(4);
        const cipher = dataKeyRing().cipher;
        workspaces = new WorkspaceService({
          runs: f.h.backend.executionStore,
          repository: new InMemoryWorkspaceRepository(),
          cipher,
          keys: new SessionWorkspaceKeys({ store: new InMemoryWorkspaceKeyStore(), cipher }),
          writers: new InMemoryWorkspaceWriterStore(),
        });
        forking = new ChildRunService({
          runs: mounting(f.h.backend.executionStore),
          children: f.children,
          journals: new JournalService(f.h.backend.payloads, testCipher()),
          workspaces,
        });
      });

      it('CF-1: a child granted /workspace gets a fork of its parent’s at the head, pinned, and both need workspace_forks', async () => {
        const root = sessionWorkspaceId(f.parent.sessionId);
        await write(f.parent, root, 'seed');
        const { childRunId } = await forkSpawn('t1.0');
        const pin = await pinOf(childRunId);
        expect(pin?.workspace).to.deep.equal({
          workspaceId: childForkId(f.parent.sessionId, childRunId),
          forkOf: { workspaceId: root, revision: 1 },
        });
        const fork = await workspaces.head(attemptOf(f.parent), pin?.workspace?.workspaceId ?? '');
        expect(fork.files.map((file) => file.path)).to.deep.equal(['seed.txt']);
        expect((await run(childRunId)).requires?.features).to.include('workspace_forks');
        expect((await run(f.parent.runId)).requires?.features).to.include.members([
          'child_runs',
          'workspace_forks',
        ]);
      });

      it('CF-2: a child that names no /workspace gets no fork, no pin and no feature', async () => {
        const { childRunId } = await forkSpawn('t1.0', { mounts: [] });
        expect((await pinOf(childRunId))?.workspace).to.equal(undefined);
        expect((await run(childRunId)).requires?.features).to.not.include('workspace_forks');
        expect(
          (await workspaces.head(attemptOf(f.parent), childForkId(f.parent.sessionId, childRunId))).revision,
        ).to.equal(0);
        expect((await run(f.parent.runId)).requires?.features).to.not.include('workspace_forks');
      });

      it('CF-3: without /workspace on the parent the request is refused and nothing is forked', async () => {
        const { childRunId } = await spawn('t1.0');
        expect((await pinOf(childRunId))?.workspace).to.equal(undefined);
        expect(await conflictCode(spawn('t2.0', { mounts: ['/workspace'] }))).to.equal(
          'CHILD_GRANT_TOO_LARGE',
        );
        const refused = childRunIdFor(f.parent.orgId, f.parent.runId, 't2.0');
        expect(
          (await workspaces.head(attemptOf(f.parent), childForkId(f.parent.sessionId, refused))).revision,
        ).to.equal(0);
      });

      it('CF-4: a replay forks nothing and keeps the first revision', async () => {
        const root = sessionWorkspaceId(f.parent.sessionId);
        await write(f.parent, root, 'one');
        const first = await forkSpawn('t1.0');
        await write(f.parent, root, 'two');
        const again = await forkSpawn('t1.0');
        expect(again).to.deep.equal({ childRunId: first.childRunId, created: false });
        expect((await pinOf(first.childRunId))?.workspace?.forkOf.revision).to.equal(1);
        const fork = await workspaces.head(attemptOf(f.parent), childForkId(f.parent.sessionId, first.childRunId));
        expect(fork.revision).to.equal(1);
      });

      it('CF-5: a fork a crashed admission left is reused at its own revision', async () => {
        const root = sessionWorkspaceId(f.parent.sessionId);
        await write(f.parent, root, 'one');
        const childRunId = childRunIdFor(f.parent.orgId, f.parent.runId, 't1.0');
        await workspaces.forkForChild(attemptOf(f.parent), root, childForkId(f.parent.sessionId, childRunId));
        await write(f.parent, root, 'two');
        await forkSpawn('t1.0');
        expect((await pinOf(childRunId))?.workspace?.forkOf).to.deep.equal({
          workspaceId: root,
          revision: 1,
        });
      });

      it('CF-6: a grandchild forks its parent’s fork, never the session’s workspace', async () => {
        const { childRunId } = await forkSpawn('t1.0', { maxChildren: 1 });
        const child = await f.h.claim(await run(childRunId), 'worker-c', LEASE_MS);
        const own = childForkId(f.parent.sessionId, childRunId);
        await write(child, own, 'mine');
        const grand = await forkSpawn('t1.0', {}, child);
        const pin = await pinOf(grand.childRunId);
        expect(pin?.workspace).to.deep.equal({
          workspaceId: childForkId(f.parent.sessionId, grand.childRunId),
          forkOf: { workspaceId: own, revision: 2 },
        });
      });
    });

    describe('an engine switched off while a worker holds its child (WP-111)', () => {
      const LEASE = 60_000;

      const commitHolder = (held: HarnessRun, h: ContractHarness) =>
        h.service.commit({
          commandId: `end-${held.runId}`,
          orgId: held.orgId,
          runId: held.runId,
          workerId: ENGINE_HOLDER,
          attemptId: held.attemptId as string,
          fence: held.fence,
          events: [],
          legacyEvents: [],
          transition: {
            status: 'completed',
            outcome: 'answer',
            childResult: CHILD_RESULT,
          } as never,
          outbox: [],
        });

      const claimAs = (service: ExecutionService, held: HarnessRun, workerId: string) =>
        service.claim({
          orgId: held.orgId,
          runId: held.runId,
          workerId,
          attemptId: `attempt-${workerId}`,
          leaseDurationMs: LEASE,
        });

      it('EO-15: a claim by another worker is refused and the live attempt runs on: still running, its commit applies', async () => {
        const clock = contractClock();
        const backend = createBackend(clock);
        await backend.reset?.();
        const h = createContractHarness(backend, clock);
        const held = await heldEngineChild(h, LEASE);
        h.setNow(held.leaseExpiresAt! - 1);
        expect(
          await conflictCode(claimAs(engineOffService(backend), held, 'engine-b')),
        ).to.equal('RUN_NOT_CLAIMABLE');
        expect(await h.service.get(held.orgId, held.runId)).to.include({
          status: 'running',
          workerId: ENGINE_HOLDER,
          attemptId: held.attemptId,
          leaseExpiresAt: held.leaseExpiresAt,
          revision: held.revision,
        });
        expect((await commitHolder(held, h)).run.status).to.equal('completed');
      });

      it('EO-16: once the attempt is lost (its lease lapsed), the next claim fails the run engine_off', async () => {
        const clock = contractClock();
        const backend = createBackend(clock);
        await backend.reset?.();
        const h = createContractHarness(backend, clock);
        const held = await heldEngineChild(h, LEASE);
        h.setNow(held.leaseExpiresAt!);
        expect(
          await conflictCode(claimAs(engineOffService(backend), held, 'engine-b')),
        ).to.equal('RUN_NOT_CLAIMABLE');
        expect((await h.service.get(held.orgId, held.runId))?.status).to.equal('failed');
        const failed = (
          await h.backend.journal.readStream(held.orgId, held.runId, 0, 100)
        ).find((event) => event.type === 'run.failed');
        expect(failed?.data).to.include({ errorType: 'engine_off' });
      });
    });

    describe('budget (P6.5)', () => {
      let ledger: BudgetLedgerStore;

      beforeEach(async function () {
        f = await family(4);
        if (f.h.backend.budgets === undefined) this.skip();
        ledger = f.h.backend.budgets;
      });

      const spend = async (runId: string, tokens: number, key = 'spent') => {
        const { reservation } = await ledger.reserve({
          orgId: f.parent.orgId,
          runId,
          key,
          requested: { usdMicros: 0, tokens },
          minimum: { usdMicros: 0, tokens },
          ttlMs: 60_000,
        });
        await ledger.commit(f.parent.orgId, runId, key, reservation.granted);
      };

      it('NC-1: admission opens the child account in its transaction; the parent opens from its pin', async () => {
        const share = await spawn('t1.0', { maxTokens: 250_000 });
        const inherit = await spawn('t1.1');
        const pin = readPinnedSpec(
          (await f.h.service.pinned(f.parent.orgId, f.parent.runId))!.pinned!.spec,
        );
        const parent = await ledger.account(f.parent.orgId, f.parent.runId);
        expect(parent?.cap).to.deep.equal({
          usdMicros: Math.round(pin.budgets.maxCostUsd * 1_000_000),
          tokens: pin.budgets.maxTokens,
        });
        const shared = await ledger.account(f.parent.orgId, share.childRunId);
        expect(shared).to.deep.include({
          parentRunId: f.parent.runId,
          rootRunId: f.parent.runId,
          cap: { usdMicros: null, tokens: 250_000 },
        });
        expect((await ledger.account(f.parent.orgId, inherit.childRunId))?.cap).to.deep.equal({
          usdMicros: null,
          tokens: null,
        });
      });

      it('NC-3: a share the parent cannot cover, or an inheriting child of a spent parent, is refused with nothing written', async () => {
        await spawn('t0.0');
        const pin = readPinnedSpec(
          (await f.h.service.pinned(f.parent.orgId, f.parent.runId))!.pinned!.spec,
        );
        await spend(f.parent.runId, pin.budgets.maxTokens - 100_000);
        const before = await types(f.parent.runId);
        expect(await conflictCode(spawn('t1.0', { maxTokens: 150_000 }))).to.equal(
          'CHILD_BUDGET_EXCEEDS_REMAINING',
        );
        const refused = childRunIdFor(f.parent.orgId, f.parent.runId, 't1.0');
        expect(await f.h.service.get(f.parent.orgId, refused)).to.equal(null);
        expect(await ledger.account(f.parent.orgId, refused)).to.equal(null);
        expect(await types(f.parent.runId)).to.deep.equal(before);
        expect((await spawn('t1.1', { maxTokens: 90_000 })).created).to.equal(true);
        await spend(f.parent.runId, 100_000, 'rest');
        expect(await conflictCode(spawn('t1.2'))).to.equal('CHILD_BUDGET_EXCEEDS_REMAINING');
      });

      it('NC-4: a replayed admission does not open the account again, whatever the parent spent since', async () => {
        const first = await spawn('t1.0', { maxTokens: 250_000 });
        const pin = readPinnedSpec(
          (await f.h.service.pinned(f.parent.orgId, f.parent.runId))!.pinned!.spec,
        );
        await spend(f.parent.runId, pin.budgets.maxTokens - 1_000);
        const replay = await spawn('t1.0', { maxTokens: 250_000 });
        expect(replay).to.deep.equal({ childRunId: first.childRunId, created: false });
        expect((await ledger.account(f.parent.orgId, first.childRunId))?.cap.tokens).to.equal(250_000);
      });

      it('NC-5: a parent and its children spending at once never take the tree past its cap', async () => {
        const share = await spawn('t1.0', { maxTokens: 200_000 });
        const inherit = await spawn('t1.1');
        const pin = readPinnedSpec(
          (await f.h.service.pinned(f.parent.orgId, f.parent.runId))!.pinned!.spec,
        );
        const cap = pin.budgets.maxTokens;
        const runs = [f.parent.runId, share.childRunId, inherit.childRunId];
        const granted = await Promise.all(
          Array.from({ length: 24 }, async (_, index) => {
            const runId = runs[index % runs.length] as string;
            try {
              const { reservation } = await ledger.reserve({
                orgId: f.parent.orgId,
                runId,
                key: `call-${String(index)}`,
                requested: { usdMicros: 0, tokens: Math.ceil(cap / 10) },
                minimum: { usdMicros: 0, tokens: 1_000 },
                ttlMs: 60_000,
              });
              await ledger.commit(f.parent.orgId, runId, reservation.key, reservation.granted);
              return reservation.granted.tokens;
            } catch (error) {
              if (error instanceof HarnessConflictError) return 0;
              throw error;
            }
          }),
        );
        const root = await ledger.remaining(f.parent.orgId, f.parent.runId);
        expect(root.committed.tokens).to.equal(granted.reduce((a, b) => a + b, 0));
        expect(root.committed.tokens).to.be.at.most(cap);
        expect(root.reserved.tokens).to.equal(0);
        expect(
          (await ledger.remaining(f.parent.orgId, share.childRunId)).committed.tokens,
        ).to.be.at.most(200_000);
      });
    });
  });
}

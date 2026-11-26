/**
 * The long-task scenario's S2 case `child_approval_overnight` on Node's side (WAITCHILD): a chat run joins a code-task
 * child, the child asks the person for an approval ten minutes in, and the person answers the next morning. Node's
 * real stores and run sweeper play the night, sweeping every hour as production does; the case reads what they left.
 *
 *   TS_NODE_PROJECT=tsconfig.test.json npx ts-node --transpile-only tests/contracts/support/child-wait-overnight-cli.ts
 *
 * Reads {"answerAfterH": 16}; prints {"steps": [...], "statuses": {...}, "errors": {...}}: each step with the
 * time (minutes since the parent was admitted), what happened, and both runs' status and deadline after it.
 */
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import type { ChildResult } from '../../../src/modules/agent-harness/domain/child-runs';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { inlineContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { agentDefinition } from '../../modules/agent-harness/support/agent-definition';
import { testCipher } from '../../modules/agent-harness/support/data-protection';
import { contractClock, createContractHarness, START } from '../../modules/agent-harness/support/execution-store-contract/harness';
import { inMemoryBackend } from '../../modules/agent-harness/support/in-memory-backend';
import { workerEvent } from '../../modules/agent-harness/support/journal-fixtures';
import { HARNESS_ENABLED } from '../../modules/agent-harness/support/rollout';

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const INTERACTION = 'ix.approve.code_task.0';
const RESULT: ChildResult = {
  resultRef: inlineContentRef(Buffer.from(JSON.stringify({ output: 'the failing test passes now', success: true }))),
  labels: { integrity: 'internal' },
};

interface Step {
  atMin: number;
  what: string;
  parent: string;
  child: string;
  parentDeadlineMin: number;
  childDeadlineMin: number;
  swept?: number;
}

async function main(answerAfterH: number): Promise<Record<string, unknown>> {
  const clock = contractClock();
  const backend = inMemoryBackend(clock);
  const h = createContractHarness(backend, clock);
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
  h.setAgentDefinition((agentKey) => agentDefinition({ agentKey, maxChildren: 2 }));
  const children = new ChildRunService({
    runs: backend.executionStore,
    children: backend.childRuns!,
    journals: new JournalService(backend.payloads, testCipher()),
  });
  const steps: Step[] = [];
  const get = async (runId: string): Promise<HarnessRun> => (await h.service.get(h.tenant.orgId, runId)) as HarnessRun;
  const commit = (run: HarnessRun, commandId: string, transition?: Record<string, unknown>, events = [] as ReturnType<typeof workerEvent>[]) =>
    h.service.commit({
      commandId,
      orgId: run.orgId,
      runId: run.runId,
      workerId: run.workerId as string,
      attemptId: run.attemptId as string,
      fence: run.fence,
      events,
      legacyEvents: [],
      ...(transition !== undefined ? { transition: transition as never } : {}),
      outbox: [],
    });

  const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', deadlineMs: HOUR });
  const parentId = admitted.run.runId;
  let childId = '';
  const record = async (what: string, swept?: number) => {
    const parent = await get(parentId);
    const child = childId !== '' ? await get(childId) : null;
    steps.push({
      atMin: (clock.now() - START) / MINUTE,
      what,
      parent: parent.status,
      child: child?.status ?? 'none',
      parentDeadlineMin: (parent.absoluteDeadlineAt - START) / MINUTE,
      childDeadlineMin: child !== null ? (child.absoluteDeadlineAt - START) / MINUTE : 0,
      ...(swept !== undefined ? { swept } : {}),
    });
  };

  const parent = await h.claim(admitted.run, 'worker-1', 20 * MINUTE);
  ({ childRunId: childId } = await children.spawn(
    { orgId: parent.orgId, runId: parent.runId, workerId: 'worker-1', attemptId: parent.attemptId as string, fence: parent.fence },
    { invocationId: 'code_task.0', goal: 'Fix the failing CI test and open the change', grants: {} },
  ));
  await record('parent spawned the code task');
  clock.set(START + 5 * MINUTE);
  await commit(parent, 'parent:join', { status: 'paused', detail: { kind: 'suspend', reason: 'join' } });
  await record('parent joined the code task');
  const child = await h.claim(await get(childId), 'worker-2', 20 * MINUTE);
  clock.set(START + 10 * MINUTE);
  await commit(child, 'child:ask', undefined, [
    workerEvent('interaction.requested', { interactionId: INTERACTION, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` }),
  ]);
  await commit(child, 'child:suspend', {
    status: 'waiting_approval',
    detail: { kind: 'suspend', reason: 'approval', interactionId: INTERACTION },
  });
  await record('code task asked for an approval');

  const answeredAt = START + 10 * MINUTE + answerAfterH * HOUR;
  for (let at = START + HOUR; at < answeredAt; at += HOUR) {
    clock.set(at);
    await record('sweeper ran', await h.service.sweep(100));
  }
  clock.set(answeredAt);
  const errors: Record<string, string> = {};
  try {
    await new InteractionService(h.service).resolve({
      orgId: h.tenant.orgId,
      runId: childId,
      interactionId: INTERACTION,
      decision: 'approved',
      channel: 'web',
      by: 'p_actor',
      commandId: 'person:approve',
    });
    await record('person approved');
    const resumed = await h.claim(await get(childId), 'worker-3', 20 * MINUTE);
    await commit(resumed, 'child:done', { status: 'completed', outcome: 'answer', childResult: RESULT });
    await record('code task finished');
    const woken = await h.claim(await get(parentId), 'worker-4', HOUR);
    await commit(woken, 'parent:done', { status: 'completed', outcome: 'answer' });
    await record('parent finished');
  } catch (error) {
    errors['afterAnswer'] = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
  }
  const statuses = { parent: (await get(parentId)).status, child: (await get(childId)).status };
  return { steps, statuses, errors };
}

const chunks: Buffer[] = [];
process.stdin.on('data', (chunk: Buffer) => chunks.push(chunk));
process.stdin.on('end', () => {
  const input = JSON.parse(Buffer.concat(chunks).toString('utf-8') || '{}') as { answerAfterH?: number };
  main(input.answerAfterH ?? 16).then(
    (out) => process.stdout.write(`${JSON.stringify(out)}\n`),
    (error: unknown) => {
      process.stderr.write(`${error instanceof Error ? (error.stack ?? error.message) : String(error)}\n`);
      process.exitCode = 1;
    },
  );
});

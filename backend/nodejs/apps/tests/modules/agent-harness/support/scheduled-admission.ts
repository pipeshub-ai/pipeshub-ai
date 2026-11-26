/**
 * P8a: a schedule's firing through the real admission path (RunAdmissionService
 * over the in-memory execution store) and the real claim (ExecutionService,
 * which pins the spec), with an agent authority that grants only to the
 * actor's own credential.
 */
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryScheduleStore } from '../../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../../src/modules/agent-harness/application/run-admission-service';
import { ScheduleFirer } from '../../../../src/modules/agent-harness/application/schedule-firer';
import { RunAdmissionScheduledRuns } from '../../../../src/modules/agent-harness/application/scheduled-run-admission';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../../src/modules/agent-harness/domain/capabilities';
import type { HarnessMajor } from '../../../../src/modules/agent-harness/domain/rollout';
import type { AgentAccessPolicy } from '../../../../src/modules/agent-harness/ports/agent-access';
import type {
  ScheduledWaitNotices,
  RunStatusReader,
} from '../../../../src/modules/agent-harness/ports/schedules';
import { agentDefinition } from './agent-definition';
import { testCipher } from './data-protection';
import { HARNESS_ENABLED } from './rollout';
import { FakeActors, FakeNotifier, FakeSetting, TestClock } from './schedules';

const DURABLE = capabilitySnapshot(
  { ...LEAST_CAPABILITIES, 'journal.durable': true },
  1,
);

/** Grants the agent only to a request carrying the actor's own credential. */
export const actorOnlyAccess = (): AgentAccessPolicy => ({
  authorizeRun: async (request) =>
    request.authorization === `Bearer token-of-${request.actorId}`
      ? agentDefinition({ agentKey: request.agentKey })
      : null,
});

export interface ScheduledWorld {
  clock: TestClock;
  schedules: InMemoryScheduleStore;
  executions: InMemoryExecutionStore;
  service: ExecutionService;
  actors: FakeActors;
  setting: FakeSetting;
  notifier: FakeNotifier;
  firer: ScheduleFirer;
}

export function scheduledWorld(
  harnessMajor: HarnessMajor = 2,
  options: { waitNotices?: ScheduledWaitNotices } = {},
): ScheduledWorld {
  const clock = new TestClock();
  const schedules = new InMemoryScheduleStore(clock);
  const executions = new InMemoryExecutionStore(clock);
  const service = new ExecutionService({
    store: executions,
    journal: executions.journal,
    journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
    capabilities: { current: () => DURABLE },
  });
  const decision = { ...HARNESS_ENABLED, harnessMajor };
  const admissions = new RunAdmissionService({
    store: executions,
    rollout: {
      decide: async () => decision,
      decideForOrg: async () => ({ ...decision, harnessOn: true, resumeInFlightRuns: true }),
    },
    clock,
    agentAccess: actorOnlyAccess(),
  });
  const runs: RunStatusReader = {
    status: async (orgId, runId) =>
      (await executions.get(orgId, runId))?.status ?? null,
  };
  const actors = new FakeActors();
  const setting = new FakeSetting();
  const notifier = new FakeNotifier();
  const firer = new ScheduleFirer({
    store: schedules,
    admission: new RunAdmissionScheduledRuns(admissions),
    actors,
    setting,
    runs,
    notifier,
    ...(options.waitNotices !== undefined ? { waitNotices: options.waitNotices } : {}),
  });
  return { clock, schedules, executions, service, actors, setting, notifier, firer };
}

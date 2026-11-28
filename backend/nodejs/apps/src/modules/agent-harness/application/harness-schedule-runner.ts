import type { RunStatus } from '../domain/execution';
import type {
  RunDigestReader,
  RunStatusReader,
  ScheduledRunAdmission,
} from '../ports/schedules';
import type {
  EndedScheduleRunStatus,
  ScheduleRunner,
  ScheduleRunRequest,
  ScheduleRunStart,
  ScheduleRunState,
  ScheduleRunStatus,
} from '../ports/schedule-runner';
import type { RunDigest } from '../projection/run-digest';

/** A durable run's status as a firing reads it. */
export function scheduleRunStatus(status: RunStatus): ScheduleRunStatus {
  switch (status) {
    case 'queued':
      return 'queued';
    case 'waiting_approval':
      return 'waiting_for_you';
    case 'completed':
    case 'failed':
    case 'cancelled':
      return status;
    case 'running':
    case 'paused':
    case 'recovering':
    case 'needs_reconciliation':
    case 'cancelling':
      return 'running';
  }
}

/**
 * A firing as a durable run of the agent harness (ADR-SCH-02 D2): today's admission, status and digest, unchanged.
 * Its `runRef` is the run's id.
 */
export class HarnessScheduleRunner implements ScheduleRunner {
  readonly kind = 'harness' as const;

  constructor(
    private readonly admission: ScheduledRunAdmission,
    private readonly runs: RunStatusReader,
    private readonly digests: RunDigestReader | null = null,
  ) {}

  async start(request: ScheduleRunRequest): Promise<ScheduleRunStart> {
    const outcome = await this.admission.admit({
      orgId: request.orgId,
      actorId: request.actorId,
      ...(request.agentKey !== undefined ? { agentKey: request.agentKey } : {}),
      ...(request.agentVersion !== undefined
        ? { agentVersion: request.agentVersion }
        : {}),
      ...(request.codingEngine !== undefined
        ? { codingEngine: request.codingEngine }
        : {}),
      idempotencyKey: request.idempotencyKey,
      deadlineMs: request.deadlineMs,
      input: request.input,
      authorization: request.authorization,
      trigger: request.trigger,
    });
    if (outcome.kind === 'refused') return outcome;
    return {
      kind: 'started',
      runRef: outcome.runId,
      runId: outcome.runId,
      sessionId: outcome.sessionId,
    };
  }

  async status(
    orgId: string,
    runRef: string,
  ): Promise<ScheduleRunState | null> {
    const status = await this.runs.status(orgId, runRef);
    return status === null ? null : { status: scheduleRunStatus(status) };
  }

  async summary(
    orgId: string,
    runRef: string,
    status: EndedScheduleRunStatus,
  ): Promise<RunDigest | null> {
    if (this.digests === null) return null;
    return this.digests.digest(orgId, runRef, status);
  }
}

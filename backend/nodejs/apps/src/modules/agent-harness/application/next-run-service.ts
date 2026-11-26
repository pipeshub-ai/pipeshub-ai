import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  HarnessQuotaExceededError,
} from '../domain/errors';
import {
  AdmitRunCommand,
  PinStale,
  RunAdmissionStore,
} from '../domain/admission';
import { HarnessRun } from '../domain/execution';
import { drainCommand, NextRunQueue } from '../domain/next-runs';
import { CollaboratorTurnsSetting } from '../ports/collaborator-turns';
import { HarnessRolloutPolicy } from '../ports/harness-rollout';
import { RoutingDecider } from '../ports/routing-decider';
import { AdmissionRefusal } from './admission/refusals';
import { RolloutGate } from './admission/rollout-gate';
import { RolloutRoutingDecider } from './rollout-routing';
import { NextRunReadmission } from '../ports/next-run-readmission';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import { QuestionAnswered, RunAdmissionClock } from './run-admission-service';

const systemClock: RunAdmissionClock = { now: () => Date.now() };

/** What a drain did: a run admitted, or the session still busy, or nothing waiting. */
export type DrainOutcome = 'admitted' | 'busy' | 'empty';

/** A store refusal's reason code, never its message. */
const reasonOf = (error: unknown): string =>
  error instanceof HarnessConflictError
    ? error.code.toLowerCase()
    : error instanceof HarnessNotFoundError
      ? 'not_found'
      : error instanceof HarnessQuotaExceededError
        ? 'quota'
        : error instanceof Error
          ? error.name
          : 'unknown';

/**
 * Admits the messages that waited for someone else's run to end (SS2
 * `next_run`), oldest first, each as its own sender, through the ordinary
 * admission store: the history is read then, the sender's share is checked
 * again in the transaction, the quotas count against them. One run at a time:
 * the admitted run is the session's active run, and the next waits behind it.
 * A message is admitted durably only while the rollout still sends the org's
 * new runs to the harness: once the kill switch is on (or the org is off),
 * a waiting message is refused with the rollout's reason, never turned into
 * a new durable run (DUR3-F7). Its grant is decided again as its sender's
 * fresh admission would be, never taken as frozen when it was queued (R2 F6).
 */
export interface NextRunServiceOptions {
  store: RunAdmissionStore & NextRunQueue;
  /** Decides each drained message again as its sender's fresh admission would. */
  readmission: NextRunReadmission;
  collaboratorTurns?: CollaboratorTurnsSetting | null;
  logger?: HarnessLogger;
  clock?: RunAdmissionClock;
  /** Today's rollout, asked through `RolloutRoutingDecider` when no `routing` is given. */
  rollout?: HarnessRolloutPolicy | null;
  /** The decider submit asks (ADM-25); absent with no rollout: every message drains. */
  routing?: RoutingDecider | null;
  /** Told when a drained message moved on a chat whose last run ended on a question (ADR-INBOX-02 D4). */
  questionAnswered?: QuestionAnswered;
}

export class NextRunService {
  private readonly store: RunAdmissionStore & NextRunQueue;
  private readonly readmission: NextRunReadmission;
  private readonly collaboratorTurns: CollaboratorTurnsSetting | null;
  private readonly logger: HarnessLogger;
  private readonly clock: RunAdmissionClock;
  private readonly gate: RolloutGate | null;
  private readonly questionAnswered: QuestionAnswered | null;

  constructor(options: NextRunServiceOptions) {
    this.store = options.store;
    this.readmission = options.readmission;
    this.collaboratorTurns = options.collaboratorTurns ?? null;
    this.logger = options.logger ?? silentHarnessLogger;
    this.clock = options.clock ?? systemClock;
    this.questionAnswered = options.questionAnswered ?? null;
    const routing =
      options.routing ??
      (options.rollout == null
        ? null
        : new RolloutRoutingDecider({ rollout: options.rollout }));
    this.gate = routing === null ? null : new RolloutGate(routing);
  }

  /** Called when a root run ended; never throws (the run's ending already committed). */
  runEnded(run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'parent'>): void {
    if (run.parent !== undefined) return;
    void this.drain(run.orgId, run.sessionId).catch((error: unknown) => {
      this.logger.warn(
        'next_run.drain_failed',
        harnessLogMeta('next_run.drain_failed', {
          orgId: run.orgId,
          sessionId: run.sessionId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    });
  }

  /** The sweeper's backstop: sessions whose ending a drain missed. */
  async drainDue(limit: number): Promise<number> {
    let admitted = 0;
    for (const { orgId, sessionId } of await this.store.drainable(limit)) {
      const outcome = await forEnumeratedOrg(
        orgId,
        'agent-harness.next-runs',
        () => this.drain(orgId, sessionId),
      );
      if (outcome === 'admitted') admitted += 1;
    }
    return admitted;
  }

  async drain(orgId: string, sessionId: string): Promise<DrainOutcome> {
    for (;;) {
      const next = await this.store.nextPending(orgId, sessionId);
      if (next === null) return 'empty';
      const fields = {
        orgId,
        sessionId,
        actorId: next.actorId,
        nextRunId: next.id,
        runId: next.runId,
        behindRunId: next.behindRunId,
      };
      const queued = drainCommand(next);
      const rolledBack = await this.rolledBack(queued);
      if (rolledBack !== null) {
        await this.refuse(fields, rolledBack);
        continue;
      }
      if (
        queued.asCollaborator === true &&
        !(await this.collaboratorTurnsOn(orgId))
      ) {
        await this.refuse(fields, 'collaborator_turns_disabled');
        continue;
      }
      const decided = await this.readmission.readmit(queued);
      if ('refused' in decided) {
        await this.refuse(fields, decided.refused);
        continue;
      }
      const now = this.clock.now();
      try {
        const admitted = await this.store.admit(decided.command, now);
        const asking = decided.command.answered?.runId;
        if (admitted.created && asking !== undefined) this.told(orgId, asking);
        await this.store.settleNextRun(
          orgId,
          next.id,
          { status: 'admitted', runId: admitted.run.runId },
          now,
        );
        this.logger.info(
          'next_run.admitted',
          harnessLogMeta('next_run.admitted', {
            ...fields,
            runId: admitted.run.runId,
            replayed: !admitted.created,
            sessionRole: admitted.run.sessionRole ?? null,
            waitedMs: now - next.createdAt,
          }),
        );
        return 'admitted';
      } catch (error) {
        // The control plane changed under the pin: an outage, so it keeps its place for the sweeper.
        if (error instanceof PinStale) throw error;
        // Someone else's run started first: this one keeps its place.
        if (
          error instanceof HarnessConflictError &&
          error.code === 'RUN_ACTIVE'
        ) {
          return 'busy';
        }
        await this.refuse(fields, reasonOf(error));
      }
    }
  }

  private told(orgId: string, askingRunId: string): void {
    try {
      this.questionAnswered?.(orgId, askingRunId);
    } catch {
      // A push is a hint: the asker's next read counts without the question.
    }
  }

  /**
   * The route a fresh turn of the sender's would take now (ADM-25): the
   * decider's reason when it would not be durable, `null` when it would. An
   * undecidable route is an outage, not a refusal: the message keeps its
   * place and the sweeper drains it again.
   */
  private async rolledBack(command: AdmitRunCommand): Promise<string | null> {
    if (this.gate === null) return null;
    try {
      const decision = await this.gate.decide({
        orgId: command.orgId,
        actorId: command.actorId,
        ...(command.agentKey !== undefined
          ? { agentKey: command.agentKey }
          : {}),
        ...(command.sessionId !== null ? { sessionId: command.sessionId } : {}),
        input: command.input,
        ...(command.trigger !== undefined ? { trigger: command.trigger } : {}),
      });
      return decision.routing.useHarness ? null : decision.routing.reason;
    } catch (error) {
      throw error instanceof AdmissionRefusal ? error.error : error;
    }
  }

  private async refuse(
    fields: Record<string, string>,
    reason: string,
  ): Promise<void> {
    await this.store.settleNextRun(
      fields['orgId'] as string,
      fields['nextRunId'] as string,
      { status: 'refused', reason },
      this.clock.now(),
    );
    this.logger.warn(
      'next_run.refused',
      harnessLogMeta('next_run.refused', { ...fields, reason }),
    );
  }

  private async collaboratorTurnsOn(orgId: string): Promise<boolean> {
    if (this.collaboratorTurns === null) return false;
    try {
      return await this.collaboratorTurns.enabled(orgId);
    } catch {
      return false;
    }
  }
}

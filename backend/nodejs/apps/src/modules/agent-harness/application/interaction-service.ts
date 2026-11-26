import type { CommitFence } from '../ports/commit-fence';
import { HarnessConflictError, HarnessNotFoundError } from '../domain/errors';
import {
  CommitReceipt,
  InteractionChannel,
  InteractionDecision,
  InteractionScope,
  WORKER_OWNED_RUN_STATUSES,
} from '../domain/execution';
import { noRunWakeup, RunWakeup } from '../ports/run-wakeup';
import { ExecutionService } from './execution-service';
import {
  harnessLogMeta,
  HarnessLogger,
  runLogFields,
  silentHarnessLogger,
} from './harness-log';

export interface ResolveInteraction {
  orgId: string;
  runId: string;
  interactionId: string;
  decision: InteractionDecision;
  channel: InteractionChannel;
  /** An approval for the rest of the conversation (UX1). */
  scope?: InteractionScope;
  /** AP-05: why the person declined; only with `denied`. */
  reason?: string;
  /** AP-06: the arguments the person approved in place of the call's own; only with `approved`. */
  edits?: Record<string, unknown>;
  /** Principal reference of whoever decided; journaled as `by`. */
  by: string;
  /** Stable per request (who, which interaction, idempotency key): a retry replays. */
  commandId: string;
  /** What the decider's authority depends on, held inside the decision's transaction (QA21-F2). */
  fence?: CommitFence;
}

/** DECIDE409: one decision on several interactions of a run ("Allow all"), journaled in one transaction. */
export type ResolveInteractions = Omit<ResolveInteraction, 'interactionId'> & {
  interactionIds: readonly string[];
};

/**
 * A decision commutes with other commits (DECIDE409); only a change of the run's status (a suspend, a claim, a
 * cancel) between the read and the write makes it re-read and try again.
 */
const MAX_ATTEMPTS = 5;

/**
 * `resolveActive` (ARCHITECTURE 4.5): an interaction is resolved whatever the
 * run is doing. A running run gets `interaction.resolved` alone and its holder
 * is woken; a suspended run is resumed with it; a queued run's next claim
 * folds it. The store's open-interaction check makes two decisions on the same
 * interaction race to exactly one (`INTERACTION_NOT_OPEN` for the other);
 * decisions on different interactions both land.
 */
export class InteractionService {
  constructor(
    private readonly executions: ExecutionService,
    private readonly wakeup: RunWakeup = noRunWakeup,
    private readonly logger: HarnessLogger = silentHarnessLogger,
  ) {}

  resolve(command: ResolveInteraction): Promise<CommitReceipt> {
    const { interactionId, ...rest } = command;
    return this.resolveAll({ ...rest, interactionIds: [interactionId] });
  }

  async resolveAll(command: ResolveInteractions): Promise<CommitReceipt> {
    const prior = await this.executions.controlReceipt(
      command.orgId,
      command.runId,
      command.commandId,
    );
    if (prior !== null) return prior;
    const resolutions = command.interactionIds.map((interactionId) => ({
      interactionId,
      decision: command.decision,
      channel: command.channel,
      ...(command.scope !== undefined ? { scope: command.scope } : {}),
      ...(command.reason !== undefined ? { reason: command.reason } : {}),
      ...(command.edits !== undefined ? { edits: command.edits } : {}),
    }));
    // One id keeps the command (and its payload hash) as it was before batches.
    const single = resolutions.length === 1 ? resolutions[0] : undefined;
    for (let attempt = 1; ; attempt += 1) {
      const run = await this.executions.get(command.orgId, command.runId);
      if (run === null) throw new HarnessNotFoundError();
      try {
        const receipt = await this.executions.control(
          {
            commandId: command.commandId,
            dispatchCommandId: `${command.commandId}:dispatch`,
            orgId: command.orgId,
            runId: command.runId,
            expectedRevision: run.revision,
            expectedStatus: run.status,
            action: 'resolve',
            by: command.by,
            ...(single !== undefined
              ? { resolution: single }
              : { resolutions }),
          },
          command.fence,
        );
        const held = WORKER_OWNED_RUN_STATUSES.has(receipt.run.status);
        this.logger.info(
          'interaction.resolved',
          harnessLogMeta('interaction.resolved', {
            ...runLogFields(run),
            interactionId: command.interactionIds[0] ?? null,
            ...(command.interactionIds.length > 1
              ? { resolved: command.interactionIds.length }
              : {}),
            decision: command.decision,
            channel: command.channel,
            ...(command.scope !== undefined ? { scope: command.scope } : {}),
            from: run.status,
            to: receipt.run.status,
            attempts: attempt,
          }),
        );
        if (held) await this.wake(command);
        return receipt;
      } catch (error) {
        const retry =
          error instanceof HarnessConflictError &&
          error.code === 'REVISION_CONFLICT' &&
          attempt < MAX_ATTEMPTS;
        if (!retry) throw error;
      }
    }
  }

  private async wake(command: ResolveInteractions): Promise<void> {
    try {
      await this.wakeup.wake(command.runId);
    } catch (error) {
      // The holder still reads the decision at its next journal poll.
      this.logger.warn(
        'interaction.wake_failed',
        harnessLogMeta('interaction.wake_failed', {
          orgId: command.orgId,
          runId: command.runId,
          interactionId: command.interactionIds[0] ?? null,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }
}

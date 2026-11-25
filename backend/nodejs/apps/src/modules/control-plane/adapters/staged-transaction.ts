import {
  ConfigChange,
  ConfigChanged,
  changeEventOf,
} from '../domain/config-change';
import {
  ControlPlaneRetryableError,
  ControlPlaneUnavailableError,
} from '../domain/errors';
import type { ChangeOutbox, ConfigAuditLog } from '../ports/config-audit-log';
import type { ControlPlaneLog } from '../ports/log';
import type { Applied, CpTx, WriteContext } from '../ports/transaction';

class AppliedValue<R> implements Applied<R> {
  private settled: { value: R } | null = null;

  get value(): R {
    if (this.settled === null) {
      throw new Error(
        'An external effect is read only after its transaction commits',
      );
    }
    return this.settled.value;
  }

  set(value: R): void {
    this.settled = { value };
  }
}

/** A `CpTx` that holds its external effects until the adapter applies them. */
export class StagedCpTx implements CpTx {
  private readonly effects: Array<() => Promise<void>> = [];
  /** Set once any effect has reached its store: from then on the write cannot be undone. */
  appliedAny = false;

  constructor(
    readonly orgId: string,
    readonly headRevision: number,
    readonly session: unknown,
    private readonly enlister: (write: () => Promise<void>) => Promise<void>,
  ) {}

  enlist(write: () => Promise<void>): Promise<void> {
    return this.enlister(write);
  }

  external<R>(effect: () => Promise<R>): Applied<R> {
    const applied = new AppliedValue<R>();
    this.effects.push(async () => {
      applied.set(await effect());
    });
    return applied;
  }

  async applyEffects(): Promise<void> {
    for (const effect of this.effects) {
      await effect();
      this.appliedAny = true;
    }
  }
}

export const committedChange = (
  ctx: WriteContext,
  headRevision: number,
  change: ConfigChange,
): ConfigChanged => ({
  ...change,
  orgId: ctx.orgId,
  actor: ctx.actor,
  approvalRef: ctx.approvalRef ?? null,
  headRevision,
});

/** Stages the audit entry and the change event, then applies the effects: the order atomic adapters commit in. */
export async function stageAndApply(
  tx: StagedCpTx,
  changed: ConfigChanged,
  audit: ConfigAuditLog,
  outbox: ChangeOutbox | undefined,
): Promise<void> {
  try {
    await audit.record(tx, changed);
    await outbox?.enqueue(tx, changeEventOf(changed));
  } catch (error) {
    // NFR-36: a write whose record cannot be staged is CP_UNAVAILABLE (503), as ordered steps answers.
    throw isRetryable(error)
      ? error
      : new ControlPlaneUnavailableError(
          'The change could not be recorded, so it was not made; try again',
          { cause: error },
        );
  }
  await tx.applyEffects();
}

export interface RetryPolicy {
  readonly attempts: number;
  pause(attempt: number): Promise<void>;
}

export const DEFAULT_RETRY: RetryPolicy = {
  attempts: 8,
  pause: (attempt) =>
    new Promise((resolve) =>
      setTimeout(resolve, Math.random() * Math.min(200, 5 * 2 ** attempt)),
    ),
};

const logMeta = (
  event: string,
  changed: Pick<ConfigChanged, 'orgId' | 'object' | 'headRevision' | 'scope'>,
  extra: Record<string, unknown> = {},
): Record<string, unknown> => ({
  event,
  orgId: changed.orgId,
  scope: changed.scope,
  objectType: changed.object.type,
  objectId: changed.object.id,
  headRevision: changed.headRevision,
  ...extra,
});

export const logCommitted = (
  log: ControlPlaneLog,
  changed: ConfigChanged,
): void => {
  log.info('cp.change.committed', logMeta('cp.change.committed', changed));
};

/**
 * An effect reached its store and the commit then failed: the change is in
 * place without its audit entry. Logged once, at error, for the operator.
 */
export function unaudited(
  log: ControlPlaneLog,
  changed: ConfigChanged | null,
  orgId: string,
  headRevision: number,
  error: unknown,
): ControlPlaneUnavailableError {
  log.error(
    'cp.change.unaudited',
    changed === null
      ? { event: 'cp.change.unaudited', orgId, headRevision }
      : logMeta('cp.change.unaudited', changed, {
          errorType: error instanceof Error ? error.name : typeof error,
        }),
  );
  return new ControlPlaneUnavailableError(
    'The change was applied but could not be recorded; an operator has been alerted',
    { cause: error },
  );
}

export const concurrentlyChanged = (
  cause: unknown,
): ControlPlaneUnavailableError =>
  new ControlPlaneUnavailableError(
    'The configuration changed concurrently; try again',
    {
      cause,
    },
  );

export const isRetryable = (error: unknown): boolean =>
  error instanceof ControlPlaneRetryableError;

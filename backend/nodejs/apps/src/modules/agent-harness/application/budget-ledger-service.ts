import { forOrg } from '../../../libs/context/orgContext';
import { AuthorityClock, systemAuthorityClock } from '../domain/authority';
import { ChatPathMetrics, noChatPathMetrics } from '../ports/chat-path-metrics';
import {
  BudgetAmount,
  BudgetHold,
  BudgetRemaining,
  BudgetReservation,
  BudgetSettlement,
  BudgetUsage,
  childCap,
  HeldByScope,
  MAX_RESERVATION_TTL_MS,
  OpenBudgetAccount,
  pinnedCap,
} from '../domain/budget-ledger';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  HarnessUnauthorizedError,
} from '../domain/errors';
import {
  SharedBudgetReached,
  type BudgetScope,
  type CostAttribution,
} from '../domain/cost-ledger';
import type { ModelRole } from '../domain/model-roles';
import { HarnessRun } from '../domain/execution';
import { PinnedRunSpec, readPinnedSpec } from '../domain/run-spec';
import {
  runTokenRefusal,
  runTokenRefused,
  RunTokenUse,
} from '../domain/run-token-standing';
import { BudgetLedgerStore } from '../ports/budget-ledger';
import { noSharedBudgets, type SharedBudgetGuard } from '../ports/cost-ledger';
import { GatewayTokenAuthority } from './gateway-token-authority';
import { HarnessAuthority } from './harness-authority';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

export interface PinnedRuns {
  pinned(
    orgId: string,
    runId: string,
  ): Promise<{ run: HarnessRun; pinned: PinnedRunSpec | null } | null>;
}

/**
 * A run's account and its ancestors', root first, each from its pin: what a
 * lazily opened account (a run admitted before P6.5) or a child admission
 * opens when missing. A child's own cap is its pin's share of its parent's.
 */
export async function budgetChain(
  runs: PinnedRuns,
  run: HarnessRun,
  pin: PinnedRunSpec,
): Promise<OpenBudgetAccount[]> {
  const chain: OpenBudgetAccount[] = [];
  let current: { run: HarnessRun; pin: PinnedRunSpec } = { run, pin };
  for (;;) {
    const parentRunId = current.run.parent?.runId ?? null;
    const own = pinnedCap(readPinnedSpec(current.pin.spec));
    if (parentRunId === null) {
      chain.unshift({ runId: current.run.runId, parentRunId: null, cap: own });
      return chain;
    }
    const parent = await runs.pinned(run.orgId, parentRunId);
    if (parent === null || parent.pinned === null) {
      throw new HarnessConflictError(
        'BUDGET_ACCOUNT_MISSING',
        "The run's parent has no pinned budget",
      );
    }
    chain.unshift({
      runId: current.run.runId,
      parentRunId,
      cap: childCap(pinnedCap(readPinnedSpec(parent.pinned.spec)), own),
    });
    current = { run: parent.run, pin: parent.pinned };
  }
}

/**
 * Who asks: a run's gateway token (the model gateway) or its attempt token (the
 * worker's own loop).
 */
export interface BudgetCaller {
  orgId: string;
  runId: string;
  attemptId: string;
  fence: number;
  via: 'gateway' | 'attempt';
}

/**
 * A settlement may come after the caller's gateway token expired (a long
 * stream); it may not come after the longest reservation could have lapsed.
 */
export const SETTLE_TOKEN_GRACE_MS = MAX_RESERVATION_TTL_MS;

export interface BudgetLedgerServiceOptions {
  ledger: BudgetLedgerStore;
  runs: PinnedRuns;
  attempts: Pick<HarnessAuthority, 'verifyAttemptToken'>;
  gateways: Pick<GatewayTokenAuthority, 'verify'> | null;
  clock?: AuthorityClock;
  logger?: HarnessLogger;
  metrics?: ChatPathMetrics;
  /** Budgets runs share, at each reservation, and an entry with each commit (C9.06); absent: none. */
  shared?: SharedBudgetGuard;
}

/** Runs whose attribution a replica keeps; a run's session, actor and agent never change. */
const ATTRIBUTIONS_KEPT = 2_000;

const attributionOf = (run: HarnessRun): CostAttribution => ({
  sessionId: run.sessionId,
  userId: run.actorId,
  agentKey: run.agentKey ?? null,
});

/**
 * The budget ledger's authority (P6.5). A caller reserves for its own run
 * only, while its attempt holds the run's lease; it settles what it reserved
 * whatever became of the lease (the spend happened), and only what its own
 * attempt reserved: each reservation is bound to its fence (SEC-C-20). A run admitted before
 * P6.5 has no account: its first reservation opens it, and its ancestors',
 * from their pinned budgets, starting at `opening` (the spend E3's counter
 * recorded for it).
 */
export class BudgetLedgerService {
  private readonly ledger: BudgetLedgerStore;
  private readonly runs: PinnedRuns;
  private readonly attempts: Pick<HarnessAuthority, 'verifyAttemptToken'>;
  private readonly gateways: Pick<GatewayTokenAuthority, 'verify'> | null;
  private readonly clock: AuthorityClock;
  private readonly logger: HarnessLogger;
  private readonly metrics: ChatPathMetrics;
  private readonly shared: SharedBudgetGuard;
  private readonly attributions = new Map<string, CostAttribution>();

  constructor(options: BudgetLedgerServiceOptions) {
    this.ledger = options.ledger;
    this.runs = options.runs;
    this.attempts = options.attempts;
    this.gateways = options.gateways;
    this.clock = options.clock ?? systemAuthorityClock;
    this.logger = options.logger ?? silentHarnessLogger;
    this.metrics = options.metrics ?? noChatPathMetrics;
    this.shared = options.shared ?? noSharedBudgets;
  }

  async reserve(
    token: string | undefined,
    runId: string,
    request: {
      key: string;
      requested: BudgetAmount;
      minimum: BudgetAmount;
      ttlS: number;
      opening?: BudgetAmount;
    },
  ): Promise<{ reservation: BudgetReservation; remaining: BudgetRemaining }> {
    return this.asCaller(token, runId, false, async (caller) => {
      const run = await this.standing(caller, 'act');
      const command = {
        orgId: caller.orgId,
        runId,
        key: request.key,
        requested: await this.withinSharedBudgets(caller, run, request),
        minimum: request.minimum,
        ttlMs: request.ttlS * 1000,
        fence: caller.fence,
      };
      try {
        return await this.refusing(caller, () => this.ledger.reserve(command));
      } catch (error) {
        if (
          !(error instanceof HarnessConflictError) ||
          error.code !== 'BUDGET_ACCOUNT_MISSING'
        ) {
          throw error;
        }
      }
      await this.openLazily(caller, run, request.opening);
      return this.refusing(caller, () => this.ledger.reserve(command));
    });
  }

  async commit(
    token: string | undefined,
    runId: string,
    key: string,
    actual: BudgetAmount,
    usage?: BudgetUsage,
    hold?: BudgetHold,
    modelRole?: ModelRole,
  ): Promise<BudgetSettlement> {
    return this.asCaller(token, runId, true, async (caller) => {
      const attribution = this.shared.recordsSpend
        ? await this.attribution(caller.orgId, runId)
        : undefined;
      const charge =
        attribution === undefined
          ? undefined
          : { ...attribution, modelRole: modelRole ?? 'primary' };
      const settled = await this.refusing(caller, () =>
        this.ledger.commit(
          caller.orgId,
          runId,
          key,
          actual,
          usage,
          charge,
          hold,
          caller.fence,
        ),
      );
      if (charge !== undefined) void this.shared.foldSoon(caller.orgId);
      this.metrics.modelCharged(
        settled.charged.usdMicros / 1_000_000,
        settled.charged.tokens,
        usage,
      );
      if (settled.crossed) {
        this.logger.warn(
          'budget.crossed',
          harnessLogMeta('budget.crossed', {
            ...this.fields(caller),
            reason: 'BUDGET_EXHAUSTED',
            spentMicroUsd: settled.remaining.committed.usdMicros,
            tokens: settled.remaining.committed.tokens,
          }),
        );
      }
      return settled;
    });
  }

  /**
   * The provider's own usage for a call charged the gateway's estimate
   * (WP-66): settled as a commit is, with the run's token even past its
   * expiry, for the run's own reservation only.
   */
  async reconcile(
    token: string | undefined,
    runId: string,
    key: string,
    actual: BudgetAmount,
    usage?: BudgetUsage,
  ): Promise<BudgetSettlement> {
    return this.asCaller(token, runId, true, async (caller) => {
      const charge = this.shared.recordsSpend
        ? await this.attribution(caller.orgId, runId)
        : undefined;
      const settled = await this.refusing(caller, () =>
        this.ledger.reconcile(
          caller.orgId,
          runId,
          key,
          actual,
          usage,
          charge,
          caller.fence,
        ),
      );
      if (charge !== undefined) void this.shared.foldSoon(caller.orgId);
      this.logger.info(
        'budget.reconciled',
        harnessLogMeta('budget.reconciled', {
          ...this.fields(caller),
          chargedMicroUsd: settled.charged.usdMicros,
          tokens: settled.charged.tokens,
        }),
      );
      return settled;
    });
  }

  async release(
    token: string | undefined,
    runId: string,
    key: string,
  ): Promise<BudgetSettlement> {
    return this.asCaller(token, runId, true, async (caller) => {
      return this.refusing(caller, () =>
        this.ledger.release(caller.orgId, runId, key, caller.fence),
      );
    });
  }

  async remaining(
    token: string | undefined,
    runId: string,
  ): Promise<BudgetRemaining> {
    return this.asCaller(token, runId, false, async (caller) => {
      await this.standing(caller, 'report');
      const left = await this.refusing(caller, () =>
        this.ledger.remaining(caller.orgId, runId),
      );
      const scope = await this.bindingSharedBudget(caller.orgId, runId, left);
      return scope === null ? left : { ...left, budgetScope: scope };
    });
  }

  /** WP56-STOP-1: the shared budget that leaves the run less than its own account, if one does. */
  private async bindingSharedBudget(
    orgId: string,
    runId: string,
    left: BudgetRemaining,
  ): Promise<BudgetScope | null> {
    if (!this.shared.recordsSpend || left.exhausted) return null;
    const attribution = await this.attribution(orgId, runId);
    if (attribution === undefined) return null;
    const binding = await this.shared.stoppingLeft(
      orgId,
      attribution,
      this.heldFor(orgId, attribution),
    );
    if (binding.left === null || binding.scope === null) return null;
    const own = left.available.usdMicros;
    return own === null || binding.left < own ? binding.scope : null;
  }

  /**
   * What the org's cut calls still hold beside their estimates (WP-66), for
   * every reader of the shared budgets, so the budget named as binding
   * (WP56-STOP-1) is the one `admit` enforces (R14-HOLD).
   */
  private heldFor(
    orgId: string,
    attribution: CostAttribution,
  ): () => Promise<HeldByScope> {
    return () =>
      this.ledger.held(orgId, {
        userId: attribution.userId,
        agentKey: attribution.agentKey,
      });
  }

  /** Shared budgets bound what a reservation may ask (BU-03); refused once one is spent. */
  private async withinSharedBudgets(
    caller: BudgetCaller,
    run: HarnessRun,
    request: { requested: BudgetAmount; minimum: BudgetAmount },
  ): Promise<BudgetAmount> {
    if (!this.shared.recordsSpend) return request.requested;
    const attribution = attributionOf(run);
    this.remember(caller.orgId, run.runId, attribution);
    try {
      return await this.shared.admit(
        caller.orgId,
        attribution,
        request.requested,
        request.minimum,
        this.heldFor(caller.orgId, attribution),
      );
    } catch (error) {
      if (error instanceof SharedBudgetReached) {
        this.logger.warn(
          'budget.refused',
          harnessLogMeta('budget.refused', {
            ...this.fields(caller),
            reason: error.code,
            budgetScope: error.scope,
          }),
        );
      }
      throw error;
    }
  }

  /** Who a run's calls are charged to, from the run itself; undefined when the run is gone. */
  private async attribution(
    orgId: string,
    runId: string,
  ): Promise<CostAttribution | undefined> {
    const known = this.attributions.get(`${orgId}:${runId}`);
    if (known !== undefined) return known;
    const found = await this.runs.pinned(orgId, runId);
    if (found === null) return undefined;
    const attribution = attributionOf(found.run);
    this.remember(orgId, runId, attribution);
    return attribution;
  }

  private remember(
    orgId: string,
    runId: string,
    attribution: CostAttribution,
  ): void {
    if (this.attributions.size >= ATTRIBUTIONS_KEPT) this.attributions.clear();
    this.attributions.set(`${orgId}:${runId}`, attribution);
  }

  /** `work` as the caller, once its token is verified: as the org the verified token names. */
  private async asCaller<T>(
    token: string | undefined,
    runId: string,
    settle: boolean,
    work: (caller: BudgetCaller) => Promise<T>,
  ): Promise<T> {
    const caller = await this.caller(token, runId, settle);
    return forOrg(caller.orgId, 'agent-harness.run-token', () => work(caller));
  }

  private async caller(
    token: string | undefined,
    runId: string,
    settle: boolean,
  ): Promise<BudgetCaller> {
    try {
      const claims = await this.attempts.verifyAttemptToken(token, runId);
      return { ...claims, via: 'attempt' };
    } catch (error) {
      if (
        !(error instanceof HarnessUnauthorizedError) ||
        this.gateways === null
      )
        throw error;
    }
    const claims = await this.gateways.verify(
      token,
      settle ? { acceptExpiredForMs: SETTLE_TOKEN_GRACE_MS } : {},
    );
    // Read as a string so the check still holds if another binding kind is ever verified here.
    const binding: string = claims.binding;
    if (binding !== 'run' || claims.runId !== runId) {
      throw new HarnessUnauthorizedError(
        'claims',
        'The token is not for this run',
      );
    }
    return {
      orgId: claims.orgId,
      runId,
      attemptId: claims.attemptId,
      fence: claims.fence,
      via: 'gateway',
    };
  }

  /** The run, if the caller's attempt may do `use` on it now: the run-token routes' one rule (SEC-C-11). */
  private async standing(
    caller: BudgetCaller,
    use: RunTokenUse,
  ): Promise<HarnessRun> {
    const found = await this.runs.pinned(caller.orgId, caller.runId);
    if (found === null) throw new HarnessNotFoundError();
    const refusal = runTokenRefusal(found.run, caller, use, this.clock.now());
    if (refusal !== null) {
      this.logger.warn(
        'budget.refused',
        harnessLogMeta('budget.refused', {
          ...this.fields(caller),
          reason: 'RUN_NOT_LEASED',
          standing: refusal,
        }),
      );
      throw runTokenRefused(refusal);
    }
    return found.run;
  }

  private async openLazily(
    caller: BudgetCaller,
    run: HarnessRun,
    opening: BudgetAmount | undefined,
  ): Promise<void> {
    const found = await this.runs.pinned(run.orgId, run.runId);
    if (found === null || found.pinned === null) {
      throw new HarnessConflictError(
        'BUDGET_ACCOUNT_MISSING',
        'The run has no pinned budget',
      );
    }
    const chain = await budgetChain(this.runs, run, found.pinned);
    await this.ledger.ensure(run.orgId, chain, opening);
    this.logger.info(
      'budget.opened',
      harnessLogMeta('budget.opened', {
        ...this.fields(caller),
        accounts: chain.length,
        openingMicroUsd: opening?.usdMicros,
        openingTokens: opening?.tokens,
      }),
    );
  }

  private async refusing<T>(
    caller: BudgetCaller,
    work: () => Promise<T>,
  ): Promise<T> {
    try {
      return await work();
    } catch (error) {
      if (
        error instanceof HarnessConflictError &&
        error.code !== 'BUDGET_ACCOUNT_MISSING'
      ) {
        this.logger.warn(
          'budget.refused',
          harnessLogMeta('budget.refused', {
            ...this.fields(caller),
            reason: error.code,
          }),
        );
      }
      throw error;
    }
  }

  private fields(caller: BudgetCaller): Record<string, string | number> {
    return {
      orgId: caller.orgId,
      runId: caller.runId,
      attemptId: caller.attemptId,
      fence: caller.fence,
      via: caller.via,
    };
  }
}

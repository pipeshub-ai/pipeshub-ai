/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import {
  addAmounts,
  addUsage,
  assertAmount,
  assertReservationOf,
  assertUsage,
  BudgetAccount,
  budgetAccountId,
  BudgetAmount,
  BudgetCap,
  budgetCrossed,
  BudgetHold,
  BudgetHolder,
  HeldByScope,
  heldByScope,
  holdReservation,
  budgetRefused,
  budgetRemaining,
  BudgetRemaining,
  BudgetReservation,
  budgetReservationId,
  BudgetSettlement,
  BudgetUsage,
  grantReservation,
  holdKey,
  MAX_RESERVATION_TTL_MS,
  newBudgetAccount,
  OpenBudgetAccount,
  pathRunIds,
  reserveForChild,
  subtractUsage,
  ZERO_AMOUNT,
  ZERO_USAGE,
} from '../domain/budget-ledger';
import {
  BudgetLedgerStore,
  ChildBudgetAccount,
  ReserveBudget,
} from '../ports/budget-ledger';
import type { ModelRole } from '../domain/model-roles';
import {
  CostAttribution,
  CostEntry,
  costEntry,
  reconcileEntry,
} from '../domain/cost-ledger';

/** Where an in-process ledger writes a charged call's entry, synchronously inside its commit. */
export interface InMemoryCostEntries {
  insertNow(entry: CostEntry): void;
  /** The role a call's own entry was charged under; absent: none. */
  roleNow(orgId: string, runId: string, key: string): ModelRole | undefined;
}

const copy = <T>(value: T): T => structuredClone(value);

/**
 * The ledger in process (tests, the journal test server, an embedding host).
 * Every operation is synchronous inside (`…Now`), so concurrent calls of one
 * event loop see each whole; `InMemoryExecutionStore` opens a child's account
 * with `admitChildNow` before it writes the child.
 */
export class InMemoryBudgetLedger implements BudgetLedgerStore {
  readonly accounts = new Map<string, BudgetAccount>();
  readonly reservations = new Map<string, BudgetReservation>();

  constructor(
    private readonly now: () => number = Date.now,
    private readonly costs: InMemoryCostEntries | null = null,
  ) {}

  async account(orgId: string, runId: string): Promise<BudgetAccount | null> {
    const found = this.accounts.get(budgetAccountId(orgId, runId));
    return found === undefined ? null : copy(found);
  }

  async ensure(
    orgId: string,
    chain: readonly OpenBudgetAccount[],
    opening?: BudgetAmount,
  ): Promise<void> {
    this.ensureNow(orgId, chain, opening);
  }

  async openRoot(
    orgId: string,
    runId: string,
    cap: BudgetCap,
    opening?: BudgetAmount,
  ): Promise<BudgetRemaining> {
    this.ensureNow(orgId, [{ runId, parentRunId: null, cap }], opening);
    return this.remainingNow(orgId, runId);
  }

  async openChild(
    orgId: string,
    parentRunId: string,
    childRunId: string,
    cap: BudgetCap,
  ): Promise<BudgetRemaining> {
    this.openChildNow(orgId, parentRunId, childRunId, cap);
    return this.remainingNow(orgId, childRunId);
  }

  async reserve(
    command: ReserveBudget,
  ): Promise<{ reservation: BudgetReservation; remaining: BudgetRemaining }> {
    const reservation = this.reserveNow(command);
    return {
      reservation: copy(reservation),
      remaining: this.remainingNow(command.orgId, command.runId),
    };
  }

  async commit(
    orgId: string,
    runId: string,
    key: string,
    actual: BudgetAmount,
    usage?: BudgetUsage,
    charge?: CostAttribution,
    hold?: BudgetHold,
    fence?: number,
  ): Promise<BudgetSettlement> {
    assertAmount(actual);
    if (usage !== undefined) assertUsage(usage);
    if (hold !== undefined) assertAmount(hold.amount);
    this.expireNow(orgId);
    const held = this.find(orgId, runId, key, fence);
    if (held.state === 'committed') return this.settlement(held);
    if (held.state === 'released') {
      throw budgetRefused(
        'BUDGET_RESERVATION_SETTLED',
        'The reservation was released; nothing was charged',
      );
    }
    const before = this.path(orgId, runId).map(copy);
    const returned = held.state === 'open' ? held.granted : ZERO_AMOUNT;
    this.add(orgId, held.accounts, actual, returned);
    held.state = 'committed';
    held.charged = { ...actual };
    held.crossed = budgetCrossed(before, this.path(orgId, runId));
    if (usage !== undefined) {
      held.usage = { ...usage };
      const own = this.accounts.get(budgetAccountId(orgId, runId));
      if (own !== undefined) own.used = addUsage(own.used ?? ZERO_USAGE, usage);
    }
    if (hold !== undefined) {
      held.estimated = true;
      this.count(orgId, held.accounts, 1);
      const reservation = holdReservation(
        held,
        hold,
        this.now(),
        charge === undefined
          ? undefined
          : { userId: charge.userId, agentKey: charge.agentKey },
      );
      const id = budgetReservationId(orgId, runId, reservation.key);
      if (
        hold.amount.usdMicros + hold.amount.tokens > 0 &&
        !this.reservations.has(id)
      ) {
        this.reservations.set(id, reservation);
        this.add(
          orgId,
          reservation.accounts,
          ZERO_AMOUNT,
          reservation.granted,
          1,
        );
      }
    }
    if (charge !== undefined) {
      this.costs?.insertNow(
        costEntry(
          orgId,
          runId,
          key,
          actual,
          usage ?? ZERO_USAGE,
          charge,
          this.now(),
          hold !== undefined,
        ),
      );
    }
    return this.settlement(held);
  }

  async reconcile(
    orgId: string,
    runId: string,
    key: string,
    actual: BudgetAmount,
    usage?: BudgetUsage,
    charge?: CostAttribution,
    fence?: number,
  ): Promise<BudgetSettlement> {
    assertAmount(actual);
    if (usage !== undefined) assertUsage(usage);
    this.expireNow(orgId);
    const held = this.find(orgId, runId, key, fence);
    if (held.state !== 'committed') {
      throw budgetRefused(
        'BUDGET_RESERVATION_SETTLED',
        'Only a committed reservation is reconciled',
      );
    }
    if (held.estimated !== true) return this.settlement(held);
    const before = this.path(orgId, runId).map(copy);
    const charged = held.charged ?? ZERO_AMOUNT;
    const difference = addAmounts(actual, charged, -1);
    this.add(orgId, held.accounts, difference, ZERO_AMOUNT);
    this.count(orgId, held.accounts, -1);
    const hold = this.reservations.get(
      budgetReservationId(orgId, runId, holdKey(key)),
    );
    if (hold !== undefined && hold.state === 'open') {
      this.add(orgId, hold.accounts, ZERO_AMOUNT, hold.granted);
      hold.state = 'released';
    }
    const was = held.usage ?? ZERO_USAGE;
    if (usage !== undefined) {
      const own = this.accounts.get(budgetAccountId(orgId, runId));
      if (own !== undefined)
        own.used = addUsage(subtractUsage(own.used ?? ZERO_USAGE, was), usage);
      held.usage = { ...usage };
    }
    held.estimated = false;
    held.charged = { ...actual };
    held.crossed = budgetCrossed(before, this.path(orgId, runId));
    if (charge !== undefined) {
      this.costs?.insertNow(
        reconcileEntry(
          orgId,
          runId,
          key,
          difference,
          usage === undefined ? ZERO_USAGE : subtractUsage(usage, was),
          this.chargedAs(charge, orgId, runId, key),
          this.now(),
        ),
      );
    }
    return this.settlement(held);
  }

  private chargedAs(
    charge: CostAttribution,
    orgId: string,
    runId: string,
    key: string,
  ): CostAttribution {
    const modelRole = this.costs?.roleNow(orgId, runId, key);
    return modelRole === undefined ? charge : { ...charge, modelRole };
  }

  async held(orgId: string, holder: BudgetHolder): Promise<HeldByScope> {
    const now = this.now();
    return heldByScope(
      [...this.reservations.values()].filter(
        (held) =>
          held.orgId === orgId &&
          held.hold === true &&
          held.state === 'open' &&
          held.expiresAt > now,
      ),
      holder,
    );
  }

  async release(
    orgId: string,
    runId: string,
    key: string,
    fence?: number,
  ): Promise<BudgetSettlement> {
    this.expireNow(orgId);
    const held = this.find(orgId, runId, key, fence);
    if (held.state === 'open') {
      this.add(orgId, held.accounts, ZERO_AMOUNT, held.granted);
      held.state = 'released';
    }
    return this.settlement(held);
  }

  async remaining(orgId: string, runId: string): Promise<BudgetRemaining> {
    this.expireNow(orgId);
    return this.remainingNow(orgId, runId);
  }

  async close(orgId: string, runId: string): Promise<BudgetAccount | null> {
    const found = this.accounts.get(budgetAccountId(orgId, runId));
    if (found === undefined) return null;
    found.closed = true;
    for (const held of this.reservations.values()) {
      if (held.orgId !== orgId || held.runId !== runId || held.state !== 'open')
        continue;
      // A hold is spend that may already have happened: it goes at its own time.
      if (held.hold === true) continue;
      this.add(orgId, held.accounts, ZERO_AMOUNT, held.granted);
      held.state = 'expired';
    }
    return copy(found);
  }

  /** All or nothing, as the Mongo store's transaction: a refusal leaves the ledger as it was. */
  admitChildNow(
    orgId: string,
    childRunId: string,
    budget: ChildBudgetAccount,
  ): void {
    const parent = budget.chain[budget.chain.length - 1];
    if (parent === undefined)
      throw new Error('A child account needs its parent');
    // Expired first, so the snapshot a refusal restores already lacks their holds.
    this.expireNow(orgId);
    const before = new Map(
      [...this.accounts].map(([id, account]) => [id, copy(account)]),
    );
    try {
      this.ensureNow(orgId, budget.chain);
      this.openChildNow(orgId, parent.runId, childRunId, budget.cap);
    } catch (error) {
      this.accounts.clear();
      before.forEach((account, id) => this.accounts.set(id, account));
      throw error;
    }
  }

  ensureNow(
    orgId: string,
    chain: readonly OpenBudgetAccount[],
    opening?: BudgetAmount,
  ): void {
    chain.forEach((open, index) => {
      const id = budgetAccountId(orgId, open.runId);
      if (this.accounts.has(id)) return;
      const parent =
        open.parentRunId === null
          ? null
          : (this.accounts.get(budgetAccountId(orgId, open.parentRunId)) ??
            null);
      if (open.parentRunId !== null && parent === null) {
        throw budgetRefused(
          'BUDGET_ACCOUNT_MISSING',
          "The run's parent has no budget account",
        );
      }
      const account = newBudgetAccount(orgId, open, parent, this.now());
      this.accounts.set(id, account);
      if (index === chain.length - 1 && opening !== undefined) {
        this.add(
          orgId,
          pathRunIds(account),
          assertAmount(opening),
          ZERO_AMOUNT,
        );
      }
    });
  }

  openChildNow(
    orgId: string,
    parentRunId: string,
    childRunId: string,
    cap: BudgetCap,
  ): void {
    this.expireNow(orgId);
    if (this.accounts.has(budgetAccountId(orgId, childRunId))) return;
    const path = this.path(orgId, parentRunId);
    this.assertOpen(path);
    const own = reserveForChild(budgetRemaining(path).available, cap);
    this.ensureNow(orgId, [{ runId: childRunId, parentRunId, cap: own }]);
  }

  reserveNow(command: ReserveBudget): BudgetReservation {
    assertAmount(command.requested);
    assertAmount(command.minimum);
    this.expireNow(command.orgId);
    const id = budgetReservationId(command.orgId, command.runId, command.key);
    const known = this.reservations.get(id);
    if (known !== undefined) {
      assertReservationOf(known, command.fence);
      return known;
    }
    const path = this.path(command.orgId, command.runId);
    this.assertOpen(path);
    const granted = grantReservation(path, command.requested, command.minimum);
    const now = this.now();
    const own = path[0] as BudgetAccount;
    const reservation: BudgetReservation = {
      orgId: command.orgId,
      runId: command.runId,
      rootRunId: own.rootRunId,
      key: command.key,
      accounts: pathRunIds(own),
      requested: { ...command.requested },
      granted,
      expiresAt: now + Math.min(command.ttlMs, MAX_RESERVATION_TTL_MS),
      state: 'open',
      createdAt: now,
      ...(command.fence !== undefined ? { fence: command.fence } : {}),
    };
    this.reservations.set(id, reservation);
    this.add(command.orgId, reservation.accounts, ZERO_AMOUNT, granted, 1);
    return reservation;
  }

  private remainingNow(orgId: string, runId: string): BudgetRemaining {
    return budgetRemaining(this.path(orgId, runId));
  }

  private settlement(held: BudgetReservation): BudgetSettlement {
    return {
      reservation: copy(held),
      charged: { ...(held.charged ?? ZERO_AMOUNT) },
      crossed: held.crossed ?? false,
      remaining: this.remainingNow(held.orgId, held.runId),
    };
  }

  private find(
    orgId: string,
    runId: string,
    key: string,
    fence: number | undefined,
  ): BudgetReservation {
    const held = this.reservations.get(budgetReservationId(orgId, runId, key));
    if (held === undefined || held.hold === true) {
      throw budgetRefused(
        'BUDGET_RESERVATION_MISSING',
        'No such reservation on this run',
      );
    }
    assertReservationOf(held, fence);
    return held;
  }

  /** The run's account first, then its ancestors nearest first. */
  private path(orgId: string, runId: string): BudgetAccount[] {
    const own = this.accounts.get(budgetAccountId(orgId, runId));
    if (own === undefined) {
      throw budgetRefused(
        'BUDGET_ACCOUNT_MISSING',
        'The run has no budget account',
      );
    }
    return pathRunIds(own).map(
      (id) => this.accounts.get(budgetAccountId(orgId, id)) as BudgetAccount,
    );
  }

  private assertOpen(path: readonly BudgetAccount[]): void {
    if (path.some((account) => account.closed)) {
      throw budgetRefused(
        'BUDGET_ACCOUNT_CLOSED',
        "The run's budget account is closed",
      );
    }
  }

  /** `committed += commit`, `reserved += sign × reserved` on every account of the path. */
  private add(
    orgId: string,
    runIds: readonly string[],
    commit: BudgetAmount,
    reserved: BudgetAmount,
    sign: 1 | -1 = -1,
  ): void {
    for (const runId of runIds) {
      const account = this.accounts.get(budgetAccountId(orgId, runId));
      if (account === undefined) continue;
      account.committed = addAmounts(account.committed, commit);
      account.reserved = addAmounts(account.reserved, reserved, sign);
    }
  }

  /** `estimated += by` on every account of the path. */
  private count(orgId: string, runIds: readonly string[], by: 1 | -1): void {
    for (const runId of runIds) {
      const account = this.accounts.get(budgetAccountId(orgId, runId));
      if (account !== undefined)
        account.estimated = (account.estimated ?? 0) + by;
    }
  }

  private expireNow(orgId: string): void {
    const now = this.now();
    for (const held of this.reservations.values()) {
      if (held.orgId !== orgId || held.state !== 'open' || held.expiresAt > now)
        continue;
      this.add(orgId, held.accounts, ZERO_AMOUNT, held.granted);
      held.state = 'expired';
    }
  }
}

import {
  addAmounts,
  assertAmount,
  assertReservationOf,
  assertUsage,
  BudgetAccount,
  BudgetAmount,
  BudgetCap,
  budgetCrossed,
  BudgetHold,
  BudgetHolder,
  budgetRefused,
  budgetRemaining,
  BudgetRemaining,
  BudgetReservation,
  BudgetSettlement,
  BudgetUsage,
  grantReservation,
  HeldByScope,
  heldByScope,
  holdKey,
  holdReservation,
  MAX_RESERVATION_TTL_MS,
  newBudgetAccount,
  OpenBudgetAccount,
  pathRunIds,
  reserveForChild,
  subtractUsage,
  USAGE_FIELDS,
  ZERO_AMOUNT,
  ZERO_USAGE,
} from '../../../../modules/agent-harness/domain/budget-ledger';
import {
  CostAttribution,
  costEntry,
  reconcileEntry,
} from '../../../../modules/agent-harness/domain/cost-ledger';
import type { CostEntryWriter } from '../../../../modules/agent-harness/ports/cost-ledger';
import type {
  BudgetLedgerStore,
  ChildBudgetAccount,
  ReserveBudget,
} from '../../../../modules/agent-harness/ports/budget-ledger';
import { PgTx } from '../../pg';
import type { Tx } from '../../uow';
import {
  decodeDoc,
  docParam,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import { PgTime, pgServerTime } from '../time/pg';

/** Expired reservations one operation returns at most; the next returns the rest. */
const EXPIRE_BATCH = 100;

/** `field = field + $n` on a numeric field of a jsonb object, for each named field. */
const incremented = (
  path: string,
  fields: readonly string[],
  first: number,
): string =>
  `jsonb_build_object(${fields
    .map(
      (field, index) =>
        `'${field}', coalesce((doc -> '${path}' ->> '${field}')::numeric, 0) + $${String(first + index)}::numeric`,
    )
    .join(', ')})`;

/**
 * The budget ledger on PostgreSQL (P6.5): `harness.budget_accounts` and `harness.budget_reservations`. Every operation
 * on a tree takes its root account's row lock first and holds it to commit, so the tree's operations run one at a time
 * on what the previous one committed: the root's cap holds under any number of concurrent reservers, the first of two
 * reservations of one key finds none and the second finds the first, and a settlement applies once. Operations of
 * different trees never wait on each other. Time is the database's (`PgTime`), read in the unit that decides.
 */
export class PgBudgetLedger implements BudgetLedgerStore {
  /** `costs`: where a commit writes its call's cost entry, in its own unit (C9.06); absent, none is written. */
  constructor(
    private readonly time: PgTime = pgServerTime,
    private readonly costs: CostEntryWriter<Tx> | null = null,
  ) {}

  account(orgId: string, runId: string): Promise<BudgetAccount | null> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'budget-ledger',
      { write: false },
      (k) => this.find(k, orgId, runId),
    );
  }

  async ensure(
    orgId: string,
    chain: readonly OpenBudgetAccount[],
    opening?: BudgetAmount,
  ): Promise<void> {
    await this.unit(orgId, (k, now) =>
      this.ensureIn(k, orgId, chain, now, opening),
    );
  }

  openRoot(
    orgId: string,
    runId: string,
    cap: BudgetCap,
    opening?: BudgetAmount,
  ): Promise<BudgetRemaining> {
    return this.unit(orgId, async (k, now) => {
      await this.ensureIn(
        k,
        orgId,
        [{ runId, parentRunId: null, cap }],
        now,
        opening,
      );
      await this.lockTree(k, orgId, runId);
      return budgetRemaining(await this.path(k, orgId, runId));
    });
  }

  openChild(
    orgId: string,
    parentRunId: string,
    childRunId: string,
    cap: BudgetCap,
  ): Promise<BudgetRemaining> {
    return this.unit(orgId, async (k, now) => {
      await this.openChildIn(k, orgId, parentRunId, childRunId, cap, now);
      return budgetRemaining(await this.path(k, orgId, childRunId));
    });
  }

  /** Inside child admission's unit (P6.5 decision 6): all or nothing with the child. */
  async admitChildIn(
    tx: Tx,
    orgId: string,
    childRunId: string,
    budget: ChildBudgetAccount,
    now: number,
  ): Promise<void> {
    const parent = budget.chain[budget.chain.length - 1];
    if (parent === undefined)
      throw new Error('A child account needs its parent');
    await orgOrJobsUnit(
      tx,
      orgId,
      'budget-ledger',
      { write: true },
      async (k) => {
        await this.ensureIn(k, orgId, budget.chain, now);
        await this.openChildIn(
          k,
          orgId,
          parent.runId,
          childRunId,
          budget.cap,
          now,
        );
      },
    );
  }

  reserve(
    command: ReserveBudget,
  ): Promise<{ reservation: BudgetReservation; remaining: BudgetRemaining }> {
    assertAmount(command.requested);
    assertAmount(command.minimum);
    return this.unit(command.orgId, async (k, now) => {
      const own = await this.ownAccount(k, command.orgId, command.runId);
      await this.lockTree(k, command.orgId, own.rootRunId);
      await this.expireIn(k, command.orgId, own.rootRunId, now);
      const known = await this.reservation(
        k,
        command.orgId,
        command.runId,
        command.key,
      );
      if (known !== null) {
        assertReservationOf(known, command.fence);
        return {
          reservation: known,
          remaining: budgetRemaining(
            await this.path(k, command.orgId, command.runId),
          ),
        };
      }
      const path = await this.path(k, command.orgId, command.runId);
      if (path.some((account) => account.closed)) {
        throw budgetRefused(
          'BUDGET_ACCOUNT_CLOSED',
          "The run's budget account is closed",
        );
      }
      const granted = grantReservation(
        path,
        command.requested,
        command.minimum,
      );
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
      await this.insertReservation(k, reservation);
      await this.add(
        k,
        command.orgId,
        reservation.accounts,
        ZERO_AMOUNT,
        granted,
      );
      return {
        reservation,
        remaining: budgetRemaining(
          path.map((account) => ({
            ...account,
            reserved: addAmounts(account.reserved, granted),
          })),
        ),
      };
    });
  }

  commit(
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
    return this.settle(orgId, runId, key, fence, async (k, held, now) => {
      if (held.state === 'released') {
        throw budgetRefused(
          'BUDGET_RESERVATION_SETTLED',
          'The reservation was released; nothing was charged',
        );
      }
      if (held.state === 'committed') return held;
      const before = await this.path(k, orgId, runId);
      const returned = held.state === 'open' ? held.granted : ZERO_AMOUNT;
      await this.add(
        k,
        orgId,
        held.accounts,
        actual,
        addAmounts(ZERO_AMOUNT, returned, -1),
      );
      const after = await this.path(k, orgId, runId);
      if (usage !== undefined) await this.use(k, orgId, runId, usage);
      if (hold !== undefined) {
        await this.count(k, orgId, held.accounts, 1);
        await this.holdIn(k, held, hold, now, charge);
      }
      if (charge !== undefined && this.costs !== null) {
        await this.costs.insertIn(
          new PgTx(k),
          costEntry(
            orgId,
            runId,
            key,
            actual,
            usage ?? ZERO_USAGE,
            charge,
            now,
            hold !== undefined,
          ),
        );
      }
      return {
        ...held,
        state: 'committed',
        charged: { ...actual },
        crossed: budgetCrossed(before, after),
        ...(usage !== undefined ? { usage: { ...usage } } : {}),
        ...(hold !== undefined ? { estimated: true } : {}),
      };
    });
  }

  reconcile(
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
    return this.settle(orgId, runId, key, fence, async (k, held, now) => {
      if (held.state !== 'committed') {
        throw budgetRefused(
          'BUDGET_RESERVATION_SETTLED',
          'Only a committed reservation is reconciled',
        );
      }
      if (held.estimated !== true) return held;
      const before = await this.path(k, orgId, runId);
      const difference = addAmounts(actual, held.charged ?? ZERO_AMOUNT, -1);
      await this.add(k, orgId, held.accounts, difference, ZERO_AMOUNT);
      await this.count(k, orgId, held.accounts, -1);
      const hold = await this.reservation(k, orgId, runId, holdKey(key));
      if (hold !== null && hold.state === 'open') {
        await this.add(
          k,
          orgId,
          hold.accounts,
          ZERO_AMOUNT,
          addAmounts(ZERO_AMOUNT, hold.granted, -1),
        );
        await this.replaceReservation(k, { ...hold, state: 'released' });
      }
      const was = held.usage ?? ZERO_USAGE;
      if (usage !== undefined)
        await this.use(k, orgId, runId, subtractUsage(usage, was));
      if (charge !== undefined && this.costs !== null) {
        const tx = new PgTx(k);
        const modelRole = await this.costs.roleIn(tx, orgId, runId, key);
        await this.costs.insertIn(
          tx,
          reconcileEntry(
            orgId,
            runId,
            key,
            difference,
            usage === undefined ? ZERO_USAGE : subtractUsage(usage, was),
            modelRole === undefined ? charge : { ...charge, modelRole },
            now,
          ),
        );
      }
      const after = await this.path(k, orgId, runId);
      return {
        ...held,
        charged: { ...actual },
        crossed: budgetCrossed(before, after),
        estimated: false,
        ...(usage !== undefined ? { usage: { ...usage } } : {}),
      };
    });
  }

  held(orgId: string, holder: BudgetHolder): Promise<HeldByScope> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'budget-ledger',
      { write: false },
      async (k) => {
        const now = await this.time.now(k);
        const { rows } = await k.query<{ doc: unknown }>(
          `/* pipeshub:harness.budget_reservations_holds */ SELECT doc FROM harness.budget_reservations
          WHERE org_id = $1 AND hold AND state = 'open' AND expires_at_ms > $2`,
          [orgId, now],
        );
        return heldByScope(
          rows.map((row) => decodeDoc<BudgetReservation>(row.doc)),
          holder,
        );
      },
    );
  }

  release(
    orgId: string,
    runId: string,
    key: string,
    fence?: number,
  ): Promise<BudgetSettlement> {
    return this.settle(orgId, runId, key, fence, async (k, held) => {
      if (held.state !== 'open') return held;
      await this.add(
        k,
        orgId,
        held.accounts,
        ZERO_AMOUNT,
        addAmounts(ZERO_AMOUNT, held.granted, -1),
      );
      return { ...held, state: 'released' };
    });
  }

  remaining(orgId: string, runId: string): Promise<BudgetRemaining> {
    return this.unit(orgId, async (k, now) => {
      const own = await this.ownAccount(k, orgId, runId);
      await this.lockTree(k, orgId, own.rootRunId);
      await this.expireIn(k, orgId, own.rootRunId, now);
      return budgetRemaining(await this.path(k, orgId, runId));
    });
  }

  /**
   * The run ended (CLEANUP-S4): its account takes no new reservation, and the reservations it still holds are expired,
   * so their hold leaves every account of the path at once. Expired, not released: a late commit still charges.
   */
  async close(orgId: string, runId: string): Promise<BudgetAccount | null> {
    return this.unit(orgId, async (k) => {
      const own = await this.find(k, orgId, runId);
      if (own === null) return null;
      await this.lockTree(k, orgId, own.rootRunId);
      await k.query(
        `/* pipeshub:harness.budget_accounts_close */ UPDATE harness.budget_accounts
            SET doc = jsonb_set(doc, '{closed}', 'true'::jsonb) WHERE org_id = $1 AND run_id = $2`,
        [orgId, runId],
      );
      // A hold is spend that may already have happened: it goes at its own time.
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.budget_reservations_of_run */ SELECT doc FROM harness.budget_reservations
          WHERE org_id = $1 AND run_id = $2 AND state = 'open' AND NOT hold ORDER BY key`,
        [orgId, runId],
      );
      for (const reservation of rows.map((row) =>
        decodeDoc<BudgetReservation>(row.doc),
      )) {
        await this.add(
          k,
          orgId,
          reservation.accounts,
          ZERO_AMOUNT,
          addAmounts(ZERO_AMOUNT, reservation.granted, -1),
        );
        await this.replaceReservation(k, { ...reservation, state: 'expired' });
      }
      return { ...own, closed: true };
    });
  }

  private unit<T>(
    orgId: string,
    work: (k: PgKernelTx, now: number) => Promise<T>,
  ): Promise<T> {
    return orgOrJobsUnit(
      undefined,
      orgId,
      'budget-ledger',
      { write: true },
      async (k) => work(k, await this.time.now(k)),
    );
  }

  private settle(
    orgId: string,
    runId: string,
    key: string,
    fence: number | undefined,
    apply: (
      k: PgKernelTx,
      held: BudgetReservation,
      now: number,
    ) => Promise<BudgetReservation>,
  ): Promise<BudgetSettlement> {
    return this.unit(orgId, async (k, now) => {
      const found = await this.reservation(k, orgId, runId, key);
      if (found === null || found.hold === true) {
        throw budgetRefused(
          'BUDGET_RESERVATION_MISSING',
          'No such reservation on this run',
        );
      }
      assertReservationOf(found, fence);
      await this.lockTree(k, orgId, found.rootRunId);
      await this.expireIn(k, orgId, found.rootRunId, now);
      const held = (await this.reservation(k, orgId, runId, key)) ?? found;
      const settled = await apply(k, held, now);
      if (settled !== held) await this.replaceReservation(k, settled);
      return {
        reservation: settled,
        charged: { ...(settled.charged ?? ZERO_AMOUNT) },
        crossed: settled.crossed ?? false,
        remaining: budgetRemaining(await this.path(k, orgId, runId)),
      };
    });
  }

  /** The tree's lock: its root account's row, held to commit. */
  private async lockTree(
    k: PgKernelTx,
    orgId: string,
    rootRunId: string,
  ): Promise<void> {
    await k.query(
      `/* pipeshub:harness.budget_accounts_lock_tree */ SELECT 1 FROM harness.budget_accounts
        WHERE org_id = $1 AND run_id = $2 FOR UPDATE`,
      [orgId, rootRunId],
    );
  }

  private async find(
    k: PgKernelTx,
    orgId: string,
    runId: string,
  ): Promise<BudgetAccount | null> {
    const { rows } = await k.query<{ doc: unknown }>(
      `/* pipeshub:harness.budget_accounts_get */ SELECT doc FROM harness.budget_accounts WHERE org_id = $1 AND run_id = $2`,
      [orgId, runId],
    );
    return rows[0] === undefined ? null : decodeDoc<BudgetAccount>(rows[0].doc);
  }

  private async ownAccount(
    k: PgKernelTx,
    orgId: string,
    runId: string,
  ): Promise<BudgetAccount> {
    const found = await this.find(k, orgId, runId);
    if (found === null)
      throw budgetRefused(
        'BUDGET_ACCOUNT_MISSING',
        'The run has no budget account',
      );
    return found;
  }

  /** The run's account first, then its ancestors nearest first. */
  private async path(
    k: PgKernelTx,
    orgId: string,
    runId: string,
  ): Promise<BudgetAccount[]> {
    const own = await this.ownAccount(k, orgId, runId);
    const ids = pathRunIds(own);
    const { rows } = await k.query<{ run_id: string; doc: unknown }>(
      `/* pipeshub:harness.budget_accounts_path */ SELECT run_id, doc FROM harness.budget_accounts
        WHERE org_id = $1 AND run_id = ANY($2::text[])`,
      [orgId, ids],
    );
    const byId = new Map(
      rows.map((row) => [row.run_id, decodeDoc<BudgetAccount>(row.doc)]),
    );
    return ids.map((id) => {
      const account = byId.get(id);
      if (account === undefined)
        throw budgetRefused(
          'BUDGET_ACCOUNT_MISSING',
          "An ancestor's budget account is missing",
        );
      return account;
    });
  }

  private async ensureIn(
    k: PgKernelTx,
    orgId: string,
    chain: readonly OpenBudgetAccount[],
    now: number,
    opening?: BudgetAmount,
  ): Promise<void> {
    for (const [index, open] of chain.entries()) {
      if ((await this.find(k, orgId, open.runId)) !== null) continue;
      let parent: BudgetAccount | null = null;
      if (open.parentRunId !== null) {
        parent = await this.find(k, orgId, open.parentRunId);
        if (parent === null)
          throw budgetRefused(
            'BUDGET_ACCOUNT_MISSING',
            "The run's parent has no budget account",
          );
      }
      const account = newBudgetAccount(orgId, open, parent, now);
      const { rowCount } = await k.query(
        `/* pipeshub:harness.budget_accounts_open */ INSERT INTO harness.budget_accounts
           (org_id, run_id, root_run_id, doc, region) VALUES ($1, $2, $3, $4::jsonb, $5) ON CONFLICT DO NOTHING`,
        [
          orgId,
          account.runId,
          account.rootRunId,
          docParam(account),
          rowRegion(),
        ],
      );
      // A concurrent opener's account is the one: its opening, not this one's, was added.
      if (rowCount !== 1) continue;
      if (index === chain.length - 1 && opening !== undefined) {
        await this.lockTree(k, orgId, account.rootRunId);
        await this.add(
          k,
          orgId,
          pathRunIds(account),
          assertAmount(opening),
          ZERO_AMOUNT,
        );
      }
    }
  }

  private async openChildIn(
    k: PgKernelTx,
    orgId: string,
    parentRunId: string,
    childRunId: string,
    cap: BudgetCap,
    now: number,
  ): Promise<void> {
    if ((await this.find(k, orgId, childRunId)) !== null) return;
    const parentAccount = await this.ownAccount(k, orgId, parentRunId);
    await this.lockTree(k, orgId, parentAccount.rootRunId);
    // Under the tree's lock: a concurrent opening of the same child has committed or not started.
    if ((await this.find(k, orgId, childRunId)) !== null) return;
    await this.expireIn(k, orgId, parentAccount.rootRunId, now);
    const path = await this.path(k, orgId, parentRunId);
    if (path.some((account) => account.closed)) {
      throw budgetRefused(
        'BUDGET_ACCOUNT_CLOSED',
        "The run's budget account is closed",
      );
    }
    const own = reserveForChild(budgetRemaining(path).available, cap);
    await this.ensureIn(
      k,
      orgId,
      [{ runId: childRunId, parentRunId, cap: own }],
      now,
    );
  }

  private async reservation(
    k: PgKernelTx,
    orgId: string,
    runId: string,
    key: string,
  ): Promise<BudgetReservation | null> {
    const { rows } = await k.query<{ doc: unknown }>(
      `/* pipeshub:harness.budget_reservations_get */ SELECT doc FROM harness.budget_reservations
        WHERE org_id = $1 AND run_id = $2 AND key = $3`,
      [orgId, runId, key],
    );
    return rows[0] === undefined
      ? null
      : decodeDoc<BudgetReservation>(rows[0].doc);
  }

  private async insertReservation(
    k: PgKernelTx,
    reservation: BudgetReservation,
  ): Promise<void> {
    await k.query(
      `/* pipeshub:harness.budget_reservations_insert */ INSERT INTO harness.budget_reservations
         (org_id, run_id, key, root_run_id, state, hold, expires_at_ms, doc, region)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)`,
      [
        reservation.orgId,
        reservation.runId,
        reservation.key,
        reservation.rootRunId,
        reservation.state,
        reservation.hold === true,
        reservation.expiresAt,
        docParam(reservation),
        rowRegion(),
      ],
    );
  }

  private async replaceReservation(
    k: PgKernelTx,
    reservation: BudgetReservation,
  ): Promise<void> {
    await k.query(
      `/* pipeshub:harness.budget_reservations_replace */ UPDATE harness.budget_reservations
          SET state = $4, expires_at_ms = $5, doc = $6::jsonb WHERE org_id = $1 AND run_id = $2 AND key = $3`,
      [
        reservation.orgId,
        reservation.runId,
        reservation.key,
        reservation.state,
        reservation.expiresAt,
        docParam(reservation),
      ],
    );
  }

  /** The hold of an estimated commit (WP-66): reserved on the path past any cap, in the commit's unit. */
  private async holdIn(
    k: PgKernelTx,
    held: BudgetReservation,
    hold: BudgetHold,
    now: number,
    charge?: CostAttribution,
  ): Promise<void> {
    if (hold.amount.usdMicros === 0 && hold.amount.tokens === 0) return;
    const reservation = holdReservation(
      held,
      hold,
      now,
      charge === undefined
        ? undefined
        : { userId: charge.userId, agentKey: charge.agentKey },
    );
    await this.insertReservation(k, reservation);
    await this.add(
      k,
      held.orgId,
      reservation.accounts,
      ZERO_AMOUNT,
      reservation.granted,
    );
  }

  /** `estimated += by` on each account of the path. */
  private async count(
    k: PgKernelTx,
    orgId: string,
    runIds: readonly string[],
    by: 1 | -1,
  ): Promise<void> {
    await k.query(
      `/* pipeshub:harness.budget_accounts_estimated */ UPDATE harness.budget_accounts
          SET doc = jsonb_set(doc, '{estimated}', to_jsonb(coalesce((doc ->> 'estimated')::numeric, 0) + $3::numeric))
        WHERE org_id = $1 AND run_id = ANY($2::text[])`,
      [orgId, [...runIds], by],
    );
  }

  /** The run's own `used += usage`. */
  private async use(
    k: PgKernelTx,
    orgId: string,
    runId: string,
    usage: BudgetUsage,
  ): Promise<void> {
    await k.query(
      `/* pipeshub:harness.budget_accounts_used */ UPDATE harness.budget_accounts
          SET doc = doc || jsonb_build_object('used', ${incremented('used', USAGE_FIELDS, 3)})
        WHERE org_id = $1 AND run_id = $2`,
      [orgId, runId, ...USAGE_FIELDS.map((field) => usage[field])],
    );
  }

  /** `committed += commit`, `reserved += reserved` on each account (reserved may be negative). */
  private async add(
    k: PgKernelTx,
    orgId: string,
    runIds: readonly string[],
    commit: BudgetAmount,
    reserved: BudgetAmount,
  ): Promise<void> {
    await k.query(
      `/* pipeshub:harness.budget_accounts_add */ UPDATE harness.budget_accounts
          SET doc = doc || jsonb_build_object(
            'committed', ${incremented('committed', ['usdMicros', 'tokens'], 3)},
            'reserved', ${incremented('reserved', ['usdMicros', 'tokens'], 5)})
        WHERE org_id = $1 AND run_id = ANY($2::text[])`,
      [
        orgId,
        [...runIds],
        commit.usdMicros,
        commit.tokens,
        reserved.usdMicros,
        reserved.tokens,
      ],
    );
  }

  private async expireIn(
    k: PgKernelTx,
    orgId: string,
    rootRunId: string,
    now: number,
  ): Promise<void> {
    const { rows } = await k.query<{ doc: unknown }>(
      `/* pipeshub:harness.budget_reservations_due */ SELECT doc FROM harness.budget_reservations
        WHERE org_id = $1 AND root_run_id = $2 AND state = 'open' AND expires_at_ms <= $3
        ORDER BY expires_at_ms, run_id, key LIMIT $4`,
      [orgId, rootRunId, now, EXPIRE_BATCH],
    );
    for (const held of rows.map((row) =>
      decodeDoc<BudgetReservation>(row.doc),
    )) {
      await this.add(
        k,
        orgId,
        held.accounts,
        ZERO_AMOUNT,
        addAmounts(ZERO_AMOUNT, held.granted, -1),
      );
      await this.replaceReservation(k, { ...held, state: 'expired' });
    }
  }
}

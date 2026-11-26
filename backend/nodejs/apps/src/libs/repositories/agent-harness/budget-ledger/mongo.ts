import { mongoTx } from '../durable-transaction/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import {
  addAmounts,
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
  holdKey,
  holdReservation,
  budgetRefused,
  budgetRemaining,
  BudgetRemaining,
  BudgetReservation,
  budgetReservationId,
  BudgetSettlement,
  BudgetUsage,
  grantReservation,
  MAX_RESERVATION_TTL_MS,
  newBudgetAccount,
  OpenBudgetAccount,
  pathRunIds,
  reserveForChild,
  subtractUsage,
  ZERO_AMOUNT,
  ZERO_USAGE,
} from '../../../../modules/agent-harness/domain/budget-ledger';
import {
  CostAttribution,
  costEntry,
  reconcileEntry,
} from '../../../../modules/agent-harness/domain/cost-ledger';
import type { CostEntryWriter } from '../../../../modules/agent-harness/ports/cost-ledger';
import {
  BudgetLedgerStore,
  ChildBudgetAccount,
  ReserveBudget,
} from '../../../../modules/agent-harness/ports/budget-ledger';
import {
  AgentBudgetAccountModel,
  AgentBudgetReservationModel,
} from './schema/budget-ledger.schema';
import { inMongoTransaction } from '../durable-transaction/mongo';
import { MongoTime, mongoServerTime } from '../time/mongo';

/** Expired reservations one operation returns at most; the next returns the rest. */
const EXPIRE_BATCH = 100;
const DUPLICATE_KEY = 11000;

const strip = <T extends { _id?: unknown }>(document: T): Omit<T, '_id'> => {
  const { _id: _ignored, ...rest } = document;
  return rest;
};

/** The reservations a closed run's account expires: every open one but its holds. */
const openReservationsOf = (
  orgId: string,
  runId: string,
): { orgId: string; runId: string; state: 'open'; hold: { $ne: true } } => ({
  orgId,
  runId,
  state: 'open' as const,
  hold: { $ne: true },
});

const isDuplicateKey = (error: unknown): boolean =>
  typeof error === 'object' &&
  error !== null &&
  (error as { code?: unknown }).code === DUPLICATE_KEY;

/**
 * The ledger on a replica set (P6.5). Every operation is one durable
 * transaction on DB time. A reservation and a settlement write every account
 * of the run's path, the root included, so two concurrent operations of one
 * tree write-conflict and one retries on what the other committed: the root's
 * cap holds without a lock. A child's account is written in the transaction
 * that admits the child (`admitChildIn`).
 */
export class MongoBudgetLedger implements BudgetLedgerStore {
  /** `costs`: where a commit writes its call's cost entry, in its own transaction (C9.06); absent, none is written. */
  constructor(
    private readonly time: MongoTime = mongoServerTime,
    private readonly costs: CostEntryWriter<Tx> | null = null,
  ) {}

  async account(orgId: string, runId: string): Promise<BudgetAccount | null> {
    const found = await AgentBudgetAccountModel.findById(
      budgetAccountId(orgId, runId),
    )
      .read('primary')
      .lean();
    return found === null ? null : strip(found);
  }

  async ensure(
    orgId: string,
    chain: readonly OpenBudgetAccount[],
    opening?: BudgetAmount,
  ): Promise<void> {
    await this.transaction(async (session, now) => {
      await this.ensureIn(session, orgId, chain, now, opening);
    });
  }

  async openRoot(
    orgId: string,
    runId: string,
    cap: BudgetCap,
    opening?: BudgetAmount,
  ): Promise<BudgetRemaining> {
    return this.transaction(async (session, now) => {
      await this.ensureIn(
        session,
        orgId,
        [{ runId, parentRunId: null, cap }],
        now,
        opening,
      );
      return budgetRemaining(await this.path(session, orgId, runId));
    });
  }

  async openChild(
    orgId: string,
    parentRunId: string,
    childRunId: string,
    cap: BudgetCap,
  ): Promise<BudgetRemaining> {
    return this.transaction(async (session, now) => {
      await this.openChildIn(session, orgId, parentRunId, childRunId, cap, now);
      return budgetRemaining(await this.path(session, orgId, childRunId));
    });
  }

  /** Inside child admission's transaction (P6.5 decision 6): all or nothing with the child. */
  async admitChildIn(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    childRunId: string,
    budget: ChildBudgetAccount,
    now: number,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    const parent = budget.chain[budget.chain.length - 1];
    if (parent === undefined)
      throw new Error('A child account needs its parent');
    await this.ensureIn(session, orgId, budget.chain, now);
    await this.openChildIn(
      session,
      orgId,
      parent.runId,
      childRunId,
      budget.cap,
      now,
    );
  }

  async reserve(
    command: ReserveBudget,
  ): Promise<{ reservation: BudgetReservation; remaining: BudgetRemaining }> {
    assertAmount(command.requested);
    assertAmount(command.minimum);
    return this.transaction(async (session, now) => {
      const id = budgetReservationId(command.orgId, command.runId, command.key);
      const own = await this.ownAccount(session, command.orgId, command.runId);
      await this.expireIn(session, command.orgId, own.rootRunId, now);
      const known = await AgentBudgetReservationModel.findById(id)
        .session(session)
        .lean();
      if (known !== null) {
        assertReservationOf(known, command.fence);
        return {
          reservation: strip(known),
          remaining: budgetRemaining(
            await this.path(session, command.orgId, command.runId),
          ),
        };
      }
      const path = await this.path(session, command.orgId, command.runId);
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
      await AgentBudgetReservationModel.create([{ _id: id, ...reservation }], {
        session,
      });
      await this.add(
        session,
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
    return this.settle(orgId, runId, key, fence, async (session, held, now) => {
      if (held.state === 'released') {
        throw budgetRefused(
          'BUDGET_RESERVATION_SETTLED',
          'The reservation was released; nothing was charged',
        );
      }
      if (held.state === 'committed') return held;
      const before = await this.path(session, orgId, runId);
      const returned = held.state === 'open' ? held.granted : ZERO_AMOUNT;
      await this.add(
        session,
        orgId,
        held.accounts,
        actual,
        addAmounts(ZERO_AMOUNT, returned, -1),
      );
      const after = await this.path(session, orgId, runId);
      if (usage !== undefined) {
        await AgentBudgetAccountModel.updateOne(
          { _id: budgetAccountId(orgId, runId) },
          {
            $inc: {
              'used.inputTokens': usage.inputTokens,
              'used.outputTokens': usage.outputTokens,
              'used.cacheReadTokens': usage.cacheReadTokens,
              'used.cacheWriteTokens': usage.cacheWriteTokens,
            },
          },
          { session },
        );
      }
      if (hold !== undefined) {
        await this.count(session, orgId, held.accounts, 1);
        await this.holdIn(session, held, hold, now, charge);
      }
      if (charge !== undefined && this.costs !== null) {
        await this.costs.insertIn(
          mongoTx(session),
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
    return this.settle(orgId, runId, key, fence, async (session, held, now) => {
      if (held.state !== 'committed') {
        throw budgetRefused(
          'BUDGET_RESERVATION_SETTLED',
          'Only a committed reservation is reconciled',
        );
      }
      if (held.estimated !== true) return held;
      const before = await this.path(session, orgId, runId);
      const difference = addAmounts(actual, held.charged ?? ZERO_AMOUNT, -1);
      await this.add(session, orgId, held.accounts, difference, ZERO_AMOUNT);
      await this.count(session, orgId, held.accounts, -1);
      const holdId = budgetReservationId(orgId, runId, holdKey(key));
      const hold = await AgentBudgetReservationModel.findOne({
        _id: holdId,
        state: 'open',
      })
        .session(session)
        .lean();
      if (hold !== null) {
        await this.add(
          session,
          orgId,
          hold.accounts,
          ZERO_AMOUNT,
          addAmounts(ZERO_AMOUNT, hold.granted, -1),
        );
        await AgentBudgetReservationModel.updateOne(
          { _id: holdId, state: 'open' },
          { $set: { state: 'released' } },
          { session },
        );
      }
      const was = held.usage ?? ZERO_USAGE;
      if (usage !== undefined) {
        const change = subtractUsage(usage, was);
        await AgentBudgetAccountModel.updateOne(
          { _id: budgetAccountId(orgId, runId) },
          {
            $inc: {
              'used.inputTokens': change.inputTokens,
              'used.outputTokens': change.outputTokens,
              'used.cacheReadTokens': change.cacheReadTokens,
              'used.cacheWriteTokens': change.cacheWriteTokens,
            },
          },
          { session },
        );
      }
      if (charge !== undefined && this.costs !== null) {
        const modelRole = await this.costs.roleIn(
          mongoTx(session),
          orgId,
          runId,
          key,
        );
        await this.costs.insertIn(
          mongoTx(session),
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
      const after = await this.path(session, orgId, runId);
      return {
        ...held,
        charged: { ...actual },
        crossed: budgetCrossed(before, after),
        estimated: false,
        ...(usage !== undefined ? { usage: { ...usage } } : {}),
      };
    });
  }

  async held(orgId: string, holder: BudgetHolder): Promise<HeldByScope> {
    const now = await this.time.read();
    const holds = await AgentBudgetReservationModel.find({
      orgId,
      hold: true,
      state: 'open',
      expiresAt: { $gt: now },
    })
      .read('primary')
      .lean();
    return heldByScope(
      holds.map((hold) => strip(hold) as BudgetReservation),
      holder,
    );
  }

  async release(
    orgId: string,
    runId: string,
    key: string,
    fence?: number,
  ): Promise<BudgetSettlement> {
    return this.settle(orgId, runId, key, fence, async (session, held) => {
      if (held.state !== 'open') return held;
      await this.add(
        session,
        orgId,
        held.accounts,
        ZERO_AMOUNT,
        addAmounts(ZERO_AMOUNT, held.granted, -1),
      );
      return { ...held, state: 'released' };
    });
  }

  async remaining(orgId: string, runId: string): Promise<BudgetRemaining> {
    return this.transaction(async (session, now) => {
      const own = await this.ownAccount(session, orgId, runId);
      await this.expireIn(session, orgId, own.rootRunId, now);
      return budgetRemaining(await this.path(session, orgId, runId));
    });
  }

  /**
   * The run ended (CLEANUP-S4): its account takes no new reservation, and the
   * reservations it still holds are expired, so their hold leaves every
   * account of the path at once instead of at the tree's next operation (an
   * ended tree has none). Expired, not released: a late commit still charges.
   */
  async close(orgId: string, runId: string): Promise<BudgetAccount | null> {
    // Outside a transaction (WP88-FU-4): one guarded write of one document is atomic, and a reservation's
    // transaction writes this account too, so it either committed before the close (and the read below finds it)
    // or conflicts with it and, retried, finds the account closed. A transaction, its clock read and its commit
    // are left for a run that still reserves something, which an ended run rarely does.
    const closed = await AgentBudgetAccountModel.findOneAndUpdate(
      { _id: budgetAccountId(orgId, runId) },
      { $set: { closed: true } },
      { returnDocument: 'after' },
    ).lean();
    if (closed === null) return null;
    const reserving = await AgentBudgetReservationModel.exists(
      openReservationsOf(orgId, runId),
    ).read('primary');
    if (reserving !== null) {
      await this.transaction(async (session) => {
        // A hold is spend that may already have happened: it goes at its own time.
        const held = await AgentBudgetReservationModel.find(
          openReservationsOf(orgId, runId),
        )
          .session(session)
          .lean();
        for (const reservation of held) {
          await this.add(
            session,
            orgId,
            reservation.accounts,
            ZERO_AMOUNT,
            addAmounts(ZERO_AMOUNT, reservation.granted, -1),
          );
          await AgentBudgetReservationModel.updateOne(
            { _id: reservation._id, state: 'open' },
            { $set: { state: 'expired' } },
            { session },
          );
        }
      });
    }
    return strip(closed);
  }

  private async settle(
    orgId: string,
    runId: string,
    key: string,
    fence: number | undefined,
    apply: (
      session: ClientSession,
      held: BudgetReservation,
      now: number,
    ) => Promise<BudgetReservation>,
  ): Promise<BudgetSettlement> {
    return this.transaction(async (session, now) => {
      const id = budgetReservationId(orgId, runId, key);
      const found = await AgentBudgetReservationModel.findById(id)
        .session(session)
        .lean();
      if (found === null || found.hold === true) {
        throw budgetRefused(
          'BUDGET_RESERVATION_MISSING',
          'No such reservation on this run',
        );
      }
      assertReservationOf(found, fence);
      await this.expireIn(session, orgId, found.rootRunId, now);
      const current = await AgentBudgetReservationModel.findById(id)
        .session(session)
        .lean();
      const held = strip(current ?? found) as BudgetReservation;
      const settled = await apply(session, held, now);
      if (settled !== held) {
        await AgentBudgetReservationModel.replaceOne(
          { _id: id },
          { _id: id, ...settled },
          { session },
        );
      }
      return {
        reservation: settled,
        charged: { ...(settled.charged ?? ZERO_AMOUNT) },
        crossed: settled.crossed ?? false,
        remaining: budgetRemaining(await this.path(session, orgId, runId)),
      };
    });
  }

  private async transaction<T>(
    work: (session: ClientSession, now: number) => Promise<T>,
  ): Promise<T> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await inMongoTransaction(async (session) =>
          work(session, await this.time.read()),
        );
      } catch (error) {
        // Two first reservations of one key (or two first openings of one run): the
        // loser's insert fails; asked again it finds the winner's document.
        if (!isDuplicateKey(error) || attempt >= 2) throw error;
      }
    }
  }

  private async ensureIn(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    chain: readonly OpenBudgetAccount[],
    now: number,
    opening?: BudgetAmount,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    for (const [index, open] of chain.entries()) {
      const id = budgetAccountId(orgId, open.runId);
      if (
        (await AgentBudgetAccountModel.findById(id).session(session).lean()) !==
        null
      )
        continue;
      let parent: BudgetAccount | null = null;
      if (open.parentRunId !== null) {
        const found = await AgentBudgetAccountModel.findById(
          budgetAccountId(orgId, open.parentRunId),
        )
          .session(session)
          .lean();
        if (found === null) {
          throw budgetRefused(
            'BUDGET_ACCOUNT_MISSING',
            "The run's parent has no budget account",
          );
        }
        parent = strip(found);
      }
      const account = newBudgetAccount(orgId, open, parent, now);
      await AgentBudgetAccountModel.create([{ _id: id, ...account }], {
        session,
      });
      if (index === chain.length - 1 && opening !== undefined) {
        await this.add(
          session,
          orgId,
          pathRunIds(account),
          assertAmount(opening),
          ZERO_AMOUNT,
        );
      }
    }
  }

  private async openChildIn(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    parentRunId: string,
    childRunId: string,
    cap: BudgetCap,
    now: number,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    const existing = await AgentBudgetAccountModel.findById(
      budgetAccountId(orgId, childRunId),
    )
      .session(session)
      .lean();
    if (existing !== null) return;
    const parentAccount = await this.ownAccount(session, orgId, parentRunId);
    await this.expireIn(session, orgId, parentAccount.rootRunId, now);
    const path = await this.path(session, orgId, parentRunId);
    if (path.some((account) => account.closed)) {
      throw budgetRefused(
        'BUDGET_ACCOUNT_CLOSED',
        "The run's budget account is closed",
      );
    }
    const own = reserveForChild(budgetRemaining(path).available, cap);
    await this.ensureIn(
      session,
      orgId,
      [{ runId: childRunId, parentRunId, cap: own }],
      now,
    );
  }

  private async ownAccount(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    runId: string,
  ): Promise<BudgetAccount> {
    const session = asMongoSession(sessionHandle);
    const found = await AgentBudgetAccountModel.findById(
      budgetAccountId(orgId, runId),
    )
      .session(session)
      .lean();
    if (found === null) {
      throw budgetRefused(
        'BUDGET_ACCOUNT_MISSING',
        'The run has no budget account',
      );
    }
    return strip(found);
  }

  /** The run's account first, then its ancestors nearest first. */
  private async path(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    runId: string,
  ): Promise<BudgetAccount[]> {
    const session = asMongoSession(sessionHandle);
    const own = await this.ownAccount(session, orgId, runId);
    const ids = pathRunIds(own).map((id) => budgetAccountId(orgId, id));
    const found = await AgentBudgetAccountModel.find({ _id: { $in: ids } })
      .session(session)
      .lean();
    const byId = new Map(
      found.map((document) => [document._id, strip(document) as BudgetAccount]),
    );
    return ids.map((id) => {
      const account = byId.get(id);
      if (account === undefined) {
        throw budgetRefused(
          'BUDGET_ACCOUNT_MISSING',
          "An ancestor's budget account is missing",
        );
      }
      return account;
    });
  }

  /** The hold of an estimated commit (WP-66): reserved on the path past any cap, in the commit's transaction. */
  private async holdIn(
    sessionHandle: Tx | ClientSession,
    held: BudgetReservation,
    hold: BudgetHold,
    now: number,
    charge?: CostAttribution,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    if (hold.amount.usdMicros === 0 && hold.amount.tokens === 0) return;
    const reservation = holdReservation(
      held,
      hold,
      now,
      charge === undefined
        ? undefined
        : { userId: charge.userId, agentKey: charge.agentKey },
    );
    const id = budgetReservationId(held.orgId, held.runId, reservation.key);
    await AgentBudgetReservationModel.create([{ _id: id, ...reservation }], {
      session,
    });
    await this.add(
      session,
      held.orgId,
      reservation.accounts,
      ZERO_AMOUNT,
      reservation.granted,
    );
  }

  /** `estimated += by` on each account of the path. */
  private async count(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    runIds: readonly string[],
    by: 1 | -1,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    await AgentBudgetAccountModel.updateMany(
      { _id: { $in: runIds.map((id) => budgetAccountId(orgId, id)) } },
      { $inc: { estimated: by } },
      { session },
    );
  }

  /** `committed += commit`, `reserved += reserved` on each account (reserved may be negative). */
  private async add(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    runIds: readonly string[],
    commit: BudgetAmount,
    reserved: BudgetAmount,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    await AgentBudgetAccountModel.updateMany(
      { _id: { $in: runIds.map((id) => budgetAccountId(orgId, id)) } },
      {
        $inc: {
          'committed.usdMicros': commit.usdMicros,
          'committed.tokens': commit.tokens,
          'reserved.usdMicros': reserved.usdMicros,
          'reserved.tokens': reserved.tokens,
        },
      },
      { session },
    );
  }

  private async expireIn(
    sessionHandle: Tx | ClientSession,
    orgId: string,
    rootRunId: string,
    now: number,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    const due = await AgentBudgetReservationModel.find({
      orgId,
      rootRunId,
      state: 'open',
      expiresAt: { $lte: now },
    })
      .limit(EXPIRE_BATCH)
      .session(session)
      .lean();
    for (const held of due) {
      await this.add(
        session,
        orgId,
        held.accounts,
        ZERO_AMOUNT,
        addAmounts(ZERO_AMOUNT, held.granted, -1),
      );
      await AgentBudgetReservationModel.updateOne(
        { _id: held._id, state: 'open' },
        { $set: { state: 'expired' } },
        { session },
      );
    }
  }
}

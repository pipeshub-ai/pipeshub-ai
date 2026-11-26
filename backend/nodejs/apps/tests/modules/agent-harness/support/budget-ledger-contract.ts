/**
 * P6.5 NL: what every `BudgetLedgerStore` must do, run on the in-memory store
 * (unit) and on Mongo (replica-set itest): the shared table
 * (`contracts/harness/vectors/budget-ledger.json`, the same one Python's
 * reference ledger runs) and parallel reservations across a tree.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  BudgetAmount,
  BudgetCap,
  BudgetRemaining,
  BudgetSettlement,
  BudgetUsage,
} from '../../../../src/modules/agent-harness/domain/budget-ledger';
import { HarnessConflictError } from '../../../../src/modules/agent-harness/domain/errors';
import { BudgetLedgerStore } from '../../../../src/modules/agent-harness/ports/budget-ledger';

export interface BudgetLedgerUnderTest {
  ledger: BudgetLedgerStore;
  advance(seconds: number): void;
}

interface Wire {
  usd: number | null;
  tokens: number | null;
}

interface Step {
  op: string;
  run?: string;
  parent?: string;
  key?: string;
  cap?: Wire;
  requested?: Wire;
  minimum?: Wire;
  actual?: Wire;
  usage?: { input: number; output: number; cacheRead: number; cacheWrite: number };
  opening?: Wire;
  hold?: Wire;
  holdTtlS?: number;
  ttlS?: number;
  seconds?: number;
  expect?: Record<string, unknown>;
}

interface Scenario {
  id: string;
  steps: Step[];
}

const VECTORS = join(
  __dirname,
  '../../../../../../../contracts/harness/vectors/budget-ledger.json',
);

export const budgetScenarios = (): Scenario[] =>
  (JSON.parse(readFileSync(VECTORS, 'utf-8')) as { scenarios: Scenario[] })
    .scenarios;

/** In the shape every store takes (PostgreSQL checks it). */
export const ORG = 'b0d9e7000000000000000001';
const amount = (wire: Wire | undefined): BudgetAmount => ({
  usdMicros: wire?.usd ?? 0,
  tokens: wire?.tokens ?? 0,
});
const cap = (wire: Wire | undefined): BudgetCap => ({
  usdMicros: wire?.usd ?? null,
  tokens: wire?.tokens ?? null,
});
const wire = (value: BudgetCap): Wire => ({
  usd: value.usdMicros,
  tokens: value.tokens,
});

export async function budgetCode(work: Promise<unknown>): Promise<string> {
  try {
    await work;
  } catch (error) {
    if (error instanceof HarnessConflictError) return error.code;
    throw error;
  }
  return 'OK';
}

const seenRemaining = (left: BudgetRemaining): Record<string, unknown> => ({
  available: wire(left.available),
  committed: wire(left.committed),
  reserved: wire(left.reserved),
  exhausted: left.exhausted,
  estimated: left.estimated,
  used: {
    input: left.used.inputTokens,
    output: left.used.outputTokens,
    cacheRead: left.used.cacheReadTokens,
    cacheWrite: left.used.cacheWriteTokens,
  },
});

const seenSettlement = (settled: BudgetSettlement): Record<string, unknown> => ({
  charged: wire(settled.charged),
  crossed: settled.crossed,
  state: settled.reservation.state,
});

const usageOf = (step: Step): BudgetUsage | undefined =>
  step.usage === undefined
    ? undefined
    : {
        inputTokens: step.usage.input,
        outputTokens: step.usage.output,
        cacheReadTokens: step.usage.cacheRead,
        cacheWriteTokens: step.usage.cacheWrite,
      };

/** Runs one scenario; run ids are prefixed so scenarios never share accounts. */
export async function runBudgetScenario(
  subject: BudgetLedgerUnderTest,
  scenario: Scenario,
): Promise<void> {
  const scope = scenario.id.split(' ')[0] ?? scenario.id;
  const run = (name: string | undefined): string => `${scope}-${name ?? ''}`;
  const pins = new Map<string, { parent: string | null; cap: BudgetCap }>();
  const { ledger } = subject;
  for (const [index, step] of scenario.steps.entries()) {
    const where = `${scenario.id} step ${String(index)} (${step.op})`;
    const expected = step.expect ?? {};
    let seen: Record<string, unknown> | null = null;
    try {
      switch (step.op) {
        case 'open_root':
          await ledger.openRoot(ORG, run(step.run), cap(step.cap));
          break;
        case 'open_child':
          seen = seenRemaining(
            await ledger.openChild(ORG, run(step.parent), run(step.run), cap(step.cap)),
          );
          break;
        case 'pin':
          pins.set(run(step.run), {
            parent: step.parent === undefined ? null : run(step.parent),
            cap: cap(step.cap),
          });
          break;
        case 'close':
          await ledger.close(ORG, run(step.run));
          break;
        case 'advance':
          subject.advance(step.seconds ?? 0);
          break;
        case 'reserve': {
          const command = {
            orgId: ORG,
            runId: run(step.run),
            key: step.key ?? '',
            requested: amount(step.requested),
            minimum: amount(step.minimum),
            ttlMs: (step.ttlS ?? 60) * 1000,
          };
          // A pinned run opens lazily, as the service does (P6.5 migration).
          if ((await ledger.account(ORG, command.runId)) === null) {
            const chain: Array<{ runId: string; parentRunId: string | null; cap: BudgetCap }> = [];
            for (let id: string | null = command.runId; id !== null; ) {
              const pin = pins.get(id);
              if (pin === undefined) break;
              chain.unshift({ runId: id, parentRunId: pin.parent, cap: pin.cap });
              id = pin.parent;
            }
            if (chain.length > 0) {
              await ledger.ensure(
                ORG,
                chain,
                step.opening !== undefined ? amount(step.opening) : undefined,
              );
            }
          }
          const { reservation } = await ledger.reserve(command);
          seen = { granted: wire(reservation.granted) };
          break;
        }
        case 'commit':
          seen = seenSettlement(
            await ledger.commit(
              ORG,
              run(step.run),
              step.key ?? '',
              amount(step.actual),
              usageOf(step),
              undefined,
              step.hold === undefined
                ? undefined
                : { amount: amount(step.hold), ttlMs: (step.holdTtlS ?? 0) * 1000 },
            ),
          );
          break;
        case 'reconcile':
          seen = seenSettlement(
            await ledger.reconcile(
              ORG,
              run(step.run),
              step.key ?? '',
              amount(step.actual),
              usageOf(step),
            ),
          );
          break;
        case 'release':
          seen = seenSettlement(
            await ledger.release(ORG, run(step.run), step.key ?? ''),
          );
          break;
        case 'remaining':
          seen = seenRemaining(await ledger.remaining(ORG, run(step.run)));
          break;
        default:
          throw new Error(`${where}: unknown op`);
      }
    } catch (error) {
      if (!(error instanceof HarnessConflictError)) throw error;
      expect(error.code, where).to.equal(
        ((expected['refused'] as string | undefined) ?? 'no refusal').toUpperCase(),
      );
      continue;
    }
    expect(expected['refused'], `${where}: expected a refusal`).to.equal(undefined);
    if (seen === null) continue;
    for (const [name, value] of Object.entries(expected)) {
      expect(seen[name], `${where}: ${name}`).to.deep.equal(value);
    }
  }
}

export function describeBudgetLedgerContract(
  label: string,
  make: () => Promise<BudgetLedgerUnderTest>,
): void {
  describe(`P6.5 NL budget ledger store contract (${label})`, () => {
    let subject: BudgetLedgerUnderTest;

    beforeEach(async () => {
      subject = await make();
    });

    for (const scenario of budgetScenarios()) {
      it(`NL-1 ${scenario.id}`, async () => {
        await runBudgetScenario(subject, scenario);
      });
    }

    it('NL-1: one key is one reservation, however many ask at once', async () => {
      const { ledger } = subject;
      await ledger.openRoot(ORG, 'same', { usdMicros: 1_000, tokens: null });
      const command = {
        orgId: ORG,
        runId: 'same',
        key: 'k',
        requested: { usdMicros: 300, tokens: 0 },
        minimum: { usdMicros: 0, tokens: 0 },
        ttlMs: 60_000,
      };
      const all = await Promise.all(
        Array.from({ length: 6 }, () => ledger.reserve(command)),
      );
      expect(new Set(all.map((r) => r.reservation.granted.usdMicros))).to.deep.equal(new Set([300]));
      expect((await ledger.remaining(ORG, 'same')).reserved.usdMicros).to.equal(300);
      const commits = await Promise.all(
        Array.from({ length: 4 }, () =>
          ledger.commit(ORG, 'same', 'k', { usdMicros: 100, tokens: 0 }),
        ),
      );
      expect(new Set(commits.map((c) => c.charged.usdMicros))).to.deep.equal(new Set([100]));
      expect((await ledger.remaining(ORG, 'same')).committed.usdMicros).to.equal(100);
    });

    it("CLEANUP-S4: closing an ended run expires its open holds on every account of its path; a late commit still charges", async () => {
      const { ledger } = subject;
      await ledger.openRoot(ORG, 'root', { usdMicros: 10_000, tokens: null });
      await ledger.openChild(ORG, 'root', 'child', { usdMicros: 4_000, tokens: null });
      const hold = { minimum: { usdMicros: 0, tokens: 0 }, ttlMs: 3_600_000 };
      await ledger.reserve({ orgId: ORG, runId: 'child', key: 'a', requested: { usdMicros: 1_000, tokens: 0 }, ...hold });
      await ledger.reserve({ orgId: ORG, runId: 'child', key: 'b', requested: { usdMicros: 500, tokens: 0 }, ...hold });
      await ledger.reserve({ orgId: ORG, runId: 'root', key: 'r', requested: { usdMicros: 200, tokens: 0 }, ...hold });
      expect((await ledger.remaining(ORG, 'root')).reserved.usdMicros).to.equal(1_700);

      await ledger.close(ORG, 'child');
      expect((await ledger.remaining(ORG, 'root')).reserved.usdMicros, "the root's own hold stays").to.equal(200);
      expect((await ledger.remaining(ORG, 'child')).reserved.usdMicros).to.equal(0);
      const late = await ledger.commit(ORG, 'child', 'a', { usdMicros: 300, tokens: 0 });
      expect(late.charged.usdMicros).to.equal(300);
      expect((await ledger.remaining(ORG, 'root')).committed.usdMicros).to.equal(300);
      const refused = await ledger
        .reserve({ orgId: ORG, runId: 'child', key: 'c', requested: { usdMicros: 100, tokens: 0 }, ...hold })
        .catch((error: unknown) => error);
      expect((refused as HarnessConflictError).code).to.equal('BUDGET_ACCOUNT_CLOSED');
      await ledger.close(ORG, 'child');
      expect((await ledger.remaining(ORG, 'root')).reserved.usdMicros, 'closing again changes nothing').to.equal(200);
    });

    it("SEC-C-20: a reservation is settled only by the attempt that made it; another fence's settlement or replay of its key is refused", async () => {
      const { ledger } = subject;
      await ledger.openRoot(ORG, 'fenced', { usdMicros: 10_000, tokens: null });
      const ask = { orgId: ORG, runId: 'fenced', requested: { usdMicros: 1_000, tokens: 0 }, minimum: { usdMicros: 0, tokens: 0 }, ttlMs: 3_600_000 };
      const stale = await ledger.reserve({ ...ask, key: 'old', fence: 1 });
      expect(stale.reservation.fence).to.equal(1);
      await ledger.reserve({ ...ask, key: 'new', fence: 2 });
      const spend = { usdMicros: 400, tokens: 0 };
      const refusals = [
        ledger.commit(ORG, 'fenced', 'new', spend, undefined, undefined, undefined, 1),
        ledger.release(ORG, 'fenced', 'new', 1),
        ledger.reconcile(ORG, 'fenced', 'new', spend, undefined, undefined, 1),
        ledger.commit(ORG, 'fenced', 'old', spend, undefined, undefined, undefined, 2),
        ledger.release(ORG, 'fenced', 'old', 3),
        ledger.reserve({ ...ask, key: 'new', fence: 1 }),
      ];
      for (const [index, refusal] of refusals.entries()) {
        expect(await budgetCode(refusal), `refusal ${String(index)}`).to.equal('BUDGET_RESERVATION_MISSING');
      }
      expect((await ledger.remaining(ORG, 'fenced')).reserved.usdMicros, 'nothing was settled').to.equal(2_000);
      // Each attempt settles its own: the superseded one's spend still counts.
      expect((await ledger.commit(ORG, 'fenced', 'old', spend, undefined, undefined, undefined, 1)).charged.usdMicros).to.equal(400);
      expect((await ledger.release(ORG, 'fenced', 'new', 2)).reservation.state).to.equal('released');
      expect(await budgetCode(ledger.commit(ORG, 'fenced', 'old', spend, undefined, undefined, undefined, 2)), 'a replay by another attempt').to.equal('BUDGET_RESERVATION_MISSING');
      expect((await ledger.reserve({ ...ask, key: 'old', fence: 1 })).reservation.state, 'its own key again is idempotent').to.equal('committed');
      const remaining = await ledger.remaining(ORG, 'fenced');
      expect([remaining.committed.usdMicros, remaining.reserved.usdMicros]).to.deep.equal([400, 0]);
    });

    it('SEC-C-20: an estimated commit\'s hold carries the fence, and only the committing attempt reconciles it', async () => {
      const { ledger } = subject;
      await ledger.openRoot(ORG, 'held', { usdMicros: 10_000, tokens: null });
      await ledger.reserve({ orgId: ORG, runId: 'held', key: 'cut', requested: { usdMicros: 1_000, tokens: 0 }, minimum: { usdMicros: 0, tokens: 0 }, ttlMs: 3_600_000, fence: 4 });
      await ledger.commit(ORG, 'held', 'cut', { usdMicros: 300, tokens: 0 }, undefined, undefined, { amount: { usdMicros: 500, tokens: 0 }, ttlMs: 3_600_000 }, 4);
      expect(await budgetCode(ledger.reconcile(ORG, 'held', 'cut', { usdMicros: 0, tokens: 0 }, undefined, undefined, 5))).to.equal('BUDGET_RESERVATION_MISSING');
      expect((await ledger.remaining(ORG, 'held')).reserved.usdMicros, 'the hold stays').to.equal(500);
      const reconciled = await ledger.reconcile(ORG, 'held', 'cut', { usdMicros: 450, tokens: 0 }, undefined, undefined, 4);
      expect(reconciled.charged.usdMicros).to.equal(450);
      expect((await ledger.remaining(ORG, 'held')).reserved.usdMicros).to.equal(0);
    });

    it('SEC-C-20: a reservation made before the binding (no fence) is settled by any attempt of its run until it lapses', async () => {
      const { ledger } = subject;
      await ledger.openRoot(ORG, 'legacy', { usdMicros: 10_000, tokens: null });
      await ledger.reserve({ orgId: ORG, runId: 'legacy', key: 'pre', requested: { usdMicros: 100, tokens: 0 }, minimum: { usdMicros: 0, tokens: 0 }, ttlMs: 60_000 });
      expect((await ledger.commit(ORG, 'legacy', 'pre', { usdMicros: 60, tokens: 0 }, undefined, undefined, undefined, 7)).charged.usdMicros).to.equal(60);
    });

    it('WP88-FU-4: close answers the account as closed, with its usage, and null for a run with none', async () => {
      const { ledger } = subject;
      expect(await ledger.close(ORG, 'no-account')).to.equal(null);
      await ledger.openRoot(ORG, 'used', { usdMicros: 10_000, tokens: null });
      const hold = { minimum: { usdMicros: 0, tokens: 0 }, ttlMs: 3_600_000 };
      await ledger.reserve({ orgId: ORG, runId: 'used', key: 'm', requested: { usdMicros: 100, tokens: 0 }, ...hold });
      const usage = { inputTokens: 900, outputTokens: 40, cacheReadTokens: 600, cacheWriteTokens: 0 };
      await ledger.commit(ORG, 'used', 'm', { usdMicros: 80, tokens: 0 }, usage);
      const closed = await ledger.close(ORG, 'used');
      expect(closed?.closed).to.equal(true);
      expect(closed?.used).to.deep.equal(await ledger.account(ORG, 'used').then((a) => a?.used));
      expect(closed?.used?.cacheReadTokens).to.equal(600);
    });

    it('NL-2: parallel reservations across a tree never exceed the root or a share', async () => {
      const { ledger } = subject;
      await ledger.openRoot(ORG, 'root', { usdMicros: 10_000, tokens: null });
      await ledger.openChild(ORG, 'root', 'c0', { usdMicros: 2_000, tokens: null });
      await ledger.openChild(ORG, 'root', 'c1', { usdMicros: null, tokens: null });
      await ledger.openChild(ORG, 'root', 'c2', { usdMicros: null, tokens: null });
      const runs = ['root', 'c0', 'c1', 'c2'];
      const granted = await Promise.all(
        Array.from({ length: 48 }, async (_, index) => {
          const runId = runs[index % runs.length] as string;
          try {
            const { reservation } = await ledger.reserve({
              orgId: ORG,
              runId,
              key: `call-${String(index)}`,
              requested: { usdMicros: 700, tokens: 0 },
              minimum: { usdMicros: 100, tokens: 0 },
              ttlMs: 60_000,
            });
            await ledger.commit(ORG, runId, reservation.key, reservation.granted);
            return reservation.granted.usdMicros;
          } catch (error) {
            if (error instanceof HarnessConflictError) return 0;
            throw error;
          }
        }),
      );
      const total = granted.reduce((sum, value) => sum + value, 0);
      expect(total).to.be.at.most(10_000).and.at.least(10_000 - 100);
      const root = await ledger.remaining(ORG, 'root');
      expect(root.committed.usdMicros).to.equal(total);
      expect(root.reserved.usdMicros).to.equal(0);
      expect((await ledger.remaining(ORG, 'c0')).committed.usdMicros).to.be.at.most(2_000);
    });
  });
}

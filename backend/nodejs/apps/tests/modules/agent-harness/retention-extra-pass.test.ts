/**
 * The retention sweeper's extra pass (`RetentionPass`, an extension point): unbound, none runs; bound, it runs after
 * the orgs' own periods, and each conversation it expires waits while a run of it is still going, moves only if it
 * is still idle, and is logged with the pass's detail. Also the sweeper's own guards: a lease that cannot be read
 * ends the tick with a log line, and `stop` waits for the tick in flight before giving the lease back.
 */
import { expect } from 'chai';
import { InMemoryConversations } from '../../../src/modules/agent-harness/adapters/in-memory-conversations';
import { InMemoryErasureJobStore } from '../../../src/modules/agent-harness/adapters/in-memory-session-erasure';
import { RetentionSweeper } from '../../../src/modules/agent-harness/application/retention-sweeper';
import type { RunStatus } from '../../../src/modules/agent-harness/domain/execution';
import { DAY_MS } from '../../../src/modules/agent-harness/domain/session-retention';
import type { SessionRunLookup } from '../../../src/modules/agent-harness/ports/session-erasure';
import type { RetentionPass, SessionRetentionSettings } from '../../../src/modules/agent-harness/ports/session-retention';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';

const ORG = 'org-1';

class Runs implements SessionRunLookup {
  readonly sessions = new Map<string, RunStatus[]>();

  async runsOf(_orgId: string, sessionId: string) {
    return (this.sessions.get(sessionId) ?? []).map((status, index) => ({
      runId: `${sessionId}-r${String(index)}`,
      status,
      revision: 1,
      harnessMajor: 2 as const,
    }));
  }
}

interface Logged {
  level: 'info' | 'warn' | 'error';
  event: string;
  meta: Record<string, unknown>;
}

/** A pass that expires the sessions it is given after `days` idle days, as an edition's pass would. */
const passOver = (sessions: () => string[], days: number) => {
  const calls: Array<{ periods: number }> = [];
  const pass: RetentionPass = {
    async run(now, periods, expire) {
      calls.push({ periods: periods.length });
      let expired = 0;
      for (const sessionId of sessions()) {
        if (await expire(ORG, sessionId, now - days * DAY_MS, { days, detail: { rule: 'short' } })) expired += 1;
      }
      return expired;
    },
  };
  return { pass, calls };
};

function world(opts: { orgDays?: number | null; pass?: RetentionPass } = {}) {
  let now = 1_000 * DAY_MS;
  const conversations = new InMemoryConversations(() => now);
  const jobs = new InMemoryErasureJobStore(() => now);
  const runs = new Runs();
  const logged: Logged[] = [];
  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event, meta) => logged.push({ level: 'info', event, meta: (meta ?? {}) as Record<string, unknown> }),
    warn: (event, meta) => logged.push({ level: 'warn', event, meta: (meta ?? {}) as Record<string, unknown> }),
    error: (event, meta) => logged.push({ level: 'error', event, meta: (meta ?? {}) as Record<string, unknown> }),
  };
  const orgDays = opts.orgDays ?? null;
  const settings: SessionRetentionSettings = {
    all: async () => (orgDays === null ? [] : [{ orgId: ORG, days: orgDays }]),
  };
  const lease = {
    acquire: async () => true,
    tryAcquire: async () => true,
    release: async () => undefined,
  };
  const sweeper = new RetentionSweeper<unknown>({
    store: {
      now: async () => now,
      collectPayloads: async () => 0,
      expireCommandReceipts: async () => 0,
      expireAdmissionReceipts: async () => 0,
      harnessOrgs: async () => [ORG],
    } as never,
    settings,
    ...(opts.pass !== undefined ? { extraPass: opts.pass } : {}),
    conversations,
    erasures: jobs,
    runs,
    artifacts: null,
    transaction: (work) => work(undefined),
    lease: lease as never,
    holderId: 'h',
    logger,
  });
  const conversation = (sessionId: string, idleDays: number, ...statuses: RunStatus[]) => {
    conversations.put({ orgId: ORG, sessionId, deleted: false, updatedAt: 1, lastActivityAt: now - idleDays * DAY_MS });
    runs.sessions.set(sessionId, statuses);
  };
  const erased = async (sessionId: string): Promise<boolean> => {
    const job = await jobs.find(ORG, sessionId);
    if (job !== null) expect(job).to.include({ reason: 'retention', by: null });
    return job !== null && conversations.get(sessionId)?.deleted === true;
  };
  return { sweeper, runs, lease, logged, conversation, erased, advance: (days: number) => (now += days * DAY_MS) };
}

describe('the retention sweeper’s extra pass (RetentionPass)', () => {
  it('runs none when unbound: only the org’s own period expires a conversation', async () => {
    const w = world();
    w.conversation('s-old', 5_000, 'completed');
    expect((await w.sweeper.tick())?.expiredSessions).to.equal(0);
    expect(await w.erased('s-old')).to.equal(false);
  });

  it('runs after the orgs’ periods, which it is given, and its expiries count with the pass’s detail logged', async () => {
    const { pass, calls } = passOver(() => ['s-short'], 30);
    const w = world({ orgDays: 90, pass });
    w.conversation('s-short', 29, 'completed');
    expect((await w.sweeper.tick())?.expiredSessions).to.equal(0);
    w.advance(2);
    expect((await w.sweeper.tick())?.expiredSessions).to.equal(1);
    expect(await w.erased('s-short')).to.equal(true);
    expect(calls).to.deep.equal([{ periods: 1 }, { periods: 1 }]);
    const expired = w.logged.find((line) => line.event === 'retention.expired');
    expect(expired?.meta).to.include({ orgId: ORG, sessionId: 's-short', rule: 'short', days: 30 });
  });

  it('a conversation with a run still going waits, and is erased once its runs end', async () => {
    const { pass } = passOver(() => ['s-busy'], 30);
    const w = world({ pass });
    w.conversation('s-busy', 400, 'completed', 'running');
    expect((await w.sweeper.tick())?.expiredSessions).to.equal(0);
    expect(await w.erased('s-busy')).to.equal(false);
    w.runs.sessions.set('s-busy', ['completed', 'completed']);
    expect((await w.sweeper.tick())?.expiredSessions).to.equal(1);
  });

  it('a conversation that moved on before the expiry commits is not counted', async () => {
    const { pass } = passOver(() => ['s-active'], 30);
    const w = world({ pass });
    w.conversation('s-active', 1, 'completed');
    expect((await w.sweeper.tick())?.expiredSessions).to.equal(0);
    expect(await w.erased('s-active')).to.equal(false);
  });
});

describe('the retention sweeper’s own guards', () => {
  it('a lease that cannot be read ends the tick with a typed log line, and the next tick runs', async () => {
    const w = world({ orgDays: 10 });
    w.lease.tryAcquire = async () => {
      throw new TypeError('lease store down');
    };
    expect(await w.sweeper.tick()).to.equal(null);
    const failed = w.logged.find((line) => line.event === 'retention.tick_failed');
    expect(failed?.level).to.equal('error');
    expect(failed?.meta).to.include({ errorType: 'TypeError' });
    w.lease.tryAcquire = async () => true;
    expect(await w.sweeper.tick()).to.not.equal(null);
  });

  it('stop waits for the tick in flight, then gives the lease back', async () => {
    const w = world({ orgDays: 10 });
    let releaseTick: () => void = () => undefined;
    const order: string[] = [];
    w.lease.tryAcquire = () =>
      new Promise<boolean>((resolve) => {
        releaseTick = () => {
          order.push('tick-ended');
          resolve(false);
        };
      });
    w.lease.release = async () => {
      order.push('released');
    };
    const tick = w.sweeper.tick();
    // A second tick while one runs does nothing.
    expect(await w.sweeper.tick()).to.equal(null);
    const stopped = w.sweeper.stop();
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(order).to.deep.equal([]);
    releaseTick();
    await Promise.all([tick, stopped]);
    expect(order).to.deep.equal(['tick-ended', 'released']);
  });
});

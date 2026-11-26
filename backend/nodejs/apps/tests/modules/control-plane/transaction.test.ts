/**
 * CPD-1: the transaction (TX) and audit (CA) contracts on the in-memory
 * transaction and on ordered steps over in-memory head leases, the
 * ordered-steps repair (OS) and the pure change helpers (CC).
 */
import { expect } from 'chai';
import {
  changedPaths,
  ConfigAuditLog,
  ControlPlaneRepair,
  ConfigChanged,
  ControlPlaneUnavailableError,
  CpTx,
  OrderedStepsTransaction,
  summarizePaths,
} from '../../../src/modules/control-plane';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import {
  FailingConfigAuditLog,
  InMemoryChangeOutbox,
  InMemoryControlPlaneTransaction,
  InMemoryOrgHeadLeases,
  NO_PAUSE,
  RecordingLog,
} from './support/in-memory';
import { aChange, CasRegister, ORG, setRegister, transactionContract } from './support/transaction-contract';
import { configAuditContract } from './support/config-audit-contract';
import {
  changeEventSchema,
  configChangedSchema,
} from '../../../src/modules/control-plane/contracts/generated';
import { committedChange } from '../../../src/modules/control-plane/adapters/staged-transaction';

/** Yields to the event loop between attempts so a concurrent writer can finish; never waits on a clock. */
const YIELD = { attempts: 50, pause: () => new Promise<void>((resolve) => setImmediate(resolve)) };

const configRecords = (chain: InMemoryAuditChainStore, orgId: string) =>
  (chain.records.get(orgId) ?? []).filter((record) => record.streamId === `cp:${orgId}`);

transactionContract('in memory', {
  atomic: true,
  make: async (options = {}) => {
    const chain = new InMemoryAuditChainStore();
    const outbox = new InMemoryChangeOutbox();
    const log = new RecordingLog();
    const failing = new Set<number>();
    const transaction = new InMemoryControlPlaneTransaction({
      audit: options.audit ?? new ChainConfigAuditLog(chain),
      outbox,
      log,
      beforeCommit: () => {
        if (failing.size > 0) throw new Error('commit failed');
      },
    });
    return {
      transaction,
      events: async () => outbox.events,
      log,
      head: async (orgId) => transaction.heads.get(orgId) ?? 0,
      configRecords: async (orgId) => configRecords(chain, orgId),
      failCommit: async (tx: CpTx) => {
        failing.add(tx.headRevision);
      },
    };
  },
});

const orderedWorld = (options: { audit?: ConfigAuditLog; now?: () => number } = {}) => {
  const chain = new InMemoryAuditChainStore();
  const chained = new ChainConfigAuditLog(chain);
  const heads = new InMemoryOrgHeadLeases();
  const outbox = new InMemoryChangeOutbox();
  const log = new RecordingLog();
  const audit =
    options.audit === undefined
      ? chained
      : { record: options.audit.record.bind(options.audit), recorded: chained.recorded.bind(chained) };
  const transaction = new OrderedStepsTransaction({
    audit,
    heads,
    outbox,
    log,
    retry: YIELD,
    leaseMs: 1_000,
    ...(options.now !== undefined ? { now: options.now } : {}),
  });
  return { chain, heads, outbox, log, transaction, chained };
};

transactionContract('ordered steps (in-memory heads)', {
  atomic: false,
  make: async (options = {}) => {
    const world = orderedWorld(options);
    return {
      transaction: world.transaction,
      events: async () => world.outbox.events,
      log: world.log,
      head: async (orgId) => world.heads.rows.get(orgId)?.revision ?? 0,
      configRecords: async (orgId) => configRecords(world.chain, orgId),
    };
  },
});

configAuditContract('chain over the in-memory audit store', async () => {
  const chain = new InMemoryAuditChainStore();
  return {
    transaction: new InMemoryControlPlaneTransaction({ audit: new ChainConfigAuditLog(chain) }),
    chain: async (orgId) => chain.records.get(orgId) ?? [],
    appendRun: async (orgId, entries) => {
      await chain.append(orgId, entries, new Date().toISOString());
    },
  };
});

describe('CPD-1 ordered steps and repair (OS)', () => {
  const ctx = { orgId: ORG, actor: 'user:u-admin' };

  it('OS-1: a writer that stopped after its effect is recorded by the next writer, then the next write follows', async () => {
    let clock = 1_000_000;
    const failing = orderedWorld({ audit: new FailingConfigAuditLog(), now: () => clock });
    const register = new CasRegister();
    const first = await failing.transaction.run(ctx, setRegister(register, 'v1')).catch((e: unknown) => e);
    expect(first).to.be.instanceOf(ControlPlaneUnavailableError);
    expect(register.value).to.equal('v1');
    expect(failing.heads.rows.get(ORG)?.lease?.intent?.headRevision).to.equal(1);

    const next = new OrderedStepsTransaction({
      audit: failing.chained,
      heads: failing.heads,
      outbox: failing.outbox,
      log: failing.log,
      retry: NO_PAUSE,
      now: () => clock,
    });
    clock += 1;
    const committed = await next.run(ctx, setRegister(register, 'v2', aChange('second')));
    expect(committed.headRevision).to.equal(2);
    const records = configRecords(failing.chain, ORG);
    expect(records.map((record) => [record.journalSeq, record.fields['objectId'], record.fields['repaired']])).to.deep.equal([
      [1, 'labs', true],
      [2, 'second', undefined],
    ]);
    expect(failing.heads.rows.get(ORG)).to.deep.equal({ revision: 2, lease: null });
    expect(failing.log.lines.filter(([, message]) => message === 'cp.repair.completed')).to.have.length(1);
  });

  it('OS-2: the repair of one intent records it once however often it runs', async () => {
    const world = orderedWorld();
    const intent: ConfigChanged = {
      ...aChange(),
      orgId: ORG,
      actor: 'user:u-admin',
      approvalRef: null,
      headRevision: 1,
    };
    const repair = new ControlPlaneRepair(world.chained, world.outbox, world.log);
    await repair.complete(intent);
    await repair.complete(intent);
    expect(configRecords(world.chain, ORG)).to.have.length(1);
    expect(world.outbox.events).to.have.length(1);
  });

  it('OS-3: a writer that stopped before recording an intent leaves nothing to record', async () => {
    let clock = 5_000;
    const world = orderedWorld({ now: () => clock });
    await world.heads.acquire(ORG, 'dead-writer', clock, clock + 1_000);
    clock += 2_000;
    const committed = await world.transaction.run(ctx, async () => ({ result: null, change: aChange() }));
    expect(committed.headRevision).to.equal(1);
    expect(configRecords(world.chain, ORG).map((record) => record.fields['repaired'])).to.deep.equal([undefined]);
  });

  it('OS-4: a live lease held by another writer fails the write closed after its bound, with no effect', async () => {
    const clock = 9_000;
    const world = orderedWorld({ now: () => clock });
    await world.heads.acquire(ORG, 'live-writer', clock, clock + 60_000);
    const register = new CasRegister();
    const failed = await world.transaction.run(ctx, setRegister(register, 'v1')).catch((e: unknown) => e);
    expect(failed).to.be.instanceOf(ControlPlaneUnavailableError);
    expect(register.writes).to.equal(0);
    expect(configRecords(world.chain, ORG)).to.deep.equal([]);
  });

  it('OS-4: the lease is waited for: freed between attempts, the write commits', async () => {
    const clock = 9_000;
    const world = orderedWorld({ now: () => clock });
    await world.heads.acquire(ORG, 'live-writer', clock, clock + 60_000);
    const transaction = new OrderedStepsTransaction({
      audit: world.chained,
      heads: world.heads,
      retry: { attempts: 3, pause: () => world.heads.release(ORG, 'live-writer', false) },
      now: () => clock,
    });
    expect((await transaction.run(ctx, async () => ({ result: 'ok', change: aChange() }))).result).to.equal('ok');
  });
});

describe('CPD-1 change helpers (CC)', () => {
  for (const [name, before, after, paths] of [
    ['equal documents', { a: 1, b: [1, 2] }, { b: [1, 2], a: 1 }, []],
    ['an added leaf', { a: 1 }, { a: 1, b: { c: true } }, ['b']],
    ['a removed leaf', { a: 1, b: 2 }, { a: 1 }, ['b']],
    ['a changed nested leaf', { o: { x: { y: 1, z: 2 } } }, { o: { x: { y: 3, z: 2 } } }, ['o.x.y']],
    ['a reordered array is one path', { list: [1, 2] }, { list: [2, 1] }, ['list']],
    ['null to a document', null, { a: 1 }, ['a']],
    ['a scalar root', 1, 2, ['$']],
  ] as const) {
    it(`CC-1: changedPaths, ${name}`, () => {
      expect(changedPaths(before, after)).to.deep.equal(paths);
    });
  }

  it('CC-1: paths are sorted and unique', () => {
    expect(changedPaths({}, { z: 1, a: 1, m: { b: 1, a: 1 } })).to.deep.equal(['a', 'm', 'z']);
    expect(changedPaths({ m: {} }, { m: { b: 1, a: 1 } })).to.deep.equal(['m.a', 'm.b']);
  });

  it('CC-2: summarizePaths never cuts a path and counts what it leaves out', () => {
    expect(summarizePaths(['a', 'b'], 256)).to.equal('a,b');
    expect(summarizePaths([], 256)).to.equal('');
    const many = Array.from({ length: 40 }, (_, i) => `featureFlags.FLAG_${String(i).padStart(2, '0')}`);
    const summary = summarizePaths(many, 256);
    expect(summary.length).to.be.at.most(256);
    const [kept, rest] = summary.split(',…+');
    const keptPaths = kept!.split(',');
    expect(keptPaths.every((path) => many.includes(path))).to.equal(true);
    expect(Number(rest)).to.equal(many.length - keptPaths.length);
    expect(summarizePaths(['x'.repeat(300)], 256)).to.equal('…+1');
  });
});

describe('CPD-1 the payload and the event are the contracts (CC-3)', () => {
  it('what a write commits parses with the generated ConfigChanged and ChangeEvent schemas', async () => {
    const outbox = new InMemoryChangeOutbox();
    const transaction = new InMemoryControlPlaneTransaction({
      audit: new ChainConfigAuditLog(new InMemoryAuditChainStore()),
      outbox,
    });
    const committed = await transaction.run({ orgId: ORG, actor: 'user:u-admin' }, async () => ({
      result: null,
      change: aChange(),
    }));
    expect(() => changeEventSchema.parse(outbox.events[0])).to.not.throw();
    const changed = committedChange({ orgId: ORG, actor: 'user:u-admin' }, committed.headRevision, aChange());
    expect(configChangedSchema.parse(changed)).to.deep.equal(changed);
    expect(configChangedSchema.safeParse({ ...changed, actor: 'p_u-admin' }).success).to.equal(false);
  });
});

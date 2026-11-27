/**
 * CPD-6 change events around a control-plane write: announced only after the
 * commit (NTF-01, NTF-02), to this process's holders before the write answers
 * (CPD6-32), each exactly the outbox row's event (NTF-13).
 */
import { expect } from 'chai';
import { ChangeSubscribers, type ChangeEvent } from '../../../src/modules/control-plane';
import { ResolvedSnapshotCache } from '../../../src/modules/control-plane/application/snapshot-cache';
import { ChainConfigAuditLog } from '../../../src/modules/agent-harness/adapters/chain-config-audit-log';
import { InMemoryAuditChainStore } from '../../../src/modules/agent-harness/adapters/in-memory-audit';
import { ORG_A, ORG_B } from './support/cp-objects';
import { InMemoryHeadRevisions } from './support/head-revisions-contract';
import { InMemoryChangeOutbox, InMemoryControlPlaneTransaction } from './support/in-memory';
import { aSnapshot } from './support/resolution-fixtures';
import { aChange, CasRegister, setRegister } from './support/transaction-contract';

const ctx = (orgId = ORG_A) => ({ orgId, actor: 'user:u-admin' });

function world() {
  const subscribers = new ChangeSubscribers();
  const announced: ChangeEvent[] = [];
  let beforeCommit: (() => void) | undefined;
  const outbox = new InMemoryChangeOutbox((event) => {
    announced.push(event);
    subscribers.deliver(event);
  });
  const transaction = new InMemoryControlPlaneTransaction({
    audit: new ChainConfigAuditLog(new InMemoryAuditChainStore()),
    outbox,
    beforeCommit: () => beforeCommit?.(),
  });
  return {
    subscribers,
    announced,
    outbox,
    transaction,
    onCommit: (probe: () => void) => {
      beforeCommit = probe;
    },
  };
}

describe('CPD-6 change events of a control-plane write', () => {
  it('NTF-01: a committed write announces one event with the new head and the head before it, after the commit', async () => {
    const w = world();
    const seenBeforeCommit: number[] = [];
    w.onCommit(() => seenBeforeCommit.push(w.announced.length));
    const register = new CasRegister();

    const first = await w.transaction.run(ctx(), setRegister(register, 'a'));
    const second = await w.transaction.run(ctx(), setRegister(register, 'b', aChange('approvals')));

    expect(seenBeforeCommit, 'nothing is announced before its commit').to.deep.equal([0, 1]);
    expect(w.announced).to.deep.equal([
      { type: 'cp.changed', orgId: ORG_A, revision: first.headRevision, previous: first.headRevision - 1, touched: [{ kind: 'platform-settings', id: 'labs' }], narrowing: false },
      { type: 'cp.changed', orgId: ORG_A, revision: second.headRevision, previous: first.headRevision, touched: [{ kind: 'platform-settings', id: 'approvals' }], narrowing: false },
    ]);
    expect(w.announced, 'NTF-13: what is announced is the outbox row').to.deep.equal(w.outbox.events);
  });

  it('NTF-02: a write that does not commit announces nothing and leaves no outbox row', async () => {
    const w = world();
    w.onCommit(() => {
      throw new Error('the commit failed');
    });

    let failed: unknown;
    await w.transaction.run(ctx(), async () => ({ result: null, change: aChange() })).catch((error: unknown) => {
      failed = error;
    });

    expect(failed).to.be.instanceOf(Error);
    expect(w.announced).to.deep.equal([]);
    expect(w.outbox.events).to.deep.equal([]);
  });

  it('a publish that fails after the commit does not fail the write', async () => {
    const outbox = new InMemoryChangeOutbox(() => {
      throw new Error('a holder failed');
    });
    const transaction = new InMemoryControlPlaneTransaction({ audit: new ChainConfigAuditLog(new InMemoryAuditChainStore()), outbox });

    const committed = await transaction.run(ctx(), async () => ({ result: 'done', change: aChange() }));

    expect(committed).to.deep.equal({ result: 'done', headRevision: 1 });
    expect(outbox.events).to.have.length(1);
  });

  it("CPD6-32: the writing instance's snapshot cache reloads the org at its next pin, before any head read says so; another org's entry stays", async () => {
    const w = world();
    // A head store that lags the write (a secondary, or the read racing the commit).
    const heads = new InMemoryHeadRevisions();
    const loads: string[] = [];
    let stored = 0;
    const cache = new ResolvedSnapshotCache({
      heads,
      load: async (orgId, agentKey, actorId) => {
        loads.push(orgId);
        return { ...aSnapshot({ headRevision: orgId === ORG_A ? stored : 0, agentKey, actorId }), orgId };
      },
    });
    w.subscribers.subscribe((event) => cache.dropBehind(event));
    await cache.snapshot(ORG_A, 'a7', 'u3');
    await cache.snapshot(ORG_B, 'a7', 'u3');

    const committed = await w.transaction.run(ctx(), async () => {
      stored += 1;
      return { result: null, change: aChange() };
    });
    const after = await cache.snapshot(ORG_A, 'a7', 'u3');
    await cache.snapshot(ORG_B, 'a7', 'u3');

    expect(after.headRevision).to.equal(committed.headRevision);
    expect(loads).to.deep.equal([ORG_A, ORG_B, ORG_A]);

    // The same event again from the broker (at least once): nothing reloads.
    w.subscribers.deliver(w.announced[0] as ChangeEvent);
    heads.revisions.set(ORG_A, committed.headRevision);
    await cache.snapshot(ORG_A, 'a7', 'u3');
    expect(loads).to.deep.equal([ORG_A, ORG_B, ORG_A]);
  });

});

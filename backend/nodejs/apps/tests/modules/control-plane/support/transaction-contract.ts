/**
 * TX: what every ControlPlaneTransaction does (03-interfaces §2.1), run on
 * the in-memory adapter (`transaction.test.ts`), the replica-set adapter and
 * the ordered-steps adapter (`transaction.mongo.itest.ts`).
 */
import { expect } from 'chai';
import {
  ChangeEvent,
  ConfigAuditLog,
  ConfigChange,
  ControlPlaneRetryableError,
  ControlPlaneTransaction,
  ControlPlaneUnavailableError,
  CpTx,
} from '../../../../src/modules/control-plane';
import { AuditRecord } from '../../../../src/modules/agent-harness/domain/audit';
import { FailingConfigAuditLog, Gate, RecordingLog } from './in-memory';

export interface TransactionWorld {
  transaction: ControlPlaneTransaction;
  /** The change events the outbox committed. */
  events(): Promise<ChangeEvent[]>;
  log: RecordingLog;
  head(orgId: string): Promise<number>;
  /** The org's `config.changed` chain records, in chain order. */
  configRecords(orgId: string): Promise<AuditRecord[]>;
  /** Fault injection: makes the commit of `tx` fail after its effects ran (atomic adapters). */
  failCommit?(tx: CpTx): Promise<void>;
}

export interface TransactionFixture {
  /** Whether an audit failure keeps the effect from ever applying (false: ordered steps, which repair). */
  atomic: boolean;
  make(options?: { audit?: ConfigAuditLog }): Promise<TransactionWorld>;
}

/** A store outside the transaction with compare-and-set, like the configuration KV store. */
export class CasRegister {
  value: string | null = null;
  version = 0;
  writes = 0;

  read(): { value: string | null; version: number } {
    return { value: this.value, version: this.version };
  }

  async replace(expected: number, value: string): Promise<void> {
    if (this.version !== expected) {
      throw new ControlPlaneRetryableError('the register moved');
    }
    this.value = value;
    this.version += 1;
    this.writes += 1;
  }
}

export const ORG = 'c00000000000000000000001';
export const OTHER_ORG = 'c00000000000000000000002';
const ctx = (orgId = ORG) => ({ orgId, actor: 'user:u-admin' });

export const aChange = (id = 'labs', changed: string[] = ['featureFlags.ENABLE_X']): ConfigChange => ({
  scope: `org:${ORG}`,
  object: { type: 'platform-settings', id },
  revisionFrom: null,
  revisionTo: null,
  changeDigest: `sha256:${'a'.repeat(64)}`,
  changed,
});

/** A write that plans on a fresh read of `register` and applies through `tx.external`. */
export const setRegister =
  (register: CasRegister, value: string, change: ConfigChange = aChange(), seen?: (tx: CpTx) => void) =>
  async (tx: CpTx) => {
    seen?.(tx);
    const { version } = register.read();
    const applied = tx.external(async () => {
      await register.replace(version, value);
      return value;
    });
    return { result: applied, change };
  };

export function transactionContract(name: string, fixture: TransactionFixture): void {
  describe(`ControlPlaneTransaction contract (TX): ${name}`, function () {
    this.timeout(60_000);

    it('TX-1: a committed write bumps the head once, records one entry and one event, applies its effect once', async () => {
      const world = await fixture.make();
      const register = new CasRegister();
      let inside = 0;
      const before = await world.head(ORG);
      const committed = await world.transaction.run(
        ctx(),
        setRegister(register, 'v1', aChange(), (tx) => {
          inside = tx.headRevision;
        }),
      );
      expect(committed.headRevision).to.equal(before + 1);
      expect(inside).to.equal(committed.headRevision);
      expect(committed.result.value).to.equal('v1');
      expect(await world.head(ORG)).to.equal(before + 1);
      expect(register.writes).to.equal(1);
      const records = await world.configRecords(ORG);
      expect(records).to.have.length(1);
      expect(records[0]).to.include({
        kind: 'config.changed',
        streamId: `cp:${ORG}`,
        writer: 'control-plane',
        sessionId: null,
        runId: null,
        journalSeq: committed.headRevision,
      });
      expect(records[0]!.fields).to.include({
        objectType: 'platform-settings',
        objectId: 'labs',
        actor: 'user:u-admin',
        headRevision: committed.headRevision,
        changed: 'featureFlags.ENABLE_X',
      });
      expect(await world.events()).to.deep.equal([
        {
          type: 'cp.changed',
          orgId: ORG,
          revision: committed.headRevision,
          previous: committed.headRevision - 1,
          touched: [{ kind: 'platform-settings', id: 'labs' }],
          narrowing: false,
        },
      ]);
      expect(world.log.lines.filter(([, message]) => message === 'cp.change.committed')).to.have.length(1);
    });

    it('TX-2: work that throws leaves no head, audit, outbox or effect', async () => {
      const world = await fixture.make();
      const register = new CasRegister();
      const before = await world.head(ORG);
      const boom = new Error('validation refused');
      const failed = await world.transaction
        .run(ctx(), async (tx) => {
          tx.external(() => register.replace(0, 'never'));
          throw boom;
        })
        .catch((error: unknown) => error);
      expect(failed).to.equal(boom);
      expect(await world.head(ORG)).to.equal(before);
      expect(await world.configRecords(ORG)).to.deep.equal([]);
      expect(await world.events()).to.deep.equal([]);
      expect(register.writes).to.equal(0);
    });

    it('TX-3: an audit failure fails the write closed (atomic) or leaves it for the repair (ordered steps)', async () => {
      const world = await fixture.make({ audit: new FailingConfigAuditLog() });
      const register = new CasRegister();
      const before = await world.head(ORG);
      const failed = await world.transaction.run(ctx(), setRegister(register, 'v1')).catch((error: unknown) => error);
      expect(failed).to.be.instanceOf(ControlPlaneUnavailableError);
      expect(await world.events()).to.deep.equal([]);
      if (fixture.atomic) {
        expect(register.writes).to.equal(0);
        expect(await world.head(ORG)).to.equal(before);
      } else {
        expect(register.writes).to.equal(1);
      }
    });

    it('TX-4: a lost compare-and-set plans the write again and commits it once', async () => {
      const world = await fixture.make();
      const register = new CasRegister();
      const before = await world.head(ORG);
      let plans = 0;
      const committed = await world.transaction.run(ctx(), async (tx) => {
        plans += 1;
        const { version } = register.read();
        // Another writer outside the control plane moves the register under the first plan.
        if (plans === 1) register.version += 1;
        tx.external(() => register.replace(version, 'v1'));
        return { result: null, change: aChange() };
      });
      expect(plans).to.equal(2);
      expect(committed.headRevision).to.equal(before + 1);
      expect(register.value).to.equal('v1');
      expect(await world.configRecords(ORG)).to.have.length(1);
    });

    it('TX-4: an effect that fails for good records nothing', async () => {
      const world = await fixture.make();
      const before = await world.head(ORG);
      const failed = await world.transaction
        .run(ctx(), async (tx) => {
          tx.external(() => Promise.reject(new Error('kv down')));
          return { result: null, change: aChange() };
        })
        .catch((error: unknown) => error);
      expect((failed as Error).message).to.equal('kv down');
      expect(await world.head(ORG)).to.equal(before);
      expect(await world.configRecords(ORG)).to.deep.equal([]);
      expect(await world.events()).to.deep.equal([]);
    });

    it('TX-5: two concurrent writes in one org commit at consecutive head revisions', async () => {
      const world = await fixture.make();
      const register = new CasRegister();
      const before = await world.head(ORG);
      const [a, b] = await Promise.all([
        world.transaction.run(ctx(), setRegister(register, 'a', aChange('a'))),
        world.transaction.run(ctx(), setRegister(register, 'b', aChange('b'))),
      ]);
      expect([a.headRevision, b.headRevision].sort()).to.deep.equal([before + 1, before + 2]);
      expect(register.writes).to.equal(2);
      const records = await world.configRecords(ORG);
      expect(records.map((record) => record.journalSeq)).to.deep.equal([before + 1, before + 2]);
      const byRevision = new Map(records.map((record) => [record.journalSeq, record.fields['objectId']]));
      expect(byRevision.get(a.headRevision)).to.equal('a');
      expect(byRevision.get(b.headRevision)).to.equal('b');
    });

    it('TX-6: a write held open in one org does not hold up another org', async () => {
      const world = await fixture.make();
      const gate = new Gate();
      const entered = new Gate();
      const slow = world.transaction.run(ctx(ORG), async () => {
        entered.open();
        await gate.opened;
        return { result: 'a', change: aChange() };
      });
      await entered.opened;
      const fast = await world.transaction.run(ctx(OTHER_ORG), async () => ({
        result: 'b',
        change: { ...aChange(), scope: `org:${OTHER_ORG}` },
      }));
      expect(fast.result).to.equal('b');
      gate.open();
      expect((await slow).result).to.equal('a');
    });

    it('TX-7: the event names the touched object and the declared narrowing', async () => {
      const world = await fixture.make();
      const committed = await world.transaction.run(ctx(), async () => ({
        result: null,
        change: { ...aChange('approvals'), narrowing: true },
      }));
      expect((await world.events()).at(-1)).to.deep.include({
        revision: committed.headRevision,
        previous: committed.headRevision - 1,
        touched: [{ kind: 'platform-settings', id: 'approvals' }],
        narrowing: true,
      });
    });

    if (fixture.atomic) {
      it('TX-8: an effect applied and then a failed commit is CP_UNAVAILABLE, logged once at error', async () => {
        const world = await fixture.make();
        const register = new CasRegister();
        const failed = await world.transaction
          .run(ctx(), async (tx) => {
            const { version } = register.read();
            tx.external(async () => {
              await register.replace(version, 'v1');
              await world.failCommit!(tx);
            });
            return { result: null, change: aChange() };
          })
          .catch((error: unknown) => error);
        expect(failed).to.be.instanceOf(ControlPlaneUnavailableError);
        expect(register.writes).to.equal(1);
        const errors = world.log.lines.filter(([level]) => level === 'error');
        expect(errors).to.have.length(1);
        expect(errors[0]![1]).to.equal('cp.change.unaudited');
        expect(errors[0]![2]).to.include({ orgId: ORG, objectType: 'platform-settings', objectId: 'labs' });
      });
    }
  });
}

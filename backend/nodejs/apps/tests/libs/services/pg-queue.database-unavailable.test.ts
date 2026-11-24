import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';
import * as runtimeBoot from '../../../src/libs/db/runtime-boot';
import {
  ConnectionLost,
  DbOverloaded,
  DbTimeout,
  PoolTimeout,
} from '../../../src/libs/db/errors';
import { mapPgStoreError } from '../../../src/libs/repositories/pg';
import {
  BasePgQueueConsumerConnection,
  failedOnDatabaseOnly,
} from '../../../src/libs/services/pg-queue.service';

class TestConsumer extends BasePgQueueConsumerConnection {}

/** One delivery row as the SQL keeps it: a claim adds an attempt, a nack whose last parameter is 1 gives it back. */
function queue() {
  const row = { attempts: 0, state: 'ready', refunds: [] as number[], delays: [] as number[] };
  const tx = {
    query: sinon.stub().callsFake(async (sql: string, args: unknown[]) => {
      if (sql.includes('pipeshub:queue.nack')) {
        const refund = Number(args[6] ?? 0); // a nack without the parameter (the code before refunds) gives nothing back
        row.refunds.push(refund);
        row.delays.push(args[5] as number);
        row.attempts -= refund;
      } else if (sql.includes('pipeshub:queue.ack')) row.state = 'acked';
      else if (sql.includes('pipeshub:queue.dead')) row.state = 'dead';
      return { rows: [], rowCount: 1 };
    }),
  };
  sinon.stub(runtimeBoot, 'ensurePgRuntime').returns({
    withUow: (_opts: unknown, fn: (t: unknown) => Promise<unknown>) => fn(tx),
  } as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>);
  return row;
}

/** Deliver the row until it is acked or dead; the handler throws `failures` in turn, then succeeds. Returns the handler runs. */
async function deliver(row: ReturnType<typeof queue>, failures: Array<() => unknown>): Promise<number> {
  const consumer = new TestConsumer({ groupId: 'g' } as never, createMockLogger() as never);
  const settle = (consumer as unknown as {
    settle: (r: unknown, h: () => Promise<void>) => Promise<void>;
  }).settle.bind(consumer);
  let runs = 0;
  const handler = async (): Promise<void> => {
    runs += 1;
    if (runs <= failures.length) throw failures[runs - 1]!();
  };
  for (let i = 0; i < 50 && row.state === 'ready'; i++) {
    row.attempts += 1;
    await settle(
      { topic: 't', message_id: '1', attempts: row.attempts, value: JSON.stringify({ i: 0 }), msg_key: null, headers: null },
      handler,
    );
  }
  return runs;
}

const lost = (): unknown => new ConnectionLost(false, new Error('connection terminated'));

describe('PostgreSQL queue consumer: a database that cannot be reached', () => {
  beforeEach(() => {
    process.env.MAX_DELIVERY_ATTEMPTS = '3';
    process.env.PG_QUEUE_HANDBACK_LIMIT = '4';
  });
  afterEach(() => {
    sinon.restore();
    delete process.env.MAX_DELIVERY_ATTEMPTS;
    delete process.env.PG_QUEUE_HANDBACK_LIMIT;
  });

  for (const [name, make] of [
    ['a lost connection', lost],
    ['a lost connection behind the repository 503', () => mapPgStoreError(new ConnectionLost(false))],
    ['a pool wait', () => mapPgStoreError(new PoolTimeout())],
    ['a server out of connection slots', () => new DbOverloaded('53300')],
  ] as const) {
    it(`${name} for more deliveries than the attempt limit does not dead-letter a good event`, async () => {
      const row = queue();
      const runs = await deliver(row, Array(4).fill(make)); // more than MAX_DELIVERY_ATTEMPTS (3)
      expect(row.state).to.equal('acked');
      expect(runs).to.equal(5);
      expect(row.refunds).to.deep.equal([1, 1, 1, 1]);
      expect(row.delays).to.deep.equal([...row.delays].sort((a, b) => a - b)); // backs off, never faster
    });
  }

  for (const [name, make] of [
    ['a TypeError', () => new TypeError('bad call')],
    ['a plain Error', () => new Error('bad input')],
    ['a lost connection after COMMIT was sent', () => new ConnectionLost(true)],
    ['a statement timeout', () => mapPgStoreError(new DbTimeout())],
    ['a full disk', () => new DbOverloaded('53100')],
    ['a TypeError caused by a lost connection', () => Object.assign(new TypeError('x'), { cause: lost() })],
  ] as const) {
    it(`${name} still dead-letters after the attempt limit`, async () => {
      const row = queue();
      const runs = await deliver(row, Array(10).fill(make));
      expect(row.state).to.equal('dead');
      expect(runs).to.equal(3);
      expect(row.refunds).to.deep.equal([0, 0]);
    });
  }

  it('a database that never comes back is dead-lettered once the hand-backs pass the limit', async () => {
    const row = queue();
    const runs = await deliver(row, Array(100).fill(lost));
    expect(row.state).to.equal('dead');
    expect(row.refunds).to.deep.equal([1, 1, 1, 1, 0, 0]); // four refunded (PG_QUEUE_HANDBACK_LIMIT), then the attempts are spent
    expect(runs).to.equal(7);
  });

  it('reads the cause of a 503 the repository raised', () => {
    expect(failedOnDatabaseOnly(mapPgStoreError(new ConnectionLost(false)))).to.equal(true);
    expect(failedOnDatabaseOnly(mapPgStoreError(new ConnectionLost(true)))).to.equal(false);
  });
});

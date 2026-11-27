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
  configureEnvelope,
  renewEnvelope,
  sign,
  MAX_FUTURE_SKEW_SECONDS,
} from '../../../src/libs/context/envelope';
import {
  BasePgQueueConsumerConnection,
  failedOnDatabaseOnly,
} from '../../../src/libs/services/pg-queue.service';

class TestConsumer extends BasePgQueueConsumerConnection {}

/** One delivery row as the SQL keeps it: a claim adds an attempt, a nack whose last parameter is 1 gives it back. */
function queue(
  fail: {
    ack?: number;
    nack?: number;
    dead?: number;
    clock?: { s: number };
  } = {},
) {
  const row = {
    attempts: 0,
    state: 'ready',
    refunds: [] as number[],
    delays: [] as number[],
    nackTries: 0,
    values: [] as string[],
  };
  let acksToFail = fail.ack ?? 0;
  let nacksToFail = fail.nack ?? 0;
  let deadsToFail = fail.dead ?? 0;
  const tx = {
    query: sinon.stub().callsFake(async (sql: string, args: unknown[]) => {
      if (sql.includes('pipeshub:queue.nack')) {
        row.nackTries += 1;
        if (fail.clock) fail.clock.s += 600; // each try of the hand-back comes 10 minutes after the last
      }
      if (sql.includes('pipeshub:queue.dead') && deadsToFail > 0) {
        deadsToFail -= 1;
        throw mapPgStoreError(new PoolTimeout()); // the message was refused; recording that could not get a connection
      }
      if (sql.includes('pipeshub:queue.ack') && acksToFail > 0) {
        acksToFail -= 1;
        throw mapPgStoreError(new PoolTimeout()); // the handler finished; the acknowledgement could not get a connection
      }
      if (sql.includes('pipeshub:queue.nack') && nacksToFail > 0) {
        nacksToFail -= 1;
        throw new ConnectionLost(false); // the outage outlasts the handler: the hand-back cannot be written either
      }
      if (sql.includes('pipeshub:queue.reissue'))
        row.values.push(args[1] as string);
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
async function deliver(
  row: ReturnType<typeof queue>,
  failures: Array<() => unknown>,
  value: unknown = { i: 0 },
): Promise<number> {
  const consumer = new TestConsumer(
    { groupId: 'g' } as never,
    createMockLogger() as never,
  );
  (consumer as unknown as { running: boolean }).running = true;
  const settle = (
    consumer as unknown as {
      settle: (r: unknown, h: () => Promise<void>) => Promise<void>;
    }
  ).settle.bind(consumer);
  let runs = 0;
  const handler = async (): Promise<void> => {
    runs += 1;
    if (runs <= failures.length) throw failures[runs - 1]!();
  };
  for (let i = 0; i < 50 && row.state === 'ready'; i++) {
    row.attempts += 1;
    await settle(
      {
        topic: 't',
        message_id: '1',
        attempts: row.attempts,
        value: JSON.stringify(
          row.values.at(-1) ? JSON.parse(row.values.at(-1)!) : value,
        ),
        msg_key: null,
        headers: null,
      },
      handler,
    );
  }
  return runs;
}

const lost = (): unknown =>
  new ConnectionLost(false, new Error('connection terminated'));

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
    [
      'a lost connection behind the repository 503',
      () => mapPgStoreError(new ConnectionLost(false)),
    ],
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
    [
      'a TypeError caused by a lost connection',
      () => Object.assign(new TypeError('x'), { cause: lost() }),
    ],
    // a misconfigured address: waiting cannot fix it, so it must not hold the message for the whole hand-back backstop
    [
      'a wrong password',
      () =>
        mapPgStoreError(
          Object.assign(new Error('password authentication failed'), {
            code: '28P01',
          }),
        ),
    ],
    [
      'a database that does not exist',
      () =>
        mapPgStoreError(
          Object.assign(new Error('database "nope" does not exist'), {
            code: '3D000',
          }),
        ),
    ],
    [
      'a host name that does not resolve',
      () =>
        mapPgStoreError(
          new ConnectionLost(
            false,
            Object.assign(new Error('getaddrinfo ENOTFOUND db'), {
              code: 'ENOTFOUND',
            }),
          ),
        ),
    ],
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

  it('an acknowledgement the outage failed after the handler finished is charged, not refunded: the handler does not rerun 200 times', async () => {
    const row = queue({ ack: 10 });
    const runs = await deliver(row, []);
    expect(row.refunds).to.deep.equal([0, 0]);
    expect(row.state).to.equal('dead');
    expect(runs).to.equal(3);
  });

  it('a refund survives a hand-back the outage also failed: it is written once the database answers', async () => {
    process.env.PG_QUEUE_NACK_RETRY_MS = '0';
    try {
      const row = queue({ nack: 3 });
      const runs = await deliver(row, [lost]);
      expect(row.nackTries).to.equal(4);
      expect(row.refunds).to.deep.equal([1]);
      expect(row.state).to.equal('acked');
      expect(runs).to.equal(2);
    } finally {
      delete process.env.PG_QUEUE_NACK_RETRY_MS;
    }
  });

  it('a hand-back signs the event again, keeping its id, so an outage longer than the envelope age does not dead-letter it as stale', async () => {
    const key = Buffer.alloc(32, 7);
    const ctx = {
      orgId: 'aaaaaaaaaaaaaaaaaaaaaaaa',
      userId: null,
      requestId: 'r',
    } as never;
    const old = Math.floor(Date.now() / 1000) - 60;
    const signed = sign(
      key,
      't',
      { orgId: 'aaaaaaaaaaaaaaaaaaaaaaaa', i: 0 },
      ctx,
      old,
      'e',
    );
    configureEnvelope(async () => [key], null, key);
    try {
      const row = queue();
      await deliver(row, [lost], signed);
      expect(row.values).to.have.length(1);
      const fresh = JSON.parse(row.values[0]!) as typeof signed;
      expect(fresh.ctx.eventId).to.equal(signed.ctx.eventId);
      expect(fresh.payload).to.equal(signed.payload);
      expect(fresh.ctx.issuedAt).to.be.greaterThan(old);
      expect(fresh.ctx.sig).to.not.equal(signed.ctx.sig);
      expect(
        renewEnvelope('t', {
          ...signed,
          ctx: { ...signed.ctx, issuedAt: old - 100000 },
        }),
      ).to.equal(null); // already stale: never laundered
      expect(renewEnvelope('t', { ...signed, payload: '{}' })).to.equal(null); // a signature that does not verify
    } finally {
      configureEnvelope(null);
    }
  });

  describe('with signed envelopes', () => {
    const key = Buffer.alloc(32, 7);
    const ctx = {
      orgId: 'aaaaaaaaaaaaaaaaaaaaaaaa',
      userId: null,
      requestId: 'r',
    } as never;
    const signedAt = (issuedAt: number) =>
      sign(
        key,
        't',
        { orgId: 'aaaaaaaaaaaaaaaaaaaaaaaa', i: 0 },
        ctx,
        issuedAt,
        'e',
      );
    beforeEach(() => {
      process.env.ORG_CONTEXT_MODE = 'enforce';
      process.env.PG_QUEUE_NACK_RETRY_MS = '0';
      configureEnvelope(async () => [key], null, key);
    });
    afterEach(() => {
      delete process.env.ORG_CONTEXT_MODE;
      delete process.env.PG_QUEUE_NACK_RETRY_MS;
      configureEnvelope(null);
    });

    it('a message dated too far ahead is never signed again: its refusal that the outage failed to record is not handed back', async () => {
      // an hour past the bound: a second that ticks between signing and the check must not bring it back inside
      const ahead = signedAt(Math.floor(Date.now() / 1000) + MAX_FUTURE_SKEW_SECONDS + 3600);
      expect(renewEnvelope('t', ahead)).to.equal(null);
      const row = queue({ dead: 1 });
      const runs = await deliver(row, [], ahead);
      expect(runs).to.equal(0);
      expect(row.values).to.deep.equal([]);
      expect(row.refunds.every((r) => r === 0)).to.equal(true);
    });

    it('an outage longer than the envelope lifetime still delivers the message: each try signs again from the last signature', async () => {
      const clock = { s: 0 };
      const now = Date.now();
      sinon.stub(Date, 'now').callsFake(() => now + clock.s * 1000);
      const row = queue({ nack: 3, clock });
      const runs = await deliver(row, [lost], signedAt(Math.floor(now / 1000)));
      expect(clock.s).to.be.greaterThan(900);
      expect(row.state).to.equal('acked');
      expect(runs).to.equal(2);
    });
  });

  it('reads the cause of a 503 the repository raised', () => {
    expect(
      failedOnDatabaseOnly(mapPgStoreError(new ConnectionLost(false))),
    ).to.equal(true);
    expect(
      failedOnDatabaseOnly(mapPgStoreError(new ConnectionLost(true))),
    ).to.equal(false);
  });
});

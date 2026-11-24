/**
 * The durable consumer inbox (`platform.consumer_inbox`) on a real PostgreSQL behind PgBouncer: what the Node consumers skip,
 * what they record, per org and per group, the notification consumer storing an event once however often the queue delivers
 * it, and the jobs-role sweep.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { randomUUID } from 'node:crypto';
import {
  DuplicateEnvelope,
  configureInbox,
  replayWindowSeconds,
  withDeliveryRecorded,
  runWithEnvelope,
} from '../../src/libs/context/envelope';
import { PgInbox, inboxMessageId } from '../../src/libs/services/consumer-inbox';
import {
  BasePgQueueConsumerConnection,
  BasePgQueueProducerConnection,
} from '../../src/libs/services/pg-queue.service';
import { TtlSweeper } from '../../src/libs/services/ttl-sweeper.service';
import { PgNotificationRepository } from '../../src/libs/repositories/notification/pg';
import { setNotificationRepository } from '../../src/libs/repositories/notification';
import { PgUnitOfWork, kernelTx } from '../../src/libs/repositories/pg';
import { NotificationConsumer } from '../../src/modules/notification/service/notification.consumer';
import { Logger } from '../../src/libs/services/logger.service';
import { activeUnit, withUow } from '../../src/libs/db';
import { isRetryableStoreError } from '../../src/libs/repositories/errors';
import { PG_BINS, PgStack, haveStack, inOrg, newId, startPgStack } from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;
const logger = Logger.getInstance({ service: 'consumer-inbox-itest' });
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

class Producer extends BasePgQueueProducerConnection {}
class Consumer extends BasePgQueueConsumerConnection {}

async function until(cond: () => Promise<boolean> | boolean, ms = 20_000): Promise<void> {
  const end = Date.now() + ms;
  while (!(await cond())) {
    if (Date.now() > end) throw new Error('condition not reached in time');
    await sleep(50);
  }
}

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL consumer inbox (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    const inbox = new PgInbox();
    const notifications = new PgNotificationRepository();
    const [A, B] = [newId(), newId()];
    const sql = (text: string, v?: unknown[]) => stack.admin(async (c) => (await c.query(text, v)).rows);
    const rowsOf = async (org: string) =>
      (await sql('SELECT message_id FROM platform.consumer_inbox WHERE org_id = $1 ORDER BY message_id', [org])).map(
        (r: { message_id: string }) => r.message_id,
      );
    const stored = async (org: string, type: string) =>
      Number((await sql('SELECT count(*)::int AS n FROM platform.notifications WHERE org_id = $1 AND type = $2', [org, type]))[0].n);
    const env: Record<string, string | undefined> = {};

    before(async () => {
      stack = await startPgStack(bin, 'consumer_inbox', { jobs: true });
      for (const k of ['PG_QUEUE_RETRY_BASE_S', 'MAX_DELIVERY_ATTEMPTS']) env[k] = process.env[k];
      process.env.PG_QUEUE_RETRY_BASE_S = '0.1';
      process.env.MAX_DELIVERY_ATTEMPTS = '5';
      setNotificationRepository(notifications);
    });
    after(async () => {
      configureInbox(null);
      setNotificationRepository();
      for (const [k, v] of Object.entries(env)) {
        if (v === undefined) delete process.env[k];
        else process.env[k] = v;
      }
      await stack?.stop();
    });
    afterEach(() => configureInbox(null));

    it('records an event per org and per group, and a row is visible only to its own org', async () => {
      const e = randomUUID();
      expect(await inbox.seen('g', e, A)).to.equal(false);
      await inbox.record('g', e, A);
      await inbox.record('g', e, A); // a second record is a no-op
      expect(await inbox.seen('g', e, A)).to.equal(true);
      expect(await inbox.seen('g2', e, A)).to.equal(false);
      expect(await inbox.seen('g', e, B)).to.equal(false);
      expect(await rowsOf(A)).to.include(inboxMessageId('g', e));
      // row security: org B's unit counts none of org A's rows
      const seenByB = await inOrg(B, () =>
        new PgUnitOfWork().run(
          async (tx) =>
            (await kernelTx(tx).query<{ n: number }>('/* pipeshub:test.inbox_count */ SELECT count(*)::int AS n FROM platform.consumer_inbox')).rows[0]?.n,
          { readOnly: true },
        ),
      );
      expect(seenByB).to.equal(0);
    });

    it('a group name cannot spell another group and event id', () => {
      expect(inboxMessageId('a:b', 'c')).to.not.equal(inboxMessageId('a', 'b:c'));
    });

    it('recordWith commits the effect with the record; a failed effect leaves no row; a later copy is refused and rolled back', async () => {
      const e = randomUUID();
      const add = (type: string) => () =>
        notifications.insertMany([{ orgId: A, assignedTo: newId(), type }]);
      let thrown: unknown;
      try {
        await inOrg(A, () => inbox.recordWith('g', e, A, async () => { await add('rw-fail')(); throw new Error('effect failed'); }));
      } catch (err) {
        thrown = err;
      }
      expect((thrown as Error).message).to.equal('effect failed');
      expect(await stored(A, 'rw-fail')).to.equal(0);
      expect(await inbox.seen('g', e, A)).to.equal(false);

      await inOrg(A, () => inbox.recordWith('g', e, A, add('rw')));
      expect(await stored(A, 'rw')).to.equal(1);
      thrown = undefined;
      try {
        await inOrg(A, () => inbox.recordWith('g', e, A, add('rw')));
      } catch (err) {
        thrown = err;
      }
      expect(thrown).to.be.instanceOf(DuplicateEnvelope);
      expect(await stored(A, 'rw')).to.equal(1);
    });

    it('two deliveries of one event racing in recordWith store it once', async () => {
      const e = randomUUID();
      const effect = () => notifications.insertMany([{ orgId: A, assignedTo: newId(), type: 'race' }]);
      const outcomes = await Promise.allSettled(
        [0, 1, 2].map(() => inOrg(A, () => inbox.recordWith('g', e, A, async () => { const r = await effect(); await sleep(200); return r; }))),
      );
      expect(outcomes.filter((o) => o.status === 'fulfilled')).to.have.length(1);
      for (const o of outcomes.filter((x) => x.status === 'rejected'))
        expect((o as PromiseRejectedResult).reason).to.be.instanceOf(DuplicateEnvelope);
      expect(await stored(A, 'race')).to.equal(1);
    });

    it('the notification consumer on the PostgreSQL queue stores an event once when it is delivered twice and when it is replayed', async () => {
      configureInbox(inbox);
      const topic = `notification-${randomUUID().slice(0, 8)}`;
      const pushes: string[] = [];
      const service = { sendToUser: (userId: string) => pushes.push(userId) };
      const queue = new Consumer({ groupId: 'notification-consumer-group', clientId: 'c1', claimMinIdleMs: 30_000 }, logger);
      const consumer = new NotificationConsumer(queue, logger, service as never);
      const user = newId();
      const event = { orgId: A, type: 'inbox.replay', recipientUserIds: [user], title: 't', eventId: randomUUID() };
      try {
        await consumer.start();
        await consumer.subscribe([topic], true);
        await consumer.consume(async () => {});
        const producer = new Producer(logger);
        // the producer keeps an event id the message already names, so these are the same event written twice
        await producer.publish(topic, { key: 'k', value: event as never });
        await producer.publish(topic, { key: 'k', value: event as never });
        await until(async () => (await sql('SELECT count(*)::int AS n FROM platform.queue_deliveries WHERE topic = $1', [topic]))[0].n === 0);
        // a replay written after the first was acknowledged
        await producer.publish(topic, { key: 'k', value: event as never });
        await until(async () => (await sql('SELECT count(*)::int AS n FROM platform.queue_deliveries WHERE topic = $1', [topic]))[0].n === 0);
        // a different event of the same shape is stored
        await producer.publish(topic, { key: 'k', value: { ...event, eventId: randomUUID() } as never });
        await until(async () => (await stored(A, 'inbox.replay')) === 2);
        await sleep(300);
        expect(await stored(A, 'inbox.replay')).to.equal(2);
        expect(pushes).to.deep.equal([user, user]);
        expect(await inbox.seen('notification-consumer-group', event.eventId, A)).to.equal(true);
      } finally {
        await consumer.stop();
      }
    });

    it('the notification consumer stores every event once while the server keeps cutting its sessions mid-statement', async () => {
      configureInbox(inbox);
      const topic = `notification-${randomUUID().slice(0, 8)}`;
      const queue = new Consumer({ groupId: 'notification-consumer-group', clientId: 'c1', claimMinIdleMs: 1_000 }, logger);
      const consumer = new NotificationConsumer(queue, logger, { sendToUser: () => undefined } as never);
      const user = newId();
      const events = Array.from({ length: 40 }, (_, i) => ({ orgId: B, type: 'inbox.cut', recipientUserIds: [user], title: `t${i}`, eventId: randomUUID() }));
      await consumer.start();
      await consumer.subscribe([topic], true);
      await consumer.consume(async () => {});
      let cutting = true;
      let cuts = 0;
      const cut = (async () => {
        while (cutting) {
          const [r] = await sql("SELECT count(*) FILTER (WHERE pg_terminate_backend(pid))::int AS n FROM pg_stat_activity WHERE usename = 'pipeshub_node_login' AND datname = current_database()");
          cuts += Number(r.n);
          await sleep(150);
        }
      })();
      try {
        try {
          const producer = new Producer(logger);
          // bounded, so a publish that never recovers fails the test and still stops the cuts and the consumer
          const deadline = Date.now() + 45_000;
          for (const event of events) {
            // a send that failed may have committed: send the same event again until one returns, as a retrying producer does
            for (;;) {
              try {
                await producer.publish(topic, { key: 'k', value: event as never });
                break;
              } catch (error) {
                if (Date.now() > deadline) throw error;
                await sleep(50);
              }
            }
          }
          await sleep(2_000);
        } finally {
          cutting = false;
          await cut;
        }
        await until(async () => (await sql('SELECT count(*)::int AS n FROM platform.queue_deliveries WHERE topic = $1', [topic]))[0].n === 0, 60_000);
        const rows = await sql('SELECT title, count(*)::int AS n FROM platform.notifications WHERE org_id = $1 AND type = $2 GROUP BY title', [B, 'inbox.cut']);
        expect(cuts).to.be.greaterThan(0);
        expect(Object.fromEntries(rows.map((r: { title: string; n: number }) => [r.title, r.n]))).to.deep.equal(
          Object.fromEntries(events.map((e) => [e.title, 1])),
        );
      } finally {
        await consumer.stop();
      }
    });

    it('the sweep removes rows past the replay window in every org and keeps newer ones; the service login cannot delete', async () => {
      const [oldA, oldB, fresh] = [randomUUID(), randomUUID(), randomUUID()];
      await inbox.record('g', oldA, A);
      await inbox.record('g', oldB, B);
      await inbox.record('g', fresh, A);
      const past = new Date(Date.now() - (replayWindowSeconds() + 60) * 1000);
      await sql('UPDATE platform.consumer_inbox SET received_at = $1 WHERE message_id = ANY($2)', [
        past,
        [inboxMessageId('g', oldA), inboxMessageId('g', oldB)],
      ]);
      const sweeper = new TtlSweeper({ info: sinon.stub(), error: sinon.stub() } as never);
      const removed = await sweeper.sweepOnce();
      expect(removed.consumer_inbox).to.be.at.least(2);
      expect(await inbox.seen('g', oldA, A)).to.equal(false);
      expect(await inbox.seen('g', oldB, B)).to.equal(false);
      expect(await inbox.seen('g', fresh, A)).to.equal(true);

      let refused: unknown;
      try {
        await inOrg(A, () =>
          withUow({ write: true }, (tx) =>
            tx.query('/* pipeshub:test.inbox_delete */ DELETE FROM platform.consumer_inbox WHERE org_id = $1', [A]),
          ),
        );
      } catch (err) {
        refused = err;
      }
      expect((refused as { sqlstate?: string })?.sqlstate).to.equal('42501');
      expect(await inbox.seen('g', fresh, A)).to.equal(true);
    });

    describe('a store refusal while the notification is being stored is retryable, not acknowledged', () => {
      const group = 'notification-consumer-group';
      // A broker like Kafka or Redis Streams: the message is acknowledged when the callback resolves, delivered again when it rejects.
      const fakeBroker = () => {
        let cb: ((m: { value: unknown }) => Promise<void>) | undefined;
        const broker = {
          isConnected: () => true,
          connect: async () => {},
          disconnect: async () => {},
          subscribe: async () => {},
          consume: async (h: typeof cb) => { cb = h; },
        };
        const deliver = (value: unknown) => runWithEnvelope('notification', JSON.parse(JSON.stringify(value)), (v) => cb!({ value: v }), group);
        return { broker, deliver };
      };
      const outcome = async (p: Promise<unknown>): Promise<'acked' | 'redelivered'> => {
        try {
          await p;
          return 'acked';
        } catch (e) {
          expect(isRetryableStoreError(e), `expected a retryable store error, got ${String(e)}`).to.equal(true);
          return 'redelivered';
        }
      };
      afterEach(() => setNotificationRepository(notifications));

      for (const cause of ['the store fence refuses the write', 'a statement times out after the inbox insert'] as const) {
        it(`${cause}: the delivery is redelivered, nothing is stored or recorded, and the redelivery stores once`, async () => {
          configureInbox(inbox);
          const { broker, deliver } = fakeBroker();
          const consumer = new NotificationConsumer(broker as never, logger, { sendToUser: () => {} } as never);
          await consumer.consume(async () => {});
          const type = `refused-${randomUUID().slice(0, 8)}`;
          const event = { orgId: A, type, recipientUserIds: [newId()], title: 't', eventId: randomUUID() };
          let fail = true;
          if (cause === 'the store fence refuses the write') {
            await sql('UPDATE platform.store_epoch SET epoch = epoch + 1');
          } else {
            setNotificationRepository({
              insertMany: async (docs: never[]) => {
                if (fail) {
                  const tx = activeUnit()!;
                  await tx.query("/* pipeshub:test.inbox_slow */ SELECT set_config('statement_timeout', '50', true)");
                  await tx.query('/* pipeshub:test.inbox_slow */ SELECT pg_sleep(1)');
                }
                return notifications.insertMany(docs);
              },
            } as never);
          }
          try {
            expect(await outcome(deliver(event))).to.equal('redelivered');
          } finally {
            if (cause === 'the store fence refuses the write') await sql('UPDATE platform.store_epoch SET epoch = epoch - 1');
            fail = false;
          }
          expect(await stored(A, type)).to.equal(0);
          expect(await inbox.seen(group, event.eventId, A)).to.equal(false);
          expect(await outcome(deliver(event))).to.equal('acked');
          expect(await outcome(deliver(event))).to.equal('acked');
          expect(await stored(A, type)).to.equal(1);
          expect(await inbox.seen(group, event.eventId, A)).to.equal(true);
        });
      }
    });

    it('withDeliveryRecorded outside a delivery runs the effect alone', async () => {
      const r = await inOrg(A, () => withDeliveryRecorded(async () => 'ran'));
      expect(r).to.equal('ran');
    });
  });
}

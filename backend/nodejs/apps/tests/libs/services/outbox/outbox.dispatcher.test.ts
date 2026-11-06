import 'reflect-metadata';
import { EventEmitter } from 'events';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  OutboxDispatcher,
  wakeOutbox,
} from '../../../../src/libs/services/outbox/outbox.dispatcher';
import { OutboxEvent } from '../../../../src/libs/repositories/outbox/schema/outbox.schema';

function makeLogger() {
  return {
    info: sinon.stub(),
    debug: sinon.stub(),
    warn: sinon.stub(),
    error: sinon.stub(),
  };
}

function makeProducer(overrides: Record<string, unknown> = {}) {
  return {
    isConnected: sinon.stub().returns(true),
    connect: sinon.stub().resolves(),
    publish: sinon.stub().resolves(),
    ...overrides,
  };
}

function row(extra: Record<string, unknown> = {}) {
  return {
    _id: new mongoose.Types.ObjectId(),
    topic: 'entity-events',
    key: 'userAdded',
    orderingKey: 'user:org-1:user-1',
    createdAt: new Date(),
    value: '{"eventType":"userAdded"}',
    headers: { eventType: 'userAdded' },
    attempts: 0,
    status: 'pending',
    ...extra,
  };
}

/**
 * Drives one claim per pass and then an empty queue, which is how the
 * dispatcher sees things: take the oldest unblocked row, deliver it, look
 * again, stop when there is nothing left.
 *
 * `blocked` decides what the ordering check finds: null means nothing older
 * about this entity is outstanding, so the row is free to go.
 */
function stubClaims(rows: unknown[], blocked: unknown = null) {
  const page = (rowsForPage: unknown[]) =>
    ({
      sort: () => ({
        skip: (n: number) => ({
          limit: () => ({ exec: async () => (n === 0 ? rowsForPage : []) }),
        }),
      }),
    }) as any;

  const find = sinon.stub(OutboxEvent, 'find');
  rows.forEach((r, i) => find.onCall(i).returns(page([r])));
  find.onCall(rows.length).returns(page([]));

  const exists = sinon.stub(OutboxEvent, 'exists').resolves(blocked as any);
  const claim = sinon.stub(OutboxEvent, 'findOneAndUpdate');
  rows.forEach((r, i) => claim.onCall(i).returns({ exec: async () => r } as any));
  return { find, exists, claim };
}

describe('OutboxDispatcher', () => {
  let update: sinon.SinonStub;

  beforeEach(() => {
    update = sinon
      .stub(OutboxEvent, 'updateOne')
      .returns({ exec: async () => ({}) } as any);
  });

  afterEach(() => sinon.restore());

  it('publishes a queued event and marks it delivered', async () => {
    const event = row();
    stubClaims([event]);
    const producer = makeProducer();

    const delivered = await new OutboxDispatcher(
      producer as any,
      makeLogger() as any,
    ).drain();

    expect(delivered).to.equal(1);
    expect(producer.publish.calledOnce).to.equal(true);
    expect(producer.publish.firstCall.args[0]).to.equal('entity-events');
    expect(producer.publish.firstCall.args[1]).to.include({
      key: 'userAdded',
      value: '{"eventType":"userAdded"}',
    });
    expect(update.firstCall.args[1].$set.status).to.equal('published');
  });

  it('keeps a failed event for another try instead of losing it', async () => {
    // This is the whole point: the old code logged the failure and returned,
    // leaving the caller believing the event had been delivered.
    stubClaims([row()]);
    const producer = makeProducer({
      publish: sinon.stub().rejects(new Error('broker unreachable')),
    });

    const delivered = await new OutboxDispatcher(
      producer as any,
      makeLogger() as any,
    ).drain();

    expect(delivered).to.equal(0);
    const set = update.firstCall.args[1].$set;
    expect(set.status).to.equal('pending');
    expect(set.attempts).to.equal(1);
    expect(set.lastError).to.contain('broker unreachable');
    expect(set.nextAttemptAt).to.be.a('date');
  });

  it('backs off further on each successive failure', async () => {
    stubClaims([row({ attempts: 1 }), row({ attempts: 4 })]);
    const producer = makeProducer({
      publish: sinon.stub().rejects(new Error('still down')),
    });

    await new OutboxDispatcher(producer as any, makeLogger() as any).drain();

    const firstWait =
      update.firstCall.args[1].$set.nextAttemptAt.getTime() - Date.now();
    const laterWait =
      update.secondCall.args[1].$set.nextAttemptAt.getTime() - Date.now();
    expect(laterWait).to.be.greaterThan(firstWait);
  });

  it('never gives up on an event, however many times it has failed', async () => {
    // An earlier version parked a row as `failed` after enough attempts. The
    // claim query never looks at failed rows, so that was a silent drop in
    // disguise — precisely the bug this mechanism exists to remove.
    stubClaims([row({ attempts: 99 })]);
    const logger = makeLogger();
    const producer = makeProducer({
      publish: sinon.stub().rejects(new Error('still down')),
    });

    await new OutboxDispatcher(producer as any, logger as any).drain();

    expect(update.firstCall.args[1].$set.status).to.equal('pending');
    expect(update.firstCall.args[1].$set.attempts).to.equal(100);
    expect(logger.error.called).to.equal(true);
  });

  it('holds an event back while something older about the same entity is stuck', async () => {
    // A failed event returns to pending with a future retry time. Without
    // this check the next pass would deliver a later event about the same
    // user — an update, or a deletion — ahead of it.
    const { claim } = stubClaims([row()], { _id: 'an-older-row' });
    const producer = makeProducer();

    const delivered = await new OutboxDispatcher(
      producer as any,
      makeLogger() as any,
    ).drain();

    expect(delivered).to.equal(0);
    expect(claim.called).to.equal(false);
    expect(producer.publish.called).to.equal(false);
  });

  it('warns quietly at first and escalates to an error', async () => {
    stubClaims([row({ attempts: 0 })]);
    const early = makeLogger();
    await new OutboxDispatcher(
      makeProducer({ publish: sinon.stub().rejects(new Error('blip')) }) as any,
      early as any,
    ).drain();
    expect(early.warn.called).to.equal(true);
    expect(early.error.called).to.equal(false);

    sinon.restore();
    update = sinon
      .stub(OutboxEvent, 'updateOne')
      .returns({ exec: async () => ({}) } as any);
    stubClaims([row({ attempts: 8 })]);
    const persistent = makeLogger();
    await new OutboxDispatcher(
      makeProducer({ publish: sinon.stub().rejects(new Error('still down')) }) as any,
      persistent as any,
    ).drain();
    expect(persistent.error.called).to.equal(true);
  });

  it('claims only events that are due, and reclaims abandoned ones', async () => {
    const { find, exists } = stubClaims([]);
    await new OutboxDispatcher(
      makeProducer() as any,
      makeLogger() as any,
    ).drain();

    const query = find.firstCall.args[0] as { $or: Record<string, unknown>[] };
    // Due pending work.
    expect(query.$or[0]).to.have.property('status', 'pending');
    expect(query.$or[0]).to.have.property('nextAttemptAt');
    // And rows whose holder died mid-publish, which would otherwise stick.
    expect(query.$or[1]).to.have.property('status', 'publishing');
    expect(query.$or[1]).to.have.property('claimedAt');
    // And the ordering check is what keeps one entity's events in sequence.
    expect(exists.called).to.equal(false);
  });

  it('walks past a window full of blocked rows to reach one that can go', async () => {
    // Blocked rows are themselves due — it is their predecessor that is in
    // backoff — so after an outage they fill the front of the queue. Reading
    // one batch and stopping would starve every unrelated entity until those
    // predecessors came due, which can be five minutes.
    const blockedRows = Array.from({ length: 20 }, () =>
      row({ orderingKey: 'user:org-1:stuck' }),
    );
    const free = row({ orderingKey: 'user:org-1:free' });

    let call = 0;
    sinon.stub(OutboxEvent, 'find').callsFake(
      () =>
        ({
          sort: () => ({
            skip: (n: number) => ({
              limit: () => ({
                exec: async () => {
                  call += 1;
                  // Second pass of the drain loop: the queue is empty.
                  if (call > 2) return [];
                  return n === 0 ? blockedRows : [free];
                },
              }),
            }),
          }),
        }) as any,
    );
    sinon
      .stub(OutboxEvent, 'exists')
      .callsFake(async (q: any) =>
        q.orderingKey === 'user:org-1:stuck' ? ({ _id: 'older' } as any) : null,
      );
    sinon
      .stub(OutboxEvent, 'findOneAndUpdate')
      .returns({ exec: async () => free } as any);
    const producer = makeProducer();

    const delivered = await new OutboxDispatcher(
      producer as any,
      makeLogger() as any,
    ).drain();

    expect(delivered).to.equal(1);
    expect(producer.publish.calledOnce).to.equal(true);
  });

  it('re-asserts the due condition when taking a row, not just its status', async () => {
    // Otherwise two instances can take the same abandoned row: the first
    // refreshes claimedAt but leaves the status at `publishing`, so a match
    // on status alone would still succeed for the second and both would
    // publish the same event.
    const { claim } = stubClaims([row({ status: 'publishing' })]);

    await new OutboxDispatcher(
      makeProducer() as any,
      makeLogger() as any,
    ).drain();

    const predicate = claim.firstCall.args[0] as Record<string, unknown>;
    expect(predicate).to.have.property('_id');
    expect(predicate).to.have.property('$or');
    const branches = predicate.$or as Record<string, unknown>[];
    expect(branches[1]).to.have.property('claimedAt');
  });

  it('connects the producer if it is not connected yet', async () => {
    stubClaims([row()]);
    const producer = makeProducer({ isConnected: sinon.stub().returns(false) });

    await new OutboxDispatcher(producer as any, makeLogger() as any).drain();

    expect(producer.connect.calledOnce).to.equal(true);
    expect(producer.publish.calledOnce).to.equal(true);
  });

  it('survives the database being unreachable', async () => {
    // A failing pass must not stop later ticks from running.
    sinon.stub(OutboxEvent, 'find').throws(new Error('no mongo'));
    const logger = makeLogger();

    const delivered = await new OutboxDispatcher(
      makeProducer() as any,
      logger as any,
    ).drain();

    expect(delivered).to.equal(0);
    expect(logger.error.called).to.equal(true);
  });

  it('does not start a second pass while one is running', async () => {
    let release: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    stubClaims([row(), row()]);
    const producer = makeProducer({
      publish: sinon.stub().callsFake(async () => gate),
    });
    const dispatcher = new OutboxDispatcher(producer as any, makeLogger() as any);

    const first = dispatcher.drain();
    const second = await dispatcher.drain();
    expect(second).to.equal(0);

    release!();
    await first;
  });

  describe('waking instead of waiting for the poll', () => {
    /** Lets the stubbed database calls of a pass run; nothing here waits on a timer. */
    const settle = async () => {
      for (let i = 0; i < 50; i += 1) await new Promise((resolve) => setImmediate(resolve));
    };
    /** A poll so long that only a wake can have delivered anything in a test. */
    const NEVER = 60 * 60 * 1000;
    let running: OutboxDispatcher[] = [];
    const startDispatcher = (producer: ReturnType<typeof makeProducer>, logger = makeLogger(), inserts: (() => unknown) | null = null) => {
      const dispatcher = new OutboxDispatcher(producer as any, logger as any, NEVER, { inserts: inserts as any });
      dispatcher.start();
      running.push(dispatcher);
      return dispatcher;
    };
    afterEach(async () => {
      await Promise.all(running.map((d) => d.stop()));
      running = [];
    });

    it('a row written outside a transaction goes out at once', async () => {
      stubClaims([row()]);
      const producer = makeProducer();
      startDispatcher(producer);

      wakeOutbox();
      await settle();

      expect(producer.publish.calledOnce).to.equal(true);
    });

    it('a row written in a transaction goes out once its session ends, never before the commit, and several rows wake it once', async () => {
      const { find } = stubClaims([row()]);
      const producer = makeProducer();
      startDispatcher(producer);
      const session = new EventEmitter();

      wakeOutbox(session as never);
      wakeOutbox(session as never);
      await settle();
      expect(find.called, 'nothing is looked for before the commit').to.equal(false);
      expect(session.listenerCount('ended')).to.equal(1);

      session.emit('ended');
      await settle();
      expect(producer.publish.calledOnce).to.equal(true);
    });

    it('a wake during a pass makes it look once more, so a row written meanwhile is not left for the poll', async () => {
      const first = row();
      const second = row({ orderingKey: 'user:org-1:user-2' });
      const page = (rows: unknown[]) =>
        ({ sort: () => ({ skip: (n: number) => ({ limit: () => ({ exec: async () => (n === 0 ? rows : []) }) }) }) }) as any;
      const find = sinon.stub(OutboxEvent, 'find');
      find.onCall(0).returns(page([first]));
      find.onCall(1).returns(page([]));
      find.onCall(2).returns(page([second]));
      find.returns(page([]));
      sinon.stub(OutboxEvent, 'exists').resolves(null as any);
      const claim = sinon.stub(OutboxEvent, 'findOneAndUpdate');
      claim.onCall(0).returns({ exec: async () => first } as any);
      claim.onCall(1).returns({ exec: async () => second } as any);
      let release!: () => void;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      const producer = makeProducer({ publish: sinon.stub().onFirstCall().callsFake(() => gate).resolves() });
      const dispatcher = startDispatcher(producer);

      const pass = dispatcher.drain();
      await settle();
      // The second row is written while the first is being published.
      wakeOutbox();
      release();
      expect(await pass).to.equal(2);
      expect(producer.publish.args.map((a) => a[1].key)).to.deep.equal(['userAdded', 'userAdded']);
      expect(find.callCount).to.equal(4);
    });

    it('a row another instance wrote wakes it through the insert stream; a stream that breaks opens again while the poll carries on', async () => {
      const clock = sinon.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
      try {
        stubClaims([row()]);
        const producer = makeProducer();
        const logger = makeLogger();
        const streams: Array<EventEmitter & { close: sinon.SinonStub }> = [];
        const open = sinon.stub().callsFake(() => {
          const stream = Object.assign(new EventEmitter(), { close: sinon.stub().resolves() });
          streams.push(stream);
          return stream;
        });
        const dispatcher = startDispatcher(producer, logger, open);
        expect(open.callCount).to.equal(1);

        streams[0]!.emit('change', { _id: { _data: 'token' } });
        await settle();
        expect(producer.publish.calledOnce).to.equal(true);

        streams[0]!.emit('error', new Error('primary stepped down'));
        expect(logger.warn.calledOnce).to.equal(true);
        expect(streams[0]!.close.calledOnce).to.equal(true);
        clock.tick(999);
        expect(open.callCount).to.equal(1);
        clock.tick(1);
        expect(open.callCount).to.equal(2);

        await dispatcher.stop();
        expect(streams[1]!.close.calledOnce).to.equal(true);
        clock.tick(60_000);
        expect(open.callCount).to.equal(2);
      } finally {
        clock.restore();
      }
    });

    it('a stopped dispatcher is not woken', async () => {
      const { find } = stubClaims([row()]);
      const dispatcher = startDispatcher(makeProducer());
      await dispatcher.stop();

      wakeOutbox();
      await settle();

      expect(find.called).to.equal(false);
    });

    it('opens no insert stream on a standalone server, where there are none', () => {
      const before = process.env.REPLICA_SET_AVAILABLE;
      process.env.REPLICA_SET_AVAILABLE = 'false';
      try {
        const logger = makeLogger();
        const dispatcher = new OutboxDispatcher(makeProducer() as any, logger as any, NEVER);
        dispatcher.start();
        running.push(dispatcher);
        expect(logger.info.firstCall.args[1]).to.deep.include({ wakesOnInsert: false });
      } finally {
        if (before === undefined) delete process.env.REPLICA_SET_AVAILABLE;
        else process.env.REPLICA_SET_AVAILABLE = before;
      }
    });
  });
});

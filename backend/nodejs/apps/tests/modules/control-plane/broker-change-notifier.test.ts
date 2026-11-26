/**
 * CPD-6 `BrokerChangeNotifier` (03-interfaces §5.4): CP-SKEL's notifier
 * contract (CN-1..3) over the broker clients, the wire a published event has,
 * what a subscriber does with a message that is not one, and listening
 * through a broker outage (NFR-37).
 */
import { expect } from 'chai';
import {
  BrokerChangeNotifier,
  changeEventMessage,
  ChangeSubscribers,
  CONTROL_PLANE_EVENTS_TOPIC,
  type ChangeEvent,
} from '../../../src/modules/control-plane';
import { Gate, RecordingLog } from './support/in-memory';
import { InMemoryBroker } from './support/in-memory-broker';
import { changeEvent, describeChangeNotifierContract } from './support/notifier-contract';
import { ORG_A } from './support/cp-objects';

const world = (broker = new InMemoryBroker(), log = new RecordingLog()) => {
  const notifier = new BrokerChangeNotifier({
    producer: broker.producer(),
    consumer: broker.consumer('this-process'),
    log,
  });
  return { broker, log, notifier };
};

describeChangeNotifierContract('BrokerChangeNotifier over the broker clients', async () => {
  const { broker, notifier } = world();
  await notifier.listen();
  return { notifier, settled: () => broker.settled() };
});

describe('CPD-6 BrokerChangeNotifier', () => {
  it('NTF-13: a published message is the envelope {eventType, payload: the event} as JSON, keyed by its org, on control-plane-events', async () => {
    const { broker, notifier } = world();
    const event = changeEvent(ORG_A, 4);

    await notifier.publish(event);

    expect(CONTROL_PLANE_EVENTS_TOPIC).to.equal('control-plane-events');
    const [entry] = broker.log(CONTROL_PLANE_EVENTS_TOPIC);
    expect(entry?.key).to.equal(ORG_A);
    // The clients encode a string value once more: a subscriber decodes twice.
    expect(JSON.parse(JSON.parse(entry?.value ?? 'null') as string)).to.deep.equal({ eventType: 'cp.changed', payload: event });
    expect(changeEventMessage(event)).to.deep.equal({
      topic: 'control-plane-events',
      key: ORG_A,
      orderingKey: `cp:${ORG_A}`,
      value: JSON.stringify({ eventType: 'cp.changed', payload: event }),
    });
  });

  it('every process has its own group: two notifiers on one broker both get every event', async () => {
    const broker = new InMemoryBroker();
    const got: Record<string, number[]> = { a: [], b: [] };
    const first = new BrokerChangeNotifier({ producer: broker.producer(), consumer: broker.consumer('node-a') });
    const second = new BrokerChangeNotifier({ producer: broker.producer(), consumer: broker.consumer('node-b') });
    first.subscribe((event) => got['a']?.push(event.revision));
    second.subscribe((event) => got['b']?.push(event.revision));
    await first.listen();
    await second.listen();

    await first.publish(changeEvent(ORG_A, 1));
    await second.publish(changeEvent(ORG_A, 2));
    await broker.settled();

    expect(got).to.deep.equal({ a: [1, 2], b: [1, 2] });
  });

  it('CN-3 for a process: one that starts listening late gets nothing published before it did', async () => {
    const broker = new InMemoryBroker();
    const early = new BrokerChangeNotifier({ producer: broker.producer(), consumer: broker.consumer('node-a') });
    await early.listen();
    await early.publish(changeEvent(ORG_A, 1));
    const late = new BrokerChangeNotifier({ producer: broker.producer(), consumer: broker.consumer('node-b') });
    const got: number[] = [];
    late.subscribe((event) => got.push(event.revision));

    await late.listen();
    await early.publish(changeEvent(ORG_A, 2));
    await broker.settled();

    expect(got).to.deep.equal([2]);
  });

  it('reads an envelope published as an object (the Python producer) as well as one published as a JSON string', async () => {
    const { broker, notifier } = world();
    const got: ChangeEvent[] = [];
    notifier.subscribe((event) => got.push(event));
    await notifier.listen();

    broker.inject(CONTROL_PLANE_EVENTS_TOPIC, ORG_A, JSON.stringify({ eventType: 'cp.changed', payload: changeEvent(ORG_A, 9), timestamp: 1 }));
    await broker.settled();

    expect(got).to.deep.equal([changeEvent(ORG_A, 9)]);
  });

  for (const [what, value] of [
    ['is not JSON inside its string', JSON.stringify('{not json')],
    ['is another event type', JSON.stringify(JSON.stringify({ eventType: 'agent_run_dispatch_requested', payload: changeEvent(ORG_A, 3) }))],
    ['is a bare event without its envelope', JSON.stringify(JSON.stringify(changeEvent(ORG_A, 3)))],
    ['is not an object', JSON.stringify('7')],
    [
      'carries a value of what was written',
      JSON.stringify(JSON.stringify({ eventType: 'cp.changed', payload: { ...changeEvent(ORG_A, 3), live: { org: { approvals: { writes: 'deny' } } } } })),
    ],
  ] as const) {
    it(`a message that ${what} is dropped and logged without its content; the next event still arrives`, async () => {
      const { broker, log, notifier } = world();
      const got: number[] = [];
      notifier.subscribe((event) => got.push(event.revision));
      await notifier.listen();

      broker.inject(CONTROL_PLANE_EVENTS_TOPIC, ORG_A, value);
      await notifier.publish(changeEvent(ORG_A, 4));
      await broker.settled();

      expect(got).to.deep.equal([4]);
      expect(log.lines).to.deep.equal([['warn', 'cp.change.unreadable', { event: 'cp.change.unreadable', key: ORG_A }]]);
    });
  }

  it('a subscriber that throws is logged and the others still get the event', async () => {
    const { broker, log, notifier } = world();
    const got: number[] = [];
    notifier.subscribe(() => {
      throw new TypeError('a handler bug');
    });
    notifier.subscribe((event) => got.push(event.revision));
    await notifier.listen();

    await notifier.publish(changeEvent(ORG_A, 1));
    await broker.settled();

    expect(got).to.deep.equal([1]);
    expect(log.lines).to.deep.equal([
      ['warn', 'cp.change.handler_failed', { event: 'cp.change.handler_failed', orgId: ORG_A, revision: 1, errorType: 'TypeError' }],
    ]);
  });

  it('a subscriber cannot change the event the next one gets', async () => {
    const subscribers = new ChangeSubscribers();
    const got: ChangeEvent[] = [];
    subscribers.subscribe((event) => {
      event.touched.length = 0;
    });
    subscribers.subscribe((event) => got.push(event));

    subscribers.deliver(changeEvent(ORG_A, 1));

    expect(got).to.deep.equal([changeEvent(ORG_A, 1)]);
  });

  it('NFR-37: with the broker down at start it keeps trying, logs each failure, and listens once the broker is back', async () => {
    const broker = new InMemoryBroker();
    broker.down = true;
    const { log, notifier } = world(broker);
    const got: number[] = [];
    notifier.subscribe((event) => got.push(event.revision));
    const waits: number[] = [];
    const retry = new Gate();
    const waiting = new Gate();

    const started = notifier.start(async (attempt) => {
      waits.push(attempt);
      if (attempt === 2) {
        waiting.open();
        await retry.opened;
      }
    });
    await waiting.opened;
    broker.down = false;
    retry.open();
    await started;

    await notifier.publish(changeEvent(ORG_A, 1));
    await broker.settled();
    expect(waits).to.deep.equal([1, 2]);
    expect(got).to.deep.equal([1]);
    expect(log.lines.map(([level, message, meta]) => [level, message, meta?.['attempt'], meta?.['errorType'], meta?.['choice']])).to.deep.equal([
      ['warn', 'cp.change.listen_failed', 1, 'BrokerDown', 'head_check'],
      ['warn', 'cp.change.listen_failed', 2, 'BrokerDown', 'head_check'],
    ]);
  });

  it('stop ends the retries of a notifier that never listened, and the consumer of one that did', async () => {
    const broker = new InMemoryBroker();
    broker.down = true;
    const never = world(broker).notifier;
    let attempts = 0;
    await never.start(async () => {
      attempts += 1;
      await never.stop();
    });
    expect(attempts).to.equal(1);

    broker.down = false;
    const consumer = broker.consumer('listening');
    const listening = new BrokerChangeNotifier({ producer: broker.producer(), consumer });
    const got: number[] = [];
    listening.subscribe((event) => got.push(event.revision));
    await listening.start(async () => undefined);
    await listening.listen();
    await listening.stop();
    await listening.publish(changeEvent(ORG_A, 1));
    await broker.settled();
    expect(consumer.isConnected()).to.equal(false);
    expect(got).to.deep.equal([]);
  });

  it('publish connects the producer once and reports a broker that is down to its caller', async () => {
    const broker = new InMemoryBroker();
    const producer = broker.producer();
    const notifier = new BrokerChangeNotifier({ producer, consumer: broker.consumer('g') });
    await notifier.publish(changeEvent(ORG_A, 1));
    await notifier.publish(changeEvent(ORG_A, 2));
    expect(producer.connects).to.equal(1);

    broker.down = true;
    let refused: unknown;
    await notifier.publish(changeEvent(ORG_A, 3)).catch((error: unknown) => {
      refused = error;
    });
    expect(refused).to.be.instanceOf(Error).with.property('name', 'BrokerDown');
  });
});

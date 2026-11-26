/**
 * The deployment's message broker as the libs clients expose it
 * (`IMessageProducer`, `IMessageConsumer`), in memory. It keeps what makes the
 * real ones behave as they do: a topic is an ordered log; a consumer group
 * created without `fromBeginning` starts at the log's end; each group sees
 * every later message once; a value is JSON-encoded on the way in and decoded
 * on the way out, so a string value arrives as a string; a publish or a
 * connect while the broker is down throws.
 */
import type {
  IMessageConsumer,
  IMessageProducer,
  StreamMessage,
} from '../../../../src/libs/types/messaging.types';

interface Entry {
  key: string;
  value: string;
}

type Handler = (message: StreamMessage<unknown>) => Promise<void>;

export class BrokerDown extends Error {
  constructor() {
    super('the broker is unreachable');
    this.name = 'BrokerDown';
  }
}

export class InMemoryBroker {
  down = false;
  private readonly topics = new Map<string, Entry[]>();
  private readonly consumers = new Set<InMemoryBrokerConsumer>();

  log(topic: string): readonly Entry[] {
    return this.topics.get(topic) ?? [];
  }

  append(topic: string, entry: Entry): void {
    if (this.down) throw new BrokerDown();
    this.topics.set(topic, [...this.log(topic), entry]);
  }

  /** Puts raw bytes on a topic, as a publisher that is not ours would. */
  inject(topic: string, key: string, value: string): void {
    this.topics.set(topic, [...this.log(topic), { key, value }]);
  }

  producer(): InMemoryBrokerProducer {
    return new InMemoryBrokerProducer(this);
  }

  consumer(groupId: string): InMemoryBrokerConsumer {
    const consumer = new InMemoryBrokerConsumer(this, groupId);
    this.consumers.add(consumer);
    return consumer;
  }

  /** Every consumer has handled everything published so far. */
  async settled(): Promise<void> {
    for (const consumer of this.consumers) await consumer.drain();
  }
}

export class InMemoryBrokerProducer implements IMessageProducer {
  private connected = false;
  connects = 0;

  constructor(private readonly broker: InMemoryBroker) {}

  async connect(): Promise<void> {
    if (this.broker.down) throw new BrokerDown();
    this.connected = true;
    this.connects += 1;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
  }

  isConnected(): boolean {
    return this.connected;
  }

  async publish<T>(topic: string, message: StreamMessage<T>): Promise<void> {
    this.broker.append(topic, { key: message.key, value: JSON.stringify(message.value) });
  }

  async publishBatch<T>(topic: string, messages: StreamMessage<T>[]): Promise<void> {
    for (const message of messages) await this.publish(topic, message);
  }

  async healthCheck(): Promise<boolean> {
    return !this.broker.down;
  }
}

export class InMemoryBrokerConsumer implements IMessageConsumer {
  private connected = false;
  private handler: Handler | null = null;
  private readonly offsets = new Map<string, number>();

  constructor(
    private readonly broker: InMemoryBroker,
    readonly groupId: string,
  ) {}

  async connect(): Promise<void> {
    if (this.broker.down) throw new BrokerDown();
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    this.handler = null;
  }

  isConnected(): boolean {
    return this.connected;
  }

  async subscribe(topics: string[], fromBeginning = false): Promise<void> {
    if (this.broker.down) throw new BrokerDown();
    for (const topic of topics) {
      if (!this.offsets.has(topic)) this.offsets.set(topic, fromBeginning ? 0 : this.broker.log(topic).length);
    }
  }

  async consume<T>(handler: (message: StreamMessage<T>) => Promise<void>): Promise<void> {
    this.handler = handler as Handler;
  }

  pause(): void {}

  resume(): void {}

  async healthCheck(): Promise<boolean> {
    return this.connected;
  }

  async drain(): Promise<void> {
    if (this.handler === null) return;
    for (const [topic, offset] of this.offsets) {
      const entries = this.broker.log(topic).slice(offset);
      this.offsets.set(topic, offset + entries.length);
      for (const entry of entries) {
        // As the real consumers: a message that cannot be decoded or handled is logged and skipped.
        try {
          await this.handler({ key: entry.key, value: JSON.parse(entry.value) as unknown });
        } catch {
          continue;
        }
      }
    }
  }
}

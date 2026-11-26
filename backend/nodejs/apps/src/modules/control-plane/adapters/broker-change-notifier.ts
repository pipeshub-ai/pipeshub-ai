import type {
  IMessageConsumer,
  IMessageProducer,
  StreamMessage,
} from '../../../libs/types/messaging.types';
import { changeEventSchema, type ChangeEvent } from '../contracts/generated';
import { ChangeSubscribers } from '../domain/change-subscribers';
import {
  CHANGE_EVENT_TYPE,
  changeEventMessage,
  CONTROL_PLANE_EVENTS_TOPIC,
} from '../domain/change-message';
import type { ChangeNotifier, Unsubscribe } from '../ports/change-notifier';
import { ControlPlaneLog, silentControlPlaneLog } from '../ports/log';

export interface BrokerChangeNotifierOptions {
  /** Where `publish` sends; connected on first use. */
  producer: IMessageProducer;
  /** This process's own consumer group, so every process sees every event. */
  consumer: IMessageConsumer;
  subscribers?: ChangeSubscribers;
  log?: ControlPlaneLog;
}

/**
 * `ChangeNotifier` on the deployment's broker (03-interfaces §5.4): topic
 * `control-plane-events`, keyed by org. The consumer starts at the end of the
 * topic: a process that starts late recovers by the gap rule, never by
 * replay. A message that is not a change event is logged and dropped.
 */
export class BrokerChangeNotifier implements ChangeNotifier {
  private readonly subscribers: ChangeSubscribers;
  private readonly log: ControlPlaneLog;
  private listening = false;
  private stopped = false;

  constructor(private readonly options: BrokerChangeNotifierOptions) {
    this.log = options.log ?? silentControlPlaneLog;
    this.subscribers =
      options.subscribers ??
      new ChangeSubscribers((event, error) => {
        this.log.warn('cp.change.handler_failed', {
          event: 'cp.change.handler_failed',
          orgId: event.orgId,
          revision: event.revision,
          errorType: error instanceof Error ? error.name : typeof error,
        });
      });
  }

  async publish(event: ChangeEvent): Promise<void> {
    const { producer } = this.options;
    if (!producer.isConnected()) await producer.connect();
    const { topic, key, value } = changeEventMessage(event);
    await producer.publish(topic, { key, value });
  }

  subscribe(handler: (event: ChangeEvent) => void): Unsubscribe {
    return this.subscribers.subscribe(handler);
  }

  /** Starts consuming; a second call does nothing. Throws when the broker cannot be reached. */
  async listen(): Promise<void> {
    if (this.listening) return;
    const { consumer } = this.options;
    await consumer.connect();
    await consumer.subscribe([CONTROL_PLANE_EVENTS_TOPIC], false);
    await consumer.consume<unknown>((message) => {
      this.receive(message);
      return Promise.resolve();
    });
    this.listening = true;
  }

  /**
   * Listens, trying again after `pause(attempt)` for as long as the broker
   * cannot be reached (NFR-37). Until it listens, this process learns of
   * other processes' writes from the head check alone; nothing waits on it.
   */
  async start(pause: (attempt: number) => Promise<void>): Promise<void> {
    this.stopped = false;
    for (let attempt = 1; this.retrying(); attempt += 1) {
      try {
        await this.listen();
      } catch (error) {
        this.log.warn('cp.change.listen_failed', {
          event: 'cp.change.listen_failed',
          attempt,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'head_check',
        });
        await pause(attempt);
      }
    }
  }

  /** Read through a method: `stop` may flip it while `start` awaits. */
  private retrying(): boolean {
    return !this.stopped && !this.listening;
  }

  async stop(): Promise<void> {
    this.stopped = true;
    if (!this.listening) return;
    this.listening = false;
    await this.options.consumer.disconnect();
  }

  private receive(message: StreamMessage<unknown>): void {
    const event = readChangeEvent(message.value);
    if (event === null) {
      this.log.warn('cp.change.unreadable', {
        event: 'cp.change.unreadable',
        key: message.key,
      });
      return;
    }
    this.subscribers.deliver(event);
  }
}

/**
 * The outbox publishes the envelope as a JSON string, which the broker clients
 * encode once more. Another `eventType`, or a payload that is not a change
 * event, is not one of ours.
 */
function readChangeEvent(value: unknown): ChangeEvent | null {
  let raw = value;
  if (typeof raw === 'string') {
    try {
      raw = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (raw === null || typeof raw !== 'object') return null;
  const envelope = raw as { eventType?: unknown; payload?: unknown };
  if (envelope.eventType !== CHANGE_EVENT_TYPE) return null;
  const parsed = changeEventSchema.safeParse(envelope.payload);
  return parsed.success ? parsed.data : null;
}

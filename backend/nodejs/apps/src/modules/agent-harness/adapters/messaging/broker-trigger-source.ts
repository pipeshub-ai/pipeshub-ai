import { forOrg } from '../../../../libs/context/orgContext';
import type {
  IMessageConsumer,
  StreamMessage,
} from '../../../../libs/types/messaging.types';
import { MAX_EVENT_AGE_MS, type TriggerEvent } from '../../domain/schedules';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../../application/harness-log';

export const RECORD_EVENTS_TOPIC = 'record-events';
const NEW_RECORD = 'newRecord';

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

/** A producer's clock this far ahead of ours is still its own time. */
const CLOCK_SKEW_MS = 5 * 60_000;

/**
 * When the event happened: the producer's own `timestamp`, so a redelivery
 * of it falls in the window the first delivery fell in and fires nothing
 * more (TR-03); our clock when it carries none, or one too old or too far
 * ahead to trust.
 */
function eventTime(stamp: unknown, now: number): number {
  return typeof stamp === 'number' &&
    Number.isFinite(stamp) &&
    stamp <= now + CLOCK_SKEW_MS &&
    now - stamp <= MAX_EVENT_AGE_MS
    ? stamp
    : now;
}

/**
 * A connector's new record (`record-events` `newRecord`) as a trigger event:
 * which org, connector and record, and when. Nothing of the record's content
 * is kept: its name and content are `external` data a schedule's run reads,
 * if its actor may, through its own tools.
 */
export function connectorTriggerEvent(
  value: unknown,
  now: number,
): TriggerEvent | null {
  let event: unknown = value;
  if (typeof event === 'string') {
    try {
      event = JSON.parse(event) as unknown;
    } catch {
      return null;
    }
  }
  if (!isObject(event) || event['eventType'] !== NEW_RECORD) return null;
  const payload = event['payload'];
  if (!isObject(payload)) return null;
  const orgId = payload['orgId'];
  const connectorId = payload['connectorId'];
  if (
    typeof orgId !== 'string' ||
    orgId === '' ||
    typeof connectorId !== 'string' ||
    connectorId === ''
  ) {
    return null;
  }
  const recordId = payload['recordId'];
  return {
    orgId,
    kind: 'connector',
    connectorId,
    ...(typeof recordId === 'string' &&
    recordId !== '' &&
    recordId.length <= 256
      ? { recordId }
      : {}),
    at: eventTime(event['timestamp'], now),
  };
}

/** The streams record events are published on: one per lane on Redis Streams, else the topic. */
export function recordEventStreams(redis: boolean, lanes: number): string[] {
  if (!redis || lanes <= 1) return [RECORD_EVENTS_TOPIC];
  return Array.from(
    { length: lanes },
    (_, lane) => `${RECORD_EVENTS_TOPIC}.${String(lane)}`,
  );
}

export interface BrokerTriggerSourceOptions {
  createConsumer: () => IMessageConsumer;
  streams: () => string[];
  deliver: (event: TriggerEvent) => Promise<unknown>;
  clock: () => Promise<number>;
  logger?: HarnessLogger;
}

/**
 * Connector events from the message broker (Kafka or Redis Streams, through
 * the platform's consumer), in its own consumer group, started only while
 * some schedule has a connector trigger: an install without one reads
 * nothing. Each event is coalesced per schedule by the firer.
 */
export class BrokerTriggerSource {
  private consumer: IMessageConsumer | null = null;
  private starting: Promise<void> | null = null;

  private readonly createConsumer: () => IMessageConsumer;
  private readonly streams: () => string[];
  private readonly deliver: (event: TriggerEvent) => Promise<unknown>;
  private readonly clock: () => Promise<number>;
  private readonly logger: HarnessLogger;

  constructor(options: BrokerTriggerSourceOptions) {
    this.createConsumer = options.createConsumer;
    this.streams = options.streams;
    this.deliver = options.deliver;
    this.clock = options.clock;
    this.logger = options.logger ?? silentHarnessLogger;
  }

  get running(): boolean {
    return this.consumer !== null;
  }

  /** Starts or stops consuming to match whether any connector trigger is active. */
  async ensure(wanted: boolean): Promise<void> {
    if (this.starting !== null) await this.starting;
    if (wanted && this.consumer === null) {
      this.starting = this.start().finally(() => {
        this.starting = null;
      });
      await this.starting;
    } else if (!wanted && this.consumer !== null) {
      await this.stop();
    }
  }

  async stop(): Promise<void> {
    const consumer = this.consumer;
    this.consumer = null;
    if (consumer?.isConnected() === true) await consumer.disconnect();
  }

  async handle(message: StreamMessage<unknown>): Promise<void> {
    const event = connectorTriggerEvent(message.value, await this.clock());
    if (event === null) return;
    try {
      // The platform's own event names its org, as main's consumers bind it (`forOrg`).
      await forOrg(event.orgId, 'agent-harness.trigger-event', () =>
        this.deliver(event),
      );
    } catch (error) {
      // An event lost here costs one window's run; the consumer must go on.
      this.logger.warn(
        'schedule.trigger_event_failed',
        harnessLogMeta('schedule.trigger_event_failed', {
          orgId: event.orgId,
          connectorId: event.connectorId,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
    }
  }

  private async start(): Promise<void> {
    const consumer = this.createConsumer();
    await consumer.connect();
    await consumer.subscribe(this.streams(), false);
    await consumer.consume((message) => this.handle(message));
    this.consumer = consumer;
    this.logger.info(
      'schedule.trigger_source_started',
      harnessLogMeta('schedule.trigger_source_started', {
        streams: this.streams(),
      }),
    );
  }
}

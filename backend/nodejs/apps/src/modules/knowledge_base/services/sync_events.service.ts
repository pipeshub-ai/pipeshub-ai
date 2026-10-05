import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { IMessageProducer, StreamMessage } from '../../../libs/types/messaging.types';


export interface Event {
  eventType: string;
  timestamp: number;
  payload: ConnectorSyncEvent | ReindexEventPayload | any;
}

export interface ReindexEventPayload {
  orgId: string;
  statusFilters: string[];
}

export interface ConnectorSyncEvent {
  orgId: string;
  connector: string;
  connectorId: string;
  origin: string;
  syncedBy?: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

export interface BaseSyncEvent {
  orgId: string;
  connector: string;
  connectorId: string;
  origin: string;
  syncedBy?: string;
  fullSync?: boolean;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

@injectable()
export class SyncEventProducer {
  private readonly syncTopic = 'sync-events';

  constructor(
    @inject('MessageProducer') private readonly producer: IMessageProducer,
    @inject('Logger') private readonly logger: Logger,
  ) {}

  async start(): Promise<void> {
    if (!this.producer.isConnected()) {
      await this.producer.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.producer.isConnected()) {
      await this.producer.disconnect();
    }
  }

  isConnected(): boolean {
    return this.producer.isConnected();
  }

  async publishEvent(event: Event): Promise<void> {
    // Partition by connector, not by event type. Kafka hashes this key to pick
    // a partition and gives each partition to one consumer in the group, so
    // keying by eventType puts every connector of a given kind -- every
    // Confluence instance, say -- on one partition and therefore one consumer.
    // Keying by connectorId spreads them and gives per-connector ordering as a
    // bonus. Events without a connectorId keep the old key.
    const message: StreamMessage<string> = {
      key: event.payload?.connectorId ?? event.eventType,
      value: JSON.stringify(event),
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.producer.publish(this.syncTopic, message);
      this.logger.info(`Published event: ${event.eventType} to topic ${this.syncTopic}`);
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}

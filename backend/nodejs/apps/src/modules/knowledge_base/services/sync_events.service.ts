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
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

export interface BaseSyncEvent {
  orgId: string;
  connector: string;
  connectorId: string;
  origin: string;
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
    const message: StreamMessage<string> = {
      key: event.eventType,
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

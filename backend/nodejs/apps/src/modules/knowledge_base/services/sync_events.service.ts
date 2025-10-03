import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BaseKafkaProducerConnection } from '../../../libs/services/kafka.service';
import { KafkaConfig, KafkaMessage } from '../../../libs/types/kafka.types';


export interface Event {
  eventType: string;
  timestamp: number;
  payload: ReindexAllRecordEvent;
}

export interface ReindexAllRecordEvent {
  orgId: string;
  connector: string;
  origin: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

export interface BaseSyncEvent {
  orgId: string;
  connector: string;
  origin: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

@injectable()
export class SyncEventProducer extends BaseKafkaProducerConnection {
  private readonly syncTopic = 'sync-events';

  constructor(
    @inject('KafkaConfig') config: KafkaConfig,
    @inject('Logger') logger: Logger,
  ) {
    super(config, logger);
  }

  async start(): Promise<void> {
    if (!this.isConnected) {
      await this.connect();
    }
  }

  async stop(): Promise<void> {
    if (this.isConnected()) {
      await this.disconnect();
    }
  }

  async publishEvent(event: Event): Promise<void> {
    const message: KafkaMessage<string> = {
      key: event.eventType,
      value: JSON.stringify(event),
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.publish(this.syncTopic, message);
      this.logger.info(`Published event: ${event.eventType} to topic ${this.syncTopic}`);
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}

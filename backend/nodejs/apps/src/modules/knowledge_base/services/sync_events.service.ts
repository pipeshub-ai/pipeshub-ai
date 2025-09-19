import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BaseKafkaProducerConnection } from '../../../libs/services/kafka.service';
import { KafkaConfig, KafkaMessage } from '../../../libs/types/kafka.types';

export enum EventType {
  ReindexAllRecordEvent = 'reindexFailed',
  SyncDriveEvent = 'drive.resync',
  SyncGmailEvent = 'gmail.resync',
  SyncOneDriveEvent = 'onedrive.resync',
  SyncSharePointOnlineEvent = 'sharepointonline.resync',
}

export interface Event {
  eventType: EventType;
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

interface BaseSyncEvent {
  orgId: string;
  connector: string;
  origin: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

export interface SyncDriveEvent extends BaseSyncEvent {}
export interface SyncGmailEvent extends BaseSyncEvent {}
export interface SyncOneDriveEvent extends BaseSyncEvent {}
export interface SyncSharePointOnlineEvent extends BaseSyncEvent {}

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

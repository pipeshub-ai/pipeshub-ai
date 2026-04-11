import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { IMessageProducer, StreamMessage } from '../../../libs/types/messaging.types';

export enum EventType {
  NewRecordEvent = 'newRecord',
  UpdateRecordEvent = 'updateRecord',
  DeletedRecordEvent = 'deleteRecord',
  ReindexRecordEvent = 'reindexRecord',
}

export interface Event {
  eventType: EventType;
  timestamp: number;
  payload:
    | NewRecordEvent
    | UpdateRecordEvent
    | DeletedRecordEvent
    | ReindexRecordEvent;
}

export interface NewRecordEvent {
  orgId: string;
  recordId: string;
  recordName: string;
  recordType: string;
  version: number;
  signedUrlRoute: string;
  origin: string;
  extension: string;
  mimeType: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

export interface UpdateRecordEvent {
  orgId: string;
  recordId: string;
  version: number;
  extension: string;
  mimeType: string;
  signedUrlRoute: string;
  updatedAtTimestamp: string;
  sourceLastModifiedTimestamp: string;
  virtualRecordId?: string;
  summaryDocumentId?:string;
}

export interface ReindexRecordEvent {
  orgId: string;
  recordId: string;
  recordName: string;
  recordType: string;
  version: number;
  signedUrlRoute: string;
  origin: string;
  extension: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  sourceCreatedAtTimestamp: string;
}

export interface DeletedRecordEvent {
  orgId: string;
  recordId: string;
  version: number;
  extension: string;
  mimeType: string;
  summaryDocumentId?:string;
  virtualRecordId?: string;
}

@injectable()
export class RecordsEventProducer {
  private readonly recordsTopic = 'record-events';

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
      await this.producer.publish(this.recordsTopic, message);
      this.logger.info(
        `Published event: ${event.eventType} to topic ${this.recordsTopic}`,
      );
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}

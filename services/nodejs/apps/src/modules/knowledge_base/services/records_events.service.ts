import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { BaseKafkaProducerConnection } from '../../../libs/services/kafka.service';
import { KafkaConfig, KafkaMessage } from '../../../libs/types/kafka.types';


export enum EventType {
  NewRecordEvent = 'newRecord',
  UpdateRecordEvent = 'updateRecord',
  DeletedRecordEvent = 'deleteRecord',
  ReindexRecordEvent = 'reindexRecord'
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
  recordId : string;
  recordName : string;
  recordType : string;
  version : number;
  signedUrlRoute : string;
  origin : string;
  extension : string;
  createdAtTimestamp : string;
  updatedAtTimestamp : string;
  sourceCreatedAtTimestamp : string;
}

export interface UpdateRecordEvent {
    orgId: string;
    recordId : string;
    version : number;
    signedUrlRoute : string;
    updatedAtTimestamp : string;
    sourceLastModifiedTimestamp : string;
}
export interface ReindexRecordEvent {
  orgId: string;
  recordId : string;
  recordName : string;
  recordType : string;
  version : number;
  signedUrlRoute : string;
  origin : string;
  extension : string;
  createdAtTimestamp : string;
  updatedAtTimestamp : string;
  sourceCreatedAtTimestamp : string;
}


export interface DeletedRecordEvent {
  orgId: string;
  recordId : string;
  version : number;
}



@injectable()
export class RecordsEventProducer extends BaseKafkaProducerConnection {
  private readonly topic = 'record-events';

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
      await this.publish(this.topic, message);
      this.logger.info(
        `Published event: ${event.eventType} to topic ${this.topic}`,
      );
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}





///////




import { injectable, inject } from 'inversify';
import { Logger } from '../../../libs/services/logger.service';
import { IMessageProducer, StreamMessage } from '../../../libs/types/messaging.types';
import { laneStreamFor } from '../../../libs/utils/lane.utils';

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
  /** Connector instance the record belongs to; the lane key. */
  connectorId: string;
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
  /** Connector instance the record belongs to; the lane key. */
  connectorId: string;
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
  /** Connector instance the record belongs to; the lane key. */
  connectorId: string;
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
  /** Connector instance the record belongs to; the lane key. */
  connectorId: string;
  recordId: string;
  version: number;
  extension: string;
  mimeType: string;
  summaryDocumentId?:string;
  virtualRecordId?: string;
}

/**
 * The fairness key a record event is placed by.
 *
 * Mirrors the Python side's default (`connectorId`, falling back to `orgId`),
 * so both producers put one connector's records on the same lane.
 */
function laneKeyFor(event: Event): string {
  const payload = (event.payload ?? {}) as unknown as Record<string, unknown>;
  for (const field of ['connectorId', 'orgId']) {
    const value = payload[field];
    if (typeof value === 'string' && value.length > 0) {
      return value;
    }
  }
  return '__default__';
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
    const laneKey = laneKeyFor(event);
    // On Redis a lane is a stream, and the key below is only stored as a
    // field -- it selects nothing. Publishing to the base topic would put
    // every connector back on one queue, which is what lanes exist to stop.
    // On Kafka this returns the topic unchanged and the key does the work.
    const topic = laneStreamFor(this.recordsTopic, laneKey);
    const message: StreamMessage<string> = {
      // Keyed by the connector instance, matching the Python lane key
      // (FAIR_SCHEDULING_LANE_KEY_FIELD). Keying by eventType would put
      // every newRecord on one partition the moment record-events has more
      // than one, which is exactly what lanes are for.
      key: laneKey,
      value: JSON.stringify(event),
      headers: {
        eventType: event.eventType,
        timestamp: event.timestamp.toString(),
      },
    };

    try {
      await this.producer.publish(topic, message);
      this.logger.info(`Published event: ${event.eventType} to topic ${topic}`);
    } catch (error) {
      this.logger.error(`Failed to publish event: ${event.eventType}`, error);
    }
  }
}

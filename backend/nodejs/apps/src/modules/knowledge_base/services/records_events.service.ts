/**
 * Record event shapes. This service no longer publishes record events: the
 * Python services publish every one, and place each connector on its Redis
 * Streams lane through the lane map (backend/python/app/services/messaging/
 * lanes/assignment.py).
 */

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

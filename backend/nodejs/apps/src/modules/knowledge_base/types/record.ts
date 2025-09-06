// Enumerations for the RecordDocument model
export type RecordType = 'FILE' | 'WEBPAGE' | 'MESSAGE' | 'EMAIL' | 'TICKET' | 'OTHERS';
export type OriginType = 'UPLOAD' | 'CONNECTOR';
export type ConnectorName =
  | 'ONEDRIVE'
  | 'GOOGLE_DRIVE'
  | 'CONFLUENCE'
  | 'JIRA'
  | 'SLACK'
  | 'SHAREPOINT ONLINE'
  | 'GMAIL';
export type IndexingStatus =
  | 'NOT_STARTED'
  | 'IN_PROGRESS'
  | 'FAILED'
  | 'COMPLETED';

// Interface for a generic record document.
export interface IRecordDocument {
  _key: string;
  // Optional properties can be omitted on document creation
  orgId: string;

  // Required fields
  recordName: string;
  externalRecordId: string;
  externalRevisionId?: string;
  recordType: RecordType;
  origin: OriginType;
  createdAtTimestamp: number;

  // Optional properties with defaults on the backend (if not provided)
  version?: number; // default: 0
  connectorName?: ConnectorName;
  updatedAtTimestamp?: number;
  lastSyncTimestamp?: number;

  // Flags and timestamps
  isDeletedAtSource?: boolean; // default: false
  deletedAtSourceTimestamp?: number;
  sourceCreatedAtTimestamp?: number;
  sourceLastModifiedTimestamp?: number;

  isDeleted?: boolean; // default: false
  isArchived?: boolean; // default: false
  deletedByUserId?: string;

  lastIndexTimestamp?: number;
  lastExtractionTimestamp?: number;
  indexingStatus?: IndexingStatus;
  isLatestVersion?: boolean; // default: false
  isDirty?: boolean; // default: false, indicates need for re-indexing
  reason?: string;
  virtualRecordId?: string;
  summaryDocumentId?:string;
  webUrl?: string;
  mimeType?: string;
}

export interface IFileBuffer {
  originalname: string;
  mimetype: string;
  size: number;
  buffer: Buffer;
  encoding?: string;
}

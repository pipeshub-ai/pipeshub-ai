// src/constants/record.constants.ts

// Record type enumeration
export const RECORD_TYPE = {
  FILE: 'FILE',
  WEBPAGE: 'WEBPAGE',
  MESSAGE: 'MESSAGE',
  EMAIL: 'EMAIL',
  OTHERS: 'OTHERS',
} as const;

// Origin type enumeration
export const ORIGIN_TYPE = {
  UPLOAD: 'UPLOAD',
  CONNECTOR: 'CONNECTOR',
} as const;

// Connector name enumeration
export const CONNECTOR_NAME = {
  ONEDRIVE: 'ONEDRIVE',
  GOOGLE_DRIVE: 'GOOGLE_DRIVE',
  SHAREPOINT_ONLINE: 'SHAREPOINT ONLINE',
  GMAIL: 'GMAIL',
  CONFLUENCE: 'CONFLUENCE',
  JIRA: 'JIRA',
  SLACK: 'SLACK',
} as const;

// Indexing status enumeration
export const INDEXING_STATUS = {
  NOT_STARTED: 'NOT_STARTED',
  IN_PROGRESS: 'IN_PROGRESS',
  FAILED: 'FAILED',
  COMPLETED: 'COMPLETED',
  FILE_TYPE_NOT_SUPPORTED: 'FILE_TYPE_NOT_SUPPORTED',
  AUTO_INDEX_OFF: 'AUTO_INDEX_OFF',
} as const;

export const ENTITY_TYPE = {
  KNOWLEDGE_BASE: 'KB',
};

// Collection names
export const COLLECTIONS = {
  // Document collections
  RECORDS: 'records',
  FILES: 'files',
  USERS: 'users',
  KNOWLEDGE_BASE: 'knowledgeBase',
  BELONGS_TO_KNOWLEDGE_BASE: 'belongsToKnowledgeBase',
  PERMISSIONS_TO_KNOWLEDGE_BASE: 'permissionsToKnowledgeBase',

  // Edge collections
  RECORD_TO_RECORD: 'recordRelations',
  IS_OF_TYPE: 'isOfType',
  PERMISSIONS: 'permissions',
  BELONGS_TO: 'belongsTo',
} as const;

export const RELATIONSHIP_TYPE = {
  USER: 'USER',
  GROUP: 'GROUP',
  DOMAIN: 'DOMAIN',
} as const;

export const ROLE = {
  OWNER: 'OWNER',
  WRITER: 'WRITER',
  COMMENTER: 'COMMENTER',
  READER: 'READER',
};

// Graph names
export const GRAPHS = {
  KB_GRAPH: 'knowledgeBaseGraph',
} as const;

// Collection types for ArangoDB
export const COLLECTION_TYPE = {
  DOCUMENT: 2, // Normal document collection
  EDGE: 3, // Edge collection
} as const;

// Type definitions using TypeScript's typeof and keyof operators
export type RecordType = (typeof RECORD_TYPE)[keyof typeof RECORD_TYPE];
export type OriginType = (typeof ORIGIN_TYPE)[keyof typeof ORIGIN_TYPE];
export type ConnectorName =
  (typeof CONNECTOR_NAME)[keyof typeof CONNECTOR_NAME];
export type IndexingStatus =
  (typeof INDEXING_STATUS)[keyof typeof INDEXING_STATUS];
export type CollectionName = (typeof COLLECTIONS)[keyof typeof COLLECTIONS];
export type GraphName = (typeof GRAPHS)[keyof typeof GRAPHS];
export type CollectionType =
  (typeof COLLECTION_TYPE)[keyof typeof COLLECTION_TYPE];

// Type guard functions for runtime validation
export const isValidRecordType = (type: string): type is RecordType => {
  return Object.values(RECORD_TYPE).includes(type as RecordType);
};

export const isValidOriginType = (origin: string): origin is OriginType => {
  return Object.values(ORIGIN_TYPE).includes(origin as OriginType);
};

export const isValidConnectorName = (name: string): name is ConnectorName => {
  return Object.values(CONNECTOR_NAME).includes(name as ConnectorName);
};

export const isValidCollectionName = (name: string): name is CollectionName => {
  return Object.values(COLLECTIONS).includes(name as CollectionName);
};

export interface Department {
  _id: string;
  name: string;
}

export interface AppSpecificRecordType {
  _id: string;
  name: string;
  tag: string;
}

export interface Module {
  _id: string;
  name: string;
}

export interface SearchTag {
  _id: string;
  name: string;
}

export interface User {
  _id: string;
  fullName: string;
}

export interface InitialContext {
  recordId: string;
  recordName: string;
  recordType: string;
  departments: string[];
  modules: string[];
  categories: string[];
}

export interface KnowledgeBase {
  id: string;
  name: string;
  orgId: string;
}

export interface RecordDetailsResponse {
  record: Record;
  knowledgeBase: KnowledgeBase;
  permissions: Permissions[];
  relatedRecords: any[];
  metadata: Metadata;
}

export interface MetadataItem {
  id: string;
  name: string;
}
export interface Metadata {
  departments: MetadataItem[];
  categories: MetadataItem[];
  subcategories1: MetadataItem[];
  subcategories2: MetadataItem[];
  subcategories3: MetadataItem[];
  topics: MetadataItem[];
  languages: MetadataItem[];
}

export interface Record {
  _key: string;
  _id: string;
  _rev: string;
  orgId: string;
  recordName: string;
  externalRecordId: string;
  recordType: string;
  origin: string;
  connectorName: string;
  createdAtTimestamp: number;
  updatedAtTimestamp: number;
  sourceCreatedAtTimestamp: number;
  sourceLastModifiedTimestamp: number;
  isDeleted: boolean;
  isArchived: boolean;
  indexingStatus: string;
  version: number;
  fileRecord: FileRecord | null;
  mailRecord: MailRecord | null;
  departments?: Array<{ _id: string; name: string }>;
  appSpecificRecordType?: Array<{ _id: string; name: string }>;
  modules?: Array<{ _id: string; name: string }>;
  searchTags?: Array<{ _id: string; name: string }>;
  createdBy?: string;
  summaryDocumentId?: string;
  webUrl?: string;
  reason:string
}

export interface FileRecord {
  _key: string;
  _id: string;
  _rev: string;
  orgId: string;
  name: string;
  isFile: boolean;
  extension: string;
  mimeType: string;
  sizeInBytes: number;
  webUrl: string;
  path: string;
}

export interface MailRecord {
  _key: string;
  _id: string;
  _rev: string;
  threadId: string;
  isParent: boolean;
  internalDate: string;
  subject: string;
  date: string;
  from: string;
  to: string;
  cc: string[];
  bcc: string[];
  messageIdHeader: string;
  historyId: string;
  webUrl: string;
  labelIds: string[];
}

export interface Permissions {
  id: string;
  name: string;
  type: string;
  relationship: string;
}

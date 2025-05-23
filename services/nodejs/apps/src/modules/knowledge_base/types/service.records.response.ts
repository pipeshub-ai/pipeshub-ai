export interface IServiceRecord {
  _key: string;
  _id: string;
  _rev: string;
  orgId: string;
  recordName: string;
  externalRecordId: string;
  recordType: string;
  origin: string;
  createdAtTimestamp: string;
  updatedAtTimestamp: string;
  isDeleted: boolean;
  isArchived: boolean;
  indexingStatus: string;
  version: number;
  summaryDocumentId: string;
  virtualRecordId:string;
  fileRecord: IServiceFileRecord | null;
  mailRecord: IServiceMailRecord | null;
}

export interface IServiceFileRecord {
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

export interface IServiceMailRecord {
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

export interface IServicePermissions {
  id: string;
  name: string;
  type: string;
  relationship: string;
}

export interface IServiceRecordsResponse {
  record: IServiceRecord;
  knowledgeBase: {
    id: string;
    name: string;
    orgId: string;
  };
  permissions: IServicePermissions[];
}

export interface IServiceDeleteRecordResponse {
  status: string;
  message: string;
  response: any;
}

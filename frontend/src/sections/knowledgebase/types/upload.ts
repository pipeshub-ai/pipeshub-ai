/**
 * Types for upload notification system
 */

export interface FailedFileInfo {
  recordId: string;
  fileName: string;
  filePath: string;
  error: string;
}

export type UploadStatus = 'uploading' | 'processing' | 'completed';

export interface ActiveUpload {
  kbId: string;
  folderId?: string;
  files: string[];
  startTime: number;
  recordIds?: string[];
  status?: UploadStatus;
  failedFiles?: FailedFileInfo[];
  hasFailures?: boolean;
}

export interface RecordsProcessedEvent {
  recordIds: string[];
  orgId: string;
  kbId?: string;
  folderId?: string;
  totalRecords: number;
  timestamp: number;
}

export interface RecordsFailedEvent {
  failedFiles: FailedFileInfo[];
  orgId: string;
  kbId?: string;
  folderId?: string;
  totalFailed: number;
  timestamp: number;
}



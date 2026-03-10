/**
 * Type definitions for the Upload Notification System
 * 
 * Import these types when working with upload notifications:
 * 
 * ```typescript
 * import type { 
 *   FileUploadState, 
 *   FileStatus, 
 *   UploadBatch 
 * } from './upload-notification.types';
 * ```
 */

/**
 * Status of an individual file in the UPLOAD process (NOT indexing)
 * - queued: File is waiting to be uploaded
 * - uploading: File is being sent to backend (0-100%)
 * - completed: File was successfully uploaded to backend
 * - failed: Upload failed
 */
export type FileStatus = 'queued' | 'uploading' | 'completed' | 'failed';

/**
 * State information for a single file being uploaded
 */
export interface FileUploadState {
  /** The name of the file */
  fileName: string;
  
  /** Current status of the file */
  status: FileStatus;
  
  /** Error message if status is 'failed' */
  error?: string;
  
  /** Backend record ID once created */
  recordId?: string;
  
  /** Upload progress (0-100) for 'uploading' status */
  progress?: number;
}

/**
 * Information about a failed file upload
 */
export interface FailedFileInfo {
  /** Backend record ID (if created) */
  recordId: string;
  
  /** Original file name */
  fileName: string;
  
  /** Full file path */
  filePath: string;
  
  /** Error message describing why it failed */
  error: string;
}

/**
 * A batch of files being uploaded together
 */
export interface UploadBatch {
  /** Knowledge base ID */
  kbId: string;
  
  /** Folder ID (undefined for root) */
  folderId?: string;
  
  /** List of file names in this batch */
  files: string[];
  
  /** Granular state for each file (NEW - recommended) */
  fileStates?: Map<string, FileUploadState>;
  
  /** When this upload batch started */
  startTime: number;
  
  /** Backend record IDs (legacy support) */
  recordIds?: string[];
  
  /** Overall batch status (legacy support) */
  status?: 'uploading' | 'processing' | 'completed';
  
  /** Failed files information (legacy support) */
  failedFiles?: FailedFileInfo[];
  
  /** Whether batch has any failures (legacy support) */
  hasFailures?: boolean;
}

/**
 * Props for the UploadNotification component
 */
export interface UploadNotificationProps {
  /** Map of upload batches, keyed by unique upload ID */
  uploads: Map<string, UploadBatch>;
  
  /** Callback when user dismisses a notification */
  onDismiss?: (uploadKey: string) => void;
  
  /** Current knowledge base ID (for filtering) */
  currentKBId?: string;
  
  /** Current folder ID (for filtering) */
  currentFolderId?: string;
}

/**
 * Upload statistics for a batch
 */
export interface UploadStats {
  /** Total number of files */
  total: number;
  
  /** Files waiting to be uploaded */
  queued: number;
  
  /** Files currently uploading */
  uploading: number;
  
  /** Files being processed by backend */
  processing: number;
  
  /** Files completed successfully */
  completed: number;
  
  /** Files that failed */
  failed: number;
  
  /** Whether all files are done (completed or failed) */
  isComplete: boolean;
}

/**
 * Callback function for upload progress updates
 */
export type UploadProgressCallback = (
  fileName: string,
  status: FileStatus,
  progress?: number,
  error?: string
) => void;

/**
 * Configuration for upload behavior
 */
export interface UploadConfig {
  /** Maximum concurrent uploads */
  maxConcurrent?: number;
  
  /** Batch size for processing */
  batchSize?: number;
  
  /** Auto-cleanup delay in milliseconds */
  autoCleanupDelay?: number;
  
  /** Progress update throttle (percentage change required) */
  progressThrottle?: number;
}

/**
 * Upload manager interface
 */
export interface IUploadNotificationManager {
  createUpload(kbId: string, folderId: string | undefined, files: File[] | string[]): string;
  updateFileStatus(uploadKey: string, fileName: string, status: FileStatus, progress?: number, error?: string): void;
  markFileUploading(uploadKey: string, fileName: string, progress?: number): void;
  markFileProcessing(uploadKey: string, fileName: string): void;
  markFileCompleted(uploadKey: string, fileName: string, recordId?: string): void;
  markFileFailed(uploadKey: string, fileName: string, error: string): void;
  updateFileProgress(uploadKey: string, fileName: string, progress: number): void;
  removeUpload(uploadKey: string): void;
  getUploadStats(uploadKey: string): UploadStats | null;
  cleanup(): void;
}

/**
 * Type guard to check if an upload batch uses granular file states
 */
export function hasFileStates(batch: UploadBatch): batch is UploadBatch & { fileStates: Map<string, FileUploadState> } {
  return batch.fileStates !== undefined && batch.fileStates.size > 0;
}

/**
 * Type guard to check if a file is in an active state (not completed/failed)
 */
export function isFileActive(state: FileUploadState): boolean {
  return state.status === 'queued' || state.status === 'uploading';
}

/**
 * Type guard to check if a file is in a terminal state (completed/failed)
 */
export function isFileTerminal(state: FileUploadState): boolean {
  return state.status === 'completed' || state.status === 'failed';
}

/**
 * Helper to get status color based on file status
 */
export function getStatusColor(status: FileStatus): string {
  switch (status) {
    case 'completed':
      return '#4caf50'; // green
    case 'failed':
      return '#f44336'; // red
    case 'uploading':
      return '#1976d2'; // primary blue
    case 'queued':
      return '#9e9e9e'; // gray
    default:
      return '#757575'; // default gray
  }
}

/**
 * Helper to get status label for display
 */
export function getStatusLabel(status: FileStatus): string {
  switch (status) {
    case 'queued':
      return 'Queued';
    case 'uploading':
      return 'Uploading';
    case 'completed':
      return 'Completed';
    case 'failed':
      return 'Failed';
    default:
      return 'Unknown';
  }
}

/**
 * Helper to calculate overall progress percentage for a batch
 */
export function calculateBatchProgress(batch: UploadBatch): number {
  if (!batch.fileStates || batch.fileStates.size === 0) {
    return 0;
  }

  const states = Array.from(batch.fileStates.values());
  const total = states.length;
  
  if (total === 0) return 0;

  // Count completed and failed as 100% each
  const completed = states.filter(s => s.status === 'completed' || s.status === 'failed').length;
  
  // Add partial progress for uploading files
  const uploadingProgress = states
    .filter(s => s.status === 'uploading')
    .reduce((sum, s) => sum + (s.progress || 0), 0);

  const totalProgress = (completed * 100) + uploadingProgress;
  
  return Math.round(totalProgress / total);
}

// Re-export for convenience
export type { FileUploadState as FileState };
export type { UploadBatch as Batch };


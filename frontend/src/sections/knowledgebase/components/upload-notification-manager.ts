/**
 * Upload Notification Manager
 * 
 * Utility class to manage file upload states and notifications
 * without requiring full UI refreshes.
 * 
 * Usage:
 * ```typescript
 * const manager = new UploadNotificationManager(uploadsMap, setUploads);
 * const uploadKey = manager.createUpload(kbId, folderId, files);
 * manager.updateFileStatus(uploadKey, fileName, 'uploading', 50);
 * manager.markFileCompleted(uploadKey, fileName, recordId);
 * manager.markFileFailed(uploadKey, fileName, error);
 * ```
 */

import React from 'react';

export type FileStatus = 'queued' | 'uploading' | 'processing' | 'completed' | 'failed';

export interface FileUploadState {
  fileName: string;
  status: FileStatus;
  error?: string;
  recordId?: string;
  progress?: number; // 0-100
}

export interface UploadBatch {
  kbId: string;
  folderId?: string;
  files: string[];
  fileStates: Map<string, FileUploadState>;
  startTime: number;
  recordIds?: string[];
  status?: 'uploading' | 'processing' | 'completed';
  failedFiles?: Array<{
    recordId: string;
    fileName: string;
    filePath: string;
    error: string;
  }>;
  hasFailures?: boolean;
}

export class UploadNotificationManager {
  private uploadsMap: Map<string, UploadBatch>;

  private updateCallback: (uploads: Map<string, UploadBatch>) => void;

  private autoCleanupTimeouts: Map<string, NodeJS.Timeout>;

  constructor(
    uploadsMap: Map<string, UploadBatch>,
    updateCallback: (uploads: Map<string, UploadBatch>) => void
  ) {
    this.uploadsMap = uploadsMap;
    this.updateCallback = updateCallback;
    this.autoCleanupTimeouts = new Map();
  }

  /**
   * Create a new upload batch and return its key
   */
  createUpload(
    kbId: string,
    folderId: string | undefined,
    files: File[] | string[]
  ): string {
    const uploadKey = `${kbId}-${folderId || 'root'}-${Date.now()}`;
    const fileStates = new Map<string, FileUploadState>();

    const fileNames = files.map((f) => (typeof f === 'string' ? f : f.name));

    // Initialize all files as 'queued'
    fileNames.forEach((fileName) => {
      fileStates.set(fileName, {
        fileName,
        status: 'queued',
      });
    });

    this.uploadsMap.set(uploadKey, {
      kbId,
      folderId,
      files: fileNames,
      fileStates,
      startTime: Date.now(),
    });

    this.triggerUpdate();
    return uploadKey;
  }

  /**
   * Update the status of a specific file
   */
  updateFileStatus(
    uploadKey: string,
    fileName: string,
    status: FileStatus,
    progress?: number,
    error?: string
  ): void {
    const upload = this.uploadsMap.get(uploadKey);
    if (!upload || !upload.fileStates) return;

    const currentState = upload.fileStates.get(fileName);
    if (currentState) {
      upload.fileStates.set(fileName, {
        ...currentState,
        status,
        progress,
        error,
      });

      this.triggerUpdate();
    }
  }

  /**
   * Mark a file as uploading with optional progress
   */
  markFileUploading(uploadKey: string, fileName: string, progress: number = 0): void {
    this.updateFileStatus(uploadKey, fileName, 'uploading', progress);
  }

  /**
   * Mark a file as completed (upload successful, backend received it)
   */
  markFileCompleted(uploadKey: string, fileName: string, recordId?: string): void {
    const upload = this.uploadsMap.get(uploadKey);
    if (!upload || !upload.fileStates) return;

    const currentState = upload.fileStates.get(fileName);
    if (currentState) {
      upload.fileStates.set(fileName, {
        ...currentState,
        status: 'completed',
        recordId,
        progress: 100,
      });

      this.checkAndMarkBatchComplete(uploadKey);
      this.triggerUpdate();
    }
  }

  /**
   * Mark a file as failed with error message
   */
  markFileFailed(uploadKey: string, fileName: string, error: string): void {
    const upload = this.uploadsMap.get(uploadKey);
    if (!upload || !upload.fileStates) return;

    const currentState = upload.fileStates.get(fileName);
    if (currentState) {
      upload.fileStates.set(fileName, {
        ...currentState,
        status: 'failed',
        error,
      });

      upload.hasFailures = true;
      this.checkAndMarkBatchComplete(uploadKey);
      this.triggerUpdate();
    }
  }

  /**
   * Update progress for an uploading file (throttled to avoid too many updates)
   */
  updateFileProgress(uploadKey: string, fileName: string, progress: number): void {
    const upload = this.uploadsMap.get(uploadKey);
    if (!upload || !upload.fileStates) return;

    const currentState = upload.fileStates.get(fileName);
    if (currentState && currentState.status === 'uploading') {
      // Only update if progress changed by at least 5% to avoid excessive re-renders
      const oldProgress = currentState.progress || 0;
      if (Math.abs(progress - oldProgress) >= 5 || progress === 100) {
        upload.fileStates.set(fileName, {
          ...currentState,
          progress,
        });
        this.triggerUpdate();
      }
    }
  }

  /**
   * Check if all files in a batch are done (completed or failed)
   * and mark the batch as complete
   */
  private checkAndMarkBatchComplete(uploadKey: string): void {
    const upload = this.uploadsMap.get(uploadKey);
    if (!upload || !upload.fileStates) return;

    const fileStates = Array.from(upload.fileStates.values());
    const allDone = fileStates.every(
      (state) => state.status === 'completed' || state.status === 'failed'
    );

    if (allDone) {
      upload.status = 'completed';
      
      // Don't auto-cleanup - let user manually dismiss the notification
      // this.scheduleAutoCleanup(uploadKey, 10000);
    }
  }

  /**
   * Schedule automatic cleanup of a completed upload
   */
  private scheduleAutoCleanup(uploadKey: string, delayMs: number): void {
    // Clear existing timeout if any
    const existingTimeout = this.autoCleanupTimeouts.get(uploadKey);
    if (existingTimeout) {
      clearTimeout(existingTimeout);
    }

    // Schedule new cleanup
    const timeout = setTimeout(() => {
      this.removeUpload(uploadKey);
      this.autoCleanupTimeouts.delete(uploadKey);
    }, delayMs);

    this.autoCleanupTimeouts.set(uploadKey, timeout);
  }

  /**
   * Manually remove an upload (e.g., user dismissed notification)
   */
  removeUpload(uploadKey: string): void {
    // Clear auto-cleanup timeout if exists
    const timeout = this.autoCleanupTimeouts.get(uploadKey);
    if (timeout) {
      clearTimeout(timeout);
      this.autoCleanupTimeouts.delete(uploadKey);
    }

    this.uploadsMap.delete(uploadKey);
    this.triggerUpdate();
  }

  /**
   * Get statistics for an upload batch
   */
  getUploadStats(uploadKey: string): {
    total: number;
    queued: number;
    uploading: number;
    processing: number;
    completed: number;
    failed: number;
    isComplete: boolean;
  } | null {
    const upload = this.uploadsMap.get(uploadKey);
    if (!upload || !upload.fileStates) return null;

    const fileStates = Array.from(upload.fileStates.values());

    return {
      total: fileStates.length,
      queued: fileStates.filter((s) => s.status === 'queued').length,
      uploading: fileStates.filter((s) => s.status === 'uploading').length,
      processing: fileStates.filter((s) => s.status === 'processing').length,
      completed: fileStates.filter((s) => s.status === 'completed').length,
      failed: fileStates.filter((s) => s.status === 'failed').length,
      isComplete: fileStates.every(
        (s) => s.status === 'completed' || s.status === 'failed'
      ),
    };
  }

  /**
   * Trigger update callback to re-render component
   */
  private triggerUpdate(): void {
    // Create new Map instance to trigger React re-render
    this.updateCallback(new Map(this.uploadsMap));
  }

  /**
   * Cleanup all timeouts (call on unmount)
   */
  cleanup(): void {
    this.autoCleanupTimeouts.forEach((timeout) => clearTimeout(timeout));
    this.autoCleanupTimeouts.clear();
  }
}

/**
 * React Hook for using the UploadNotificationManager
 */
export function useUploadNotificationManager(
  uploadsMap: Map<string, UploadBatch>,
  setUploads: (uploads: Map<string, UploadBatch>) => void
): UploadNotificationManager {
  const managerRef = React.useRef<UploadNotificationManager | null>(null);

  if (!managerRef.current) {
    managerRef.current = new UploadNotificationManager(uploadsMap, setUploads);
  }

  // Update the callback when it changes
  React.useEffect(() => {
    if (managerRef.current) {
      managerRef.current = new UploadNotificationManager(uploadsMap, setUploads);
    }
  }, [uploadsMap, setUploads]);

  // Cleanup on unmount
  React.useEffect(() => () => {
    managerRef.current?.cleanup();
  }, []);

  return managerRef.current;
}

// UploadBatch is already exported above, no need to re-export


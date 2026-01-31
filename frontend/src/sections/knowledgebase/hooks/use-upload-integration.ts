/**
 * Integration hook for Upload Manager and Notification System
 * 
 * This connects the existing upload-manager.tsx with the new
 * granular notification system, tracking individual file states.
 */

import { useCallback } from 'react';
import { UploadNotificationManager } from '../components/upload-notification-manager';

interface UseUploadIntegrationProps {
  manager: UploadNotificationManager | null;
  currentKBId: string;
  currentFolderId?: string;
}

export function useUploadIntegration(props: UseUploadIntegrationProps) {
  const { manager, currentKBId, currentFolderId } = props;

  /**
   * Called when upload manager starts uploading files
   * Returns the upload key for tracking
   */
  const handleUploadStart = useCallback(
    (fileNames: string[], kbId: string, folderId?: string): string | undefined => {
      if (!manager || kbId !== currentKBId) return undefined;
      
      // Create upload batch with files in 'queued' state
      const uploadKey = manager.createUpload(kbId, folderId, fileNames);
      
      return uploadKey;
    },
    [manager, currentKBId]
  );

  /**
   * Called when a file starts uploading
   */
  const handleFileUploadStart = useCallback(
    (uploadKey: string, fileName: string) => {
      if (!manager) return;
      manager.markFileUploading(uploadKey, fileName, 0);
    },
    [manager]
  );

  /**
   * Called to update file upload progress
   */
  const handleFileUploadProgress = useCallback(
    (uploadKey: string, fileName: string, progress: number) => {
      if (!manager) return;
      manager.updateFileProgress(uploadKey, fileName, progress);
    },
    [manager]
  );

  /**
   * Called when a file upload completes (backend successfully received it)
   * @param uploadKey - The upload batch identifier
   * @param fileName - The name of the file
   * @param recordId - The record ID returned by backend
   */
  const handleFileUploadComplete = useCallback(
    (uploadKey: string, fileName: string, recordId: string) => {
      if (!manager) return;
      
      console.log('[Upload Integration] File upload complete:', {
        uploadKey,
        fileName,
        recordId,
      });
      
      // Mark as COMPLETED (upload successful)
      // Backend will handle indexing separately
      manager.markFileCompleted(uploadKey, fileName, recordId);
    },
    [manager]
  );

  /**
   * Called when a file upload fails
   */
  const handleFileUploadFailed = useCallback(
    (uploadKey: string, fileName: string, error: string) => {
      if (!manager) return;
      manager.markFileFailed(uploadKey, fileName, error);
    },
    [manager]
  );

  /**
   * Called when upload manager completes (either success or partial success)
   * 
   * @param message - Success message (not used in new system)
   * @param records - Successfully uploaded records from backend
   * @param failedFiles - Files that failed during upload
   */
  const handleUploadSuccess = useCallback(
    async (
      message?: string,
      records?: any[],
      failedFiles?: Array<{ fileName: string; filePath: string; error: string }>
    ) => {
      if (!manager) return;

      // Find the most recent upload key for this KB/folder
      const uploadKeyPrefix = `${currentKBId}-${currentFolderId || 'root'}`;
      
      // Get uploads map (need to access manager's internal state)
      // For now, we'll assume there's only one active upload per KB/folder
      // In production, you might need better tracking
      
      // Mark successful files as processing (waiting for backend indexing)
      if (records && records.length > 0) {
        records.forEach((record) => {
          const fileName = record.recordName || record.name;
          if (fileName) {
            // We need the upload key - construct it based on timing
            // This is a limitation of not having per-file callbacks in upload-manager
            // Best we can do is mark files as processing when batch completes
            const recordId = record.id || record._key;
            
            // Since upload manager doesn't give us per-file updates,
            // we rely on WebSocket events to update individual file states
            // Just mark the batch as sent to backend
            
            console.log('[Upload Integration] File sent to backend:', fileName, recordId);
          }
        });
      }

      // Mark failed files immediately
      if (failedFiles && failedFiles.length > 0) {
        failedFiles.forEach((failedFile) => {
          const fileName = failedFile.fileName || failedFile.filePath;
          
          // Again, we need upload key - this is the limitation
          // For now, log it - WebSocket handler will pick up failures
          console.log('[Upload Integration] File failed:', fileName, failedFile.error);
        });
      }

      // The actual status updates will come from WebSocket events
      // which are handled by use-upload-notifications.ts
    },
    [manager, currentKBId, currentFolderId]
  );

  return {
    handleUploadStart,
    handleFileUploadStart,
    handleFileUploadProgress,
    handleFileUploadComplete,
    handleFileUploadFailed,
    handleUploadSuccess,
  };
}


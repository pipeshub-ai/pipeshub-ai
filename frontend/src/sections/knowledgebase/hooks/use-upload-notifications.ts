/**
 * Hook to manage upload notifications with WebSocket integration
 * 
 * This hook:
 * 1. Manages upload notification state
 * 2. Listens to WebSocket events for backend progress
 * 3. Updates individual file states without UI refreshes
 * 4. Provides callbacks for upload manager integration
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import { UploadNotificationManager, UploadBatch, FileUploadState } from '../components/upload-notification-manager';

interface UseUploadNotificationsOptions {
  currentKBId?: string;
  currentFolderId?: string;
  socketOn?: (event: string, callback: (...args: any[]) => void) => void;
  socketOff?: (event: string, callback?: (...args: any[]) => void) => void;
  socketConnected?: boolean;
  onRecordsProcessed?: (recordIds: string[]) => void;
  onRecordsFailed?: (recordIds: string[]) => void;
}

interface RecordsProcessedEvent {
  recordIds: string[];
  orgId: string;
  kbId?: string;
  folderId?: string;
  totalRecords: number;
  timestamp: number;
}

interface RecordsFailedEvent {
  failedFiles: Array<{
    recordId: string;
    fileName: string;
    filePath: string;
    error: string;
  }>;
  orgId: string;
  kbId?: string;
  folderId?: string;
  totalFailed: number;
  timestamp: number;
}

export function useUploadNotifications(options: UseUploadNotificationsOptions) {
  const { currentKBId, currentFolderId, socketOn, socketOff, socketConnected, onRecordsProcessed, onRecordsFailed } = options;
  
  const [uploads, setUploads] = useState<Map<string, UploadBatch>>(new Map());
  const managerRef = useRef<UploadNotificationManager | null>(null);

  // Initialize manager
  if (!managerRef.current) {
    managerRef.current = new UploadNotificationManager(uploads, setUploads);
  }

  const manager = managerRef.current;

  /**
   * Start tracking an upload batch
   */
  const startUpload = useCallback(
    (files: File[], kbId: string, folderId?: string) => {
      if (!manager) return null;
      
      const uploadKey = manager.createUpload(kbId, folderId, files);
      
      return uploadKey;
    },
    [manager]
  );

  /**
   * Mark a specific file as uploading (called when upload starts)
   */
  const markFileUploading = useCallback(
    (uploadKey: string, fileName: string) => {
      if (!manager) return;
      manager.markFileUploading(uploadKey, fileName, 0);
    },
    [manager]
  );

  /**
   * Update upload progress for a file
   */
  const updateFileProgress = useCallback(
    (uploadKey: string, fileName: string, progress: number) => {
      if (!manager) return;
      manager.updateFileProgress(uploadKey, fileName, progress);
    },
    [manager]
  );

  // Removed markFileProcessing - files are marked as completed immediately after upload

  /**
   * Mark file as failed
   */
  const markFileFailed = useCallback(
    (uploadKey: string, fileName: string, error: string) => {
      if (!manager) return;
      manager.markFileFailed(uploadKey, fileName, error);
    },
    [manager]
  );

  /**
   * Handle upload completion from backend
   * Called from onUploadSuccess callback
   * Note: Individual files are already marked as completed by upload-manager callbacks
   * This just handles any additional cleanup or batch-level failures
   */
  const handleUploadComplete = useCallback(
    (
      kbId: string,
      folderId: string | undefined,
      records: any[],
      failedFiles?: Array<{ fileName: string; filePath: string; error: string }>
    ) => {
      // Files are already marked as completed by onFileUploadComplete callback
      // This function is kept for backward compatibility but doesn't need to do anything
      // Upload failures are also already handled by onFileUploadFailed callback
      
      console.log('[Upload Notifications] Upload batch complete:', {
        kbId,
        folderId,
        totalRecords: records.length,
        totalFailed: failedFiles?.length || 0,
      });
    },
    []
  );

  /**
   * Handle records:processed WebSocket event
   * Note: This updates the main file list's indexing status, NOT the upload notification
   * Upload notifications show only upload status (queued -> uploading -> completed)
   */
  const handleRecordsProcessed = useCallback(
    (data: RecordsProcessedEvent) => {
      // Only process if for current KB/folder
      if (data.kbId !== currentKBId) return;
      
      const eventFolderId = data.folderId || undefined;
      const viewFolderId = currentFolderId || undefined;
      
      if (eventFolderId !== viewFolderId) return;
      
      // Notify parent component to update items list (indexing status)
      if (onRecordsProcessed && data.recordIds.length > 0) {
        onRecordsProcessed(data.recordIds);
      }
    },
    [currentKBId, currentFolderId, onRecordsProcessed]
  );

  /**
   * Handle records:failed WebSocket event
   * Note: This updates the main file list, NOT the upload notification
   * Upload failures are handled during the upload itself, not via socket events
   */
  const handleRecordsFailed = useCallback(
    (data: RecordsFailedEvent) => {
      // Only process if for current KB/folder
      if (data.kbId !== currentKBId) return;
      
      const eventFolderId = data.folderId || undefined;
      const viewFolderId = currentFolderId || undefined;
      
      if (eventFolderId !== viewFolderId) return;

      // Notify parent component to remove failed records from items list
      const failedRecordIds = data.failedFiles.map(f => f.recordId);
      if (onRecordsFailed && failedRecordIds.length > 0) {
        onRecordsFailed(failedRecordIds);
      }
    },
    [currentKBId, currentFolderId, onRecordsFailed]
  );

  /**
   * Set up WebSocket listeners
   */
  useEffect(() => {
    if (!socketConnected || !socketOn || !socketOff) return undefined;

    socketOn('records:processed', handleRecordsProcessed);
    socketOn('records:failed', handleRecordsFailed);

    return () => {
      socketOff('records:processed', handleRecordsProcessed);
      socketOff('records:failed', handleRecordsFailed);
    };
  }, [
    socketConnected,
    socketOn,
    socketOff,
    handleRecordsProcessed,
    handleRecordsFailed,
  ]);

  /**
   * Dismiss a notification
   */
  const dismissUpload = useCallback(
    (uploadKey: string) => {
      if (!manager) return;
      manager.removeUpload(uploadKey);
      
    },
    [manager]
  );

  // Cleanup on unmount
  useEffect(() => () => manager?.cleanup(), [manager]);

  return {
    uploads,
    manager,
    startUpload,
    markFileUploading,
    updateFileProgress,
    markFileFailed,
    handleUploadComplete,
    dismissUpload,
  };
}


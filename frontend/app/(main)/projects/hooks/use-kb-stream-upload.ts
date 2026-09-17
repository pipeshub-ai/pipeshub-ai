'use client';

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { KnowledgeBaseApi } from '@/knowledge-base/api';
import { useUploadStore, generateUploadId } from '@/lib/store/upload-store';

interface StreamedFileEventData {
  filePath?: string;
  fileName?: string;
  errors?: string[];
  message?: string;
}

interface UseKbStreamUploadOptions {
  /** Resolves the target KB id, lazily creating it on first call (`ProjectApi.ensureKnowledgeBase`). */
  getKbId: () => Promise<string>;
  /** Called once after a batch finishes with at least one success, so the caller can refresh its file list. */
  onUploaded?: () => void;
}

/**
 * Flat (no-folder), root-only variant of the Knowledge Base page's streaming
 * upload flow (`app/(main)/knowledge-base/page.tsx`) — same
 * `KnowledgeBaseApi.streamUpload` SSE contract and the same global
 * `useUploadStore` tracker (mounted once in `app/(main)/layout.tsx`), so a
 * project's Files card gets the identical per-file success/failure display
 * as Collections uploads instead of a single opaque "uploading" spinner.
 * Deliberately does not replicate the KB page's folder-priming/batching
 * logic: a project's uploads always land at its linked KB's root.
 */
export function useKbStreamUpload({ getKbId, onUploaded }: UseKbStreamUploadOptions) {
  const { t } = useTranslation();

  const uploadFiles = useCallback(
    async (files: File[]): Promise<void> => {
      if (files.length === 0) return;

      // SSE events correlate by file name, so duplicates would collapse
      // tracker rows. Keep only the last File for each name (matches the
      // FormData append order the backend sees).
      const uniqueByName = new Map(files.map((f) => [f.name, f]));
      files = Array.from(uniqueByName.values());

      let kbId: string;
      try {
        kbId = await getKbId();
      } catch {
        useUploadStore.getState().addItems(
          files.map((file) => ({
            id: generateUploadId(),
            name: file.name,
            type: 'file' as const,
            size: file.size,
            file,
            knowledgeBaseId: '',
            parentId: null,
            status: 'failed' as const,
            errors: [
              t('chat.projects.workspace.knowledgeBaseUnavailable', {
                defaultValue: 'Could not prepare this project’s file storage',
              }),
            ],
          })),
        );
        return;
      }

      const uploadId = generateUploadId();
      const entries = files.map((file) => ({ storeId: generateUploadId(), file }));

      useUploadStore.getState().upsertSession({
        uploadId,
        kbId,
        folderId: null,
        label:
          files.length === 1
            ? files[0].name
            : t('chat.projects.workspace.uploadSessionLabel', {
                defaultValue: '{{count}} files',
                count: files.length,
              }),
      });
      useUploadStore.getState().addItems(
        entries.map((e) => ({
          id: e.storeId,
          name: e.file.name,
          type: 'file' as const,
          size: e.file.size,
          file: e.file,
          knowledgeBaseId: kbId,
          parentId: null,
          uploadId,
        })),
      );
      entries.forEach((e) => useUploadStore.getState().startUpload(e.storeId));

      // `streamUpload` falls back to each file's own `.name` as its
      // `filePath` when no `filesMetadata` is passed — matches the key used
      // here to correlate streamed per-file events back to a tracker row.
      const storeIdByName = new Map(entries.map((e) => [e.file.name, e.storeId]));

      let gotDone = false;
      let gotError: string | null = null;
      let streamError: Error | null = null;
      let anySuccess = false;

      try {
        await KnowledgeBaseApi.streamUpload(
          kbId,
          null,
          entries.map((e) => e.file),
          undefined,
          {
            onEvent: (evt) => {
              const data = evt.data as StreamedFileEventData | undefined;
              const storeId = storeIdByName.get(String(data?.filePath || data?.fileName || ''));
              if (evt.event === 'file:succeeded') {
                if (storeId) useUploadStore.getState().completeUpload(storeId);
                anySuccess = true;
              } else if (evt.event === 'file:failed') {
                if (storeId) {
                  const errors =
                    Array.isArray(data?.errors) && data.errors.length > 0
                      ? data.errors
                      : [t('uploadProgress.uploadFailedDefault', { defaultValue: 'Upload failed' })];
                  useUploadStore.getState().failUpload(storeId, errors);
                }
              } else if (evt.event === 'done') {
                gotDone = true;
              } else if (evt.event === 'error') {
                gotError =
                  (data && typeof data.message === 'string' && data.message) ||
                  t('uploadProgress.serverError', { defaultValue: 'Upload failed on the server' });
              }
            },
            onError: (err) => {
              streamError = err;
            },
          },
        );
      } catch (err) {
        streamError = err instanceof Error ? err : new Error(String(err));
      }

      // Never mark a row completed unless the server confirmed the batch
      // finished (`done`) with no error — a row whose terminal event was
      // missed is failed instead of assumed successful.
      const fallbackMessage =
        (streamError as Error | null)?.message ||
        gotError ||
        t('uploadProgress.uploadIncomplete', { defaultValue: 'Upload incomplete' });
      const succeededCleanly = gotDone && !gotError && !streamError;
      const items = useUploadStore.getState().items;
      entries.forEach((e) => {
        const item = items.find((i) => i.id === e.storeId);
        if (item && (item.status === 'uploading' || item.status === 'pending')) {
          if (succeededCleanly) {
            useUploadStore.getState().completeUpload(e.storeId);
            anySuccess = true;
          } else {
            useUploadStore.getState().failUpload(e.storeId, fallbackMessage);
          }
        }
      });
      useUploadStore.getState().finalizeSession(uploadId);

      if (anySuccess) onUploaded?.();
    },
    [getKbId, onUploaded, t],
  );

  return { uploadFiles };
}

'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { FileRejectionReason } from '@/lib/constants/file-rejection-reason';

export type UploadItemType = 'file' | 'folder';
export type UploadStatus = 'pending' | 'uploading' | 'completed' | 'failed';

// Exported so callers can pre-generate IDs before adding items to the store
export const generateUploadId = () =>
  `upload-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

/** A whole drag-drop = one session (one streaming upload), grouped in the tracker. */
export type UploadSessionStatus = 'active' | 'done';

export interface UploadSession {
  /** Client-generated id for this drop; groups the tracker rows. */
  uploadId: string;
  kbId: string;
  folderId: string | null;
  label: string;
  createdAt: number;
  status: UploadSessionStatus;
}

// File with relative path for folder uploads
export interface FileWithPath {
  file: File;
  relativePath: string; // e.g., "subfolder/nested/document.pdf"
}

export interface UploadItem {
  id: string;
  name: string;
  type: UploadItemType;
  size: number;
  status: UploadStatus;
  progress: number;
  file?: File;
  files?: File[]; // Keep for backward compatibility
  filesWithPaths?: FileWithPath[]; // For folder uploads with path preservation
  // Human-readable failure messages shown in the upload tracker.
  errors?: string[];
  /** Stable codes from the API (`EXCEEDS_SIZE_LIMIT`, `UNSUPPORTED_TYPE`, …). */
  rejectionReasons?: FileRejectionReason[];
  knowledgeBaseId: string;
  parentId: string | null;
  /** Upload session this item belongs to (groups the tracker). */
  uploadId?: string;
  /** Stable path used to correlate streamed per-file events back to this row. */
  filePath?: string;
}

// Accepts a single message or a list; callers may pass either.
const toErrorList = (error?: string | string[]): string[] | undefined => {
  if (error === undefined) return undefined;
  const list = Array.isArray(error) ? error : [error];
  const cleaned = list.map((e) => e?.trim()).filter((e): e is string => !!e);
  return cleaned.length > 0 ? cleaned : undefined;
};

interface UploadState {
  items: UploadItem[];
  sessions: Record<string, UploadSession>;
  isVisible: boolean;
  isCollapsed: boolean;
  totalSize: number;
  completedCount: number;
  totalCount: number;
  /** Completed rows removed by `clearCompleted` (tray only lists failures). */
  clearedCompletedCount: number;
}

interface UploadActions {
  // Pass `id` to use a pre-generated ID (useful for tracking before the item is added).
  // Pass `status`/`errors` to seed an item already in a terminal state (e.g. a file
  // rejected client-side, so it shows as failed immediately).
  addItems: (
    items: Array<
      Omit<UploadItem, 'status' | 'progress' | 'id'> & {
        id?: string;
        status?: UploadStatus;
        progress?: number;
        errors?: string[];
        rejectionReasons?: FileRejectionReason[];
      }
    >,
  ) => void;
  updateItemStatus: (
    id: string,
    status: UploadStatus,
    progress?: number,
    error?: string | string[],
  ) => void;
  bulkUpdateItemStatus: (
    ids: string[],
    status: UploadStatus,
    progress?: number,
  ) => void;
  removeItem: (id: string) => void;
  clearCompleted: () => void;
  clearAll: () => void;
  setVisible: (visible: boolean) => void;
  setCollapsed: (collapsed: boolean) => void;
  startUpload: (id: string) => void;
  completeUpload: (id: string) => void;
  failUpload: (
    id: string,
    error: string | string[],
    rejectionReasons?: FileRejectionReason[],
  ) => void;

  // ---- Session grouping ----
  upsertSession: (
    session: Omit<UploadSession, 'status' | 'createdAt'> & {
      status?: UploadSessionStatus;
      createdAt?: number;
    },
  ) => void;
  /**
   * Finalize a session: mark it done and FAIL any rows still in flight. By the
   * time this runs every row should already be resolved by its streamed
   * terminal event; anything still 'uploading' has no confirmed outcome, so it
   * is failed (never silently completed) to avoid showing unconfirmed success.
   */
  finalizeSession: (uploadId: string, message?: string) => void;
  removeSession: (uploadId: string) => void;
}

type UploadStore = UploadState & UploadActions;

const initialState: UploadState = {
  items: [],
  sessions: {},
  isVisible: false,
  isCollapsed: false,
  totalSize: 0,
  completedCount: 0,
  totalCount: 0,
  clearedCompletedCount: 0,
};

const recountDerived = (state: UploadState): void => {
  state.totalCount = state.items.length;
  state.totalSize = state.items.reduce((sum, item) => sum + item.size, 0);
  state.completedCount = state.items.filter(
    (i) => i.status === 'completed',
  ).length;
};

export const useUploadStore = create<UploadStore>()(
  devtools(
    immer<UploadStore>((set) => ({
      ...initialState,

      addItems: (newItems) =>
        set((state) => {
          const itemsToAdd: UploadItem[] = newItems.map((item) => {
            const status = item.status ?? ('pending' as UploadStatus);
            return {
              ...item,
              id: item.id ?? generateUploadId(),
              status,
              progress: item.progress ?? (status === 'completed' ? 100 : 0),
              errors: toErrorList(item.errors),
              rejectionReasons: item.rejectionReasons?.length
                ? [...item.rejectionReasons]
                : undefined,
            };
          });
          state.items.push(...itemsToAdd);
          recountDerived(state);
          state.isVisible = true;
        }),

      updateItemStatus: (id, status, progress, error) =>
        set((state) => {
          const item = state.items.find((i) => i.id === id);
          if (item) {
            item.status = status;
            if (progress !== undefined) item.progress = progress;
            if (error !== undefined) item.errors = toErrorList(error);
          }
          state.completedCount = state.items.filter(
            (i) => i.status === 'completed',
          ).length;
        }),

      bulkUpdateItemStatus: (ids, status, progress) =>
        set((state) => {
          const idSet = new Set(ids);
          for (const item of state.items) {
            if (idSet.has(item.id)) {
              item.status = status;
              if (progress !== undefined) item.progress = progress;
            }
          }
          state.completedCount = state.items.filter(
            (i) => i.status === 'completed',
          ).length;
        }),

      removeItem: (id) =>
        set((state) => {
          state.items = state.items.filter((item) => item.id !== id);
          recountDerived(state);
          if (state.items.length === 0) {
            state.isVisible = false;
          }
        }),

      clearCompleted: () =>
        set((state) => {
          const completedInTray = state.items.filter(
            (item) => item.status === 'completed',
          ).length;
          state.clearedCompletedCount += completedInTray;
          state.items = state.items.filter(
            (item) => item.status !== 'completed',
          );
          // Drop sessions that no longer have any rows (prevents the sessions
          // map from growing unbounded across many uploads in a long-lived tab).
          const liveUploadIds = new Set(
            state.items.map((i) => i.uploadId).filter(Boolean),
          );
          for (const uploadId of Object.keys(state.sessions)) {
            if (!liveUploadIds.has(uploadId)) delete state.sessions[uploadId];
          }
          recountDerived(state);
          if (state.items.length === 0) {
            state.isVisible = false;
          }
        }),

      clearAll: () =>
        set((state) => {
          state.items = [];
          state.sessions = {};
          state.totalCount = 0;
          state.totalSize = 0;
          state.completedCount = 0;
          state.clearedCompletedCount = 0;
          state.isVisible = false;
        }),

      setVisible: (visible) =>
        set((state) => {
          state.isVisible = visible;
        }),

      setCollapsed: (collapsed) =>
        set((state) => {
          state.isCollapsed = collapsed;
        }),

      startUpload: (id) =>
        set((state) => {
          const item = state.items.find((i) => i.id === id);
          if (item) {
            item.status = 'uploading';
            item.progress = 0;
          }
        }),

      completeUpload: (id) =>
        set((state) => {
          const item = state.items.find((i) => i.id === id);
          // Never override a failure with a late success.
          if (item && item.status !== 'failed') {
            item.status = 'completed';
            item.progress = 100;
            item.errors = undefined;
            item.rejectionReasons = undefined;
          }
          state.completedCount = state.items.filter(
            (i) => i.status === 'completed',
          ).length;
        }),

      failUpload: (id, error, rejectionReasons) =>
        set((state) => {
          const item = state.items.find((i) => i.id === id);
          if (item) {
            item.status = 'failed';
            item.progress = 0;
            item.errors = toErrorList(error);
            item.rejectionReasons =
              rejectionReasons && rejectionReasons.length > 0
                ? [...rejectionReasons]
                : undefined;
          }
          state.completedCount = state.items.filter(
            (i) => i.status === 'completed',
          ).length;
        }),

      upsertSession: (session) =>
        set((state) => {
          const existing = state.sessions[session.uploadId];
          state.sessions[session.uploadId] = {
            uploadId: session.uploadId,
            kbId: session.kbId,
            folderId: session.folderId,
            label: session.label,
            createdAt: existing?.createdAt ?? session.createdAt ?? Date.now(),
            status: session.status ?? existing?.status ?? 'active',
          };
          state.isVisible = true;
        }),

      finalizeSession: (uploadId, message) =>
        set((state) => {
          const s = state.sessions[uploadId];
          if (s) s.status = 'done';
          for (const item of state.items) {
            if (
              item.uploadId === uploadId &&
              (item.status === 'pending' || item.status === 'uploading')
            ) {
              // No confirmed terminal event for this row — fail it rather than
              // assume success.
              item.status = 'failed';
              item.progress = 0;
              item.errors = [message || 'Upload did not complete'];
            }
          }
          state.completedCount = state.items.filter(
            (i) => i.status === 'completed',
          ).length;
        }),

      removeSession: (uploadId) =>
        set((state) => {
          delete state.sessions[uploadId];
        }),
    })),
    { name: 'UploadStore' },
  ),
);

// Selectors
export const selectUploadItems = (state: UploadStore) => state.items;
export const selectSessions = (state: UploadStore) => state.sessions;
export const selectIsVisible = (state: UploadStore) => state.isVisible;
export const selectIsCollapsed = (state: UploadStore) => state.isCollapsed;
export const selectTotalSize = (state: UploadStore) => state.totalSize;
export const selectCompletedCount = (state: UploadStore) => state.completedCount;
export const selectTotalCount = (state: UploadStore) => state.totalCount;
export const selectClearedCompletedCount = (state: UploadStore) =>
  state.clearedCompletedCount;

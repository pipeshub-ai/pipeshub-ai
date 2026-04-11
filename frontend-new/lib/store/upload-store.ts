'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

export type UploadItemType = 'file' | 'folder';
export type UploadStatus = 'pending' | 'uploading' | 'completed' | 'failed';

// Exported so callers can pre-generate IDs before adding items to the store
export const generateUploadId = () =>
  `upload-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

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
  error?: string;
  knowledgeBaseId: string;
  parentId: string | null;
}

interface UploadState {
  items: UploadItem[];
  isVisible: boolean;
  isCollapsed: boolean;
  totalSize: number;
  completedCount: number;
  totalCount: number;
}

interface UploadActions {
  // Pass `id` to use a pre-generated ID (useful for tracking before the item is added)
  addItems: (items: Array<Omit<UploadItem, 'status' | 'progress' | 'id'> & { id?: string }>) => void;
  updateItemStatus: (id: string, status: UploadStatus, progress?: number, error?: string) => void;
  bulkUpdateItemStatus: (ids: string[], status: UploadStatus, progress?: number) => void;
  removeItem: (id: string) => void;
  clearCompleted: () => void;
  clearAll: () => void;
  setVisible: (visible: boolean) => void;
  setCollapsed: (collapsed: boolean) => void;
  startUpload: (id: string) => void;
  completeUpload: (id: string) => void;
  failUpload: (id: string, error: string) => void;
}

type UploadStore = UploadState & UploadActions;

const initialState: UploadState = {
  items: [],
  isVisible: false,
  isCollapsed: false,
  totalSize: 0,
  completedCount: 0,
  totalCount: 0,
};

export const useUploadStore = create<UploadStore>()(
  devtools(
    immer((set, _get) => ({
      ...initialState,

      addItems: (newItems) =>
        set((state) => {
          const itemsToAdd: UploadItem[] = newItems.map((item) => ({
            ...item,
            // Use caller-provided id when pre-generated, otherwise generate one
            id: item.id ?? generateUploadId(),
            status: 'pending' as UploadStatus,
            progress: 0,
          }));

          state.items.push(...itemsToAdd);
          state.totalCount = state.items.length;
          state.totalSize = state.items.reduce((sum, item) => sum + item.size, 0);
          state.isVisible = true;
        }),

      updateItemStatus: (id, status, progress, error) =>
        set((state) => {
          const item = state.items.find((i) => i.id === id);
          if (item) {
            item.status = status;
            if (progress !== undefined) item.progress = progress;
            if (error !== undefined) item.error = error;
          }
          state.completedCount = state.items.filter((i) => i.status === 'completed').length;
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
          state.completedCount = state.items.filter((i) => i.status === 'completed').length;
        }),

      removeItem: (id) =>
        set((state) => {
          state.items = state.items.filter((item) => item.id !== id);
          state.totalCount = state.items.length;
          state.totalSize = state.items.reduce((sum, item) => sum + item.size, 0);
          state.completedCount = state.items.filter((i) => i.status === 'completed').length;
          if (state.items.length === 0) {
            state.isVisible = false;
          }
        }),

      clearCompleted: () =>
        set((state) => {
          state.items = state.items.filter((item) => item.status !== 'completed');
          state.totalCount = state.items.length;
          state.totalSize = state.items.reduce((sum, item) => sum + item.size, 0);
          state.completedCount = 0;
          if (state.items.length === 0) {
            state.isVisible = false;
          }
        }),

      clearAll: () =>
        set((state) => {
          state.items = [];
          state.totalCount = 0;
          state.totalSize = 0;
          state.completedCount = 0;
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
          if (item) {
            item.status = 'completed';
            item.progress = 100;
          }
          state.completedCount = state.items.filter((i) => i.status === 'completed').length;
        }),

      failUpload: (id, error) =>
        set((state) => {
          const item = state.items.find((i) => i.id === id);
          if (item) {
            item.status = 'failed';
            item.error = error;
          }
        }),
    })),
    { name: 'UploadStore' }
  )
);

// Selectors
export const selectUploadItems = (state: UploadStore) => state.items;
export const selectIsVisible = (state: UploadStore) => state.isVisible;
export const selectIsCollapsed = (state: UploadStore) => state.isCollapsed;
export const selectTotalSize = (state: UploadStore) => state.totalSize;
export const selectCompletedCount = (state: UploadStore) => state.completedCount;
export const selectTotalCount = (state: UploadStore) => state.totalCount;

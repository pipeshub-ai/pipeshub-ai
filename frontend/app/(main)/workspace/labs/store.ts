'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { AvailableFlag } from './api';

// ========================================
// Types
// ========================================

export interface LabsFormData {
  /** File size limit displayed in MB; '' when the field is empty */
  fileSizeLimitMb: number | '';
  /** Dynamic flag values keyed by flag.key */
  featureFlags: Record<string, boolean>;
}

export interface LabsErrors {
  fileSizeLimitMb?: string;
}

interface LabsState {
  form: LabsFormData;
  savedForm: LabsFormData;
  /** Server-side descriptors for all available feature flags */
  availableFlags: AvailableFlag[];
  errors: LabsErrors;
  discardDialogOpen: boolean;
  isLoading: boolean;
}

interface LabsActions {
  setFileSizeLimitMb: (value: number | '') => void;
  setFlagValue: (key: string, value: boolean) => void;
  setForm: (form: LabsFormData, availableFlags: AvailableFlag[]) => void;
  markSaved: () => void;
  setErrors: (errors: LabsErrors) => void;
  discardChanges: () => void;
  setDiscardDialogOpen: (open: boolean) => void;
  setLoading: (loading: boolean) => void;
  isDirty: () => boolean;
}

export type LabsStore = LabsState & LabsActions;

// ========================================
// Defaults
// ========================================

const DEFAULT_FORM: LabsFormData = {
  fileSizeLimitMb: '',
  featureFlags: {},
};

// ========================================
// Store
// ========================================

export const useLabsStore = create<LabsStore>()(
  devtools(
    immer((set, get) => ({
      // ── State ──────────────────────────────────────
      form: { ...DEFAULT_FORM },
      savedForm: { ...DEFAULT_FORM },
      availableFlags: [],
      errors: {},
      discardDialogOpen: false,
      isLoading: true,

      // ── Actions ────────────────────────────────────
      setFileSizeLimitMb: (value) =>
        set((state) => {
          state.form.fileSizeLimitMb = value;
          delete state.errors.fileSizeLimitMb;
        }),

      setFlagValue: (key, value) =>
        set((state) => {
          state.form.featureFlags[key] = value;
        }),

      setForm: (form, availableFlags) =>
        set((state) => {
          state.form = form;
          state.savedForm = { ...form, featureFlags: { ...form.featureFlags } };
          state.availableFlags = availableFlags;
          state.isLoading = false;
        }),

      markSaved: () =>
        set((state) => {
          state.savedForm = {
            ...state.form,
            featureFlags: { ...state.form.featureFlags },
          };
        }),

      setErrors: (errors) =>
        set((state) => {
          state.errors = errors;
        }),

      discardChanges: () =>
        set((state) => {
          state.form = {
            ...state.savedForm,
            featureFlags: { ...state.savedForm.featureFlags },
          };
          state.errors = {};
          state.discardDialogOpen = false;
        }),

      setDiscardDialogOpen: (open) =>
        set((state) => {
          state.discardDialogOpen = open;
        }),

      setLoading: (loading) =>
        set((state) => {
          state.isLoading = loading;
        }),

      isDirty: () => {
        const { form, savedForm } = get();
        if (form.fileSizeLimitMb !== savedForm.fileSizeLimitMb) return true;
        const keys = Object.keys({ ...form.featureFlags, ...savedForm.featureFlags });
        return keys.some((k) => form.featureFlags[k] !== savedForm.featureFlags[k]);
      },
    })),
    { name: 'LabsStore' }
  )
);

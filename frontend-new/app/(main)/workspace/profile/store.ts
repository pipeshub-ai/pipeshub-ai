'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

// ========================================
// Types
// ========================================

export interface ProfileFormData {
  /** Full display name */
  fullName: string;
  /** Job title / role in the company */
  designation: string;
}

export interface ProfileFormErrors {
  fullName?: string;
}

// ========================================
// State
// ========================================

interface ProfileState {
  /** Current (possibly edited) form values */
  form: ProfileFormData;
  /** Last-saved snapshot — used for dirty detection */
  savedForm: ProfileFormData;
  /** Field-level validation errors */
  errors: ProfileFormErrors;
  /** Whether the discard-confirmation dialog is open */
  discardDialogOpen: boolean;
  /** Whether the initial data is loading */
  isLoading: boolean;
}

// ========================================
// Actions
// ========================================

interface ProfileActions {
  setField: <K extends keyof ProfileFormData>(key: K, value: ProfileFormData[K]) => void;
  setForm: (data: ProfileFormData) => void;
  markSaved: () => void;
  setErrors: (errors: ProfileFormErrors) => void;
  clearErrors: () => void;
  discardChanges: () => void;
  setDiscardDialogOpen: (open: boolean) => void;
  setLoading: (loading: boolean) => void;
  isDirty: () => boolean;
  reset: () => void;
}

type ProfileStore = ProfileState & ProfileActions;

// ========================================
// Initial values
// ========================================

const EMPTY_FORM: ProfileFormData = {
  fullName: '',
  designation: '',
};

const initialState: ProfileState = {
  form: { ...EMPTY_FORM },
  savedForm: { ...EMPTY_FORM },
  errors: {},
  discardDialogOpen: false,
  isLoading: true,
};

// ========================================
// Store
// ========================================

export const useProfileStore = create<ProfileStore>()(
  devtools(
    immer((set, get) => ({
      ...initialState,

      setField: (key, value) =>
        set((state) => {
          state.form[key] = value as never;
        }),

      setForm: (data) =>
        set((state) => {
          state.form = { ...data };
          state.savedForm = { ...data };
        }),

      markSaved: () =>
        set((state) => {
          state.savedForm = { ...state.form };
        }),

      setErrors: (errors) =>
        set((state) => {
          state.errors = errors;
        }),

      clearErrors: () =>
        set((state) => {
          state.errors = {};
        }),

      discardChanges: () =>
        set((state) => {
          state.form = { ...state.savedForm };
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
        return (
          form.fullName !== savedForm.fullName ||
          form.designation !== savedForm.designation
        );
      },

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'profile-store' }
  )
);

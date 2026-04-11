'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

// ========================================
// Types
// ========================================

export interface GeneralFormData {
  registeredName: string;
  displayName: string;
  contactEmail: string;
  streetAddress: string;
  country: string;
  state: string;
  city: string;
  zipCode: string;
  dataCollection: boolean;
  logoFileName: string | null;
}

export interface FormErrors {
  contactEmail?: string;
  zipCode?: string;
}

// ========================================
// State
// ========================================

interface GeneralState {
  /** Current form values (may have unsaved edits) */
  form: GeneralFormData;
  /** Last-saved snapshot — used for dirty detection */
  savedForm: GeneralFormData;
  /** Field-level validation errors */
  errors: FormErrors;
  /** Whether the discard-confirmation dialog is open */
  discardDialogOpen: boolean;
  /** Initial data load in progress */
  isLoading: boolean;
}

// ========================================
// Actions
// ========================================

interface GeneralActions {
  /** Update a single form field */
  setField: <K extends keyof GeneralFormData>(key: K, value: GeneralFormData[K]) => void;
  /** Bulk-set the entire form (e.g. after fetching org data) */
  setForm: (data: GeneralFormData) => void;
  /** Mark the current form as the saved baseline */
  markSaved: () => void;
  /** Set validation errors */
  setErrors: (errors: FormErrors) => void;
  /** Clear all validation errors */
  clearErrors: () => void;
  /** Revert form to last-saved state and clear errors */
  discardChanges: () => void;
  /** Toggle the discard dialog */
  setDiscardDialogOpen: (open: boolean) => void;
  /** Set loading state */
  setLoading: (loading: boolean) => void;
  /** Whether the form has unsaved changes */
  isDirty: () => boolean;
  /** Full reset to initial state */
  reset: () => void;
}

type GeneralStore = GeneralState & GeneralActions;

// ========================================
// Initial values
// ========================================

const EMPTY_FORM: GeneralFormData = {
  registeredName: '',
  displayName: '',
  contactEmail: '',
  streetAddress: '',
  country: '',
  state: '',
  city: '',
  zipCode: '',
  dataCollection: false,
  logoFileName: null,
};

const initialState: GeneralState = {
  form: { ...EMPTY_FORM },
  savedForm: { ...EMPTY_FORM },
  errors: {},
  discardDialogOpen: false,
  isLoading: true,
};

// ========================================
// Helpers
// ========================================

function isFormEqual(a: GeneralFormData, b: GeneralFormData): boolean {
  return (
    a.registeredName === b.registeredName &&
    a.displayName === b.displayName &&
    a.contactEmail === b.contactEmail &&
    a.streetAddress === b.streetAddress &&
    a.country === b.country &&
    a.state === b.state &&
    a.city === b.city &&
    a.zipCode === b.zipCode &&
    a.dataCollection === b.dataCollection &&
    a.logoFileName === b.logoFileName
  );
}

// ========================================
// Store
// ========================================

export const useGeneralStore = create<GeneralStore>()(
  devtools(
    immer((set, get) => ({
      ...initialState,

      setField: (key, value) =>
        set((state) => {
          (state.form as Record<string, unknown>)[key] = value;
          // Clear associated validation error when user types
          if (key === 'contactEmail') {
            delete state.errors.contactEmail;
          } else if (key === 'zipCode') {
            delete state.errors.zipCode;
          }
        }),

      setForm: (data) =>
        set((state) => {
          state.form = data;
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
        const s = get();
        return !isFormEqual(s.form, s.savedForm);
      },

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'GeneralStore' }
  )
);

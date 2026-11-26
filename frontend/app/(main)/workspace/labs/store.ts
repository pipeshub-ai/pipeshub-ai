'use client';

import { create } from 'zustand';
import { resetStoreOnSessionEnd } from '@/lib/api/session-cache';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { AvailableFlag, PlatformSettingsChanges, PlatformSettingsPayload } from './api';
import { bytesToMb, mbToBytes } from './api';

// ========================================
// Types
// ========================================

export interface LabsFormData {
  /** File size limit displayed in MB; '' when the field is empty */
  fileSizeLimitMb: number | '';
  /** Dynamic flag values keyed by flag.key */
  featureFlags: Record<string, boolean>;
  /** Flags the admin asked to follow their release default again ("Reset to default"). */
  resets: string[];
}

export interface LabsErrors {
  fileSizeLimitMb?: string;
}

interface LabsState {
  form: LabsFormData;
  savedForm: LabsFormData;
  /** The stored upload limit in bytes, as loaded: what a save says it expected (UX2). */
  savedFileUploadBytes: number | null;
  /** Flags someone else changed while this admin edited (UX2): shown until the next save or discard. */
  conflicts: string[];
  /** Server-side descriptors for all available feature flags */
  availableFlags: AvailableFlag[];
  /** Flags the admin changed earlier (UX1); every other flag follows its release default. */
  overrides: Record<string, boolean>;
  errors: LabsErrors;
  discardDialogOpen: boolean;
  isLoading: boolean;
}

interface LabsActions {
  setFileSizeLimitMb: (value: number | '') => void;
  setFlagValue: (key: string, value: boolean) => void;
  resetFlag: (key: string) => void;
  setForm: (
    form: LabsFormData,
    availableFlags: AvailableFlag[],
    overrides?: Record<string, boolean>,
    savedFileUploadBytes?: number | null,
  ) => void;
  /** Someone else saved Labs first (UX2): load what is stored now and keep this admin's unsaved changes on top. */
  rebaseOnServer: (settings: PlatformSettingsPayload) => void;
  markSaved: () => void;
  setErrors: (errors: LabsErrors) => void;
  discardChanges: () => void;
  setDiscardDialogOpen: (open: boolean) => void;
  setLoading: (loading: boolean) => void;
  isDirty: () => boolean;
}

export type LabsStore = LabsState & LabsActions;

// ========================================
// Pure rules
// ========================================

const defaultOf = (flags: AvailableFlag[], key: string): boolean =>
  !!flags.find((flag) => flag.key === key)?.defaultEnabled;

/**
 * What a save sends (UX1): only the flags whose value the admin changed, and
 * each flag reset to its release default. Never the whole list, so a flag
 * nobody touched keeps following its default when a release changes it.
 */
export function labsChanges(
  form: LabsFormData,
  saved: LabsFormData,
  flags: AvailableFlag[],
  savedFileUploadBytes: number | null = null,
): PlatformSettingsChanges {
  const featureFlags: Record<string, boolean> = {};
  for (const [key, value] of Object.entries(form.featureFlags)) {
    if (saved.featureFlags[key] !== value) featureFlags[key] = value;
  }
  for (const key of form.resets) featureFlags[key] = defaultOf(flags, key);
  const sizeChanged = form.fileSizeLimitMb !== saved.fileSizeLimitMb && form.fileSizeLimitMb !== '';
  // UX2: each setting sent carries what this page showed, so a newer choice by someone else is refused, not overwritten.
  const expectedFlags = Object.fromEntries(
    Object.keys(featureFlags)
      .filter((key) => typeof saved.featureFlags[key] === 'boolean')
      .map((key) => [key, saved.featureFlags[key]]),
  );
  const expected = {
    ...(Object.keys(expectedFlags).length > 0 ? { featureFlags: expectedFlags } : {}),
    ...(sizeChanged && savedFileUploadBytes !== null ? { fileUploadMaxSizeBytes: savedFileUploadBytes } : {}),
  };
  return {
    ...(sizeChanged ? { fileUploadMaxSizeBytes: mbToBytes(Number(form.fileSizeLimitMb)) } : {}),
    ...(Object.keys(featureFlags).length > 0 ? { featureFlags } : {}),
    ...(Object.keys(expected).length > 0 ? { expected } : {}),
  };
}

/** Every listed flag's value as the server now reports it; a flag it does not report is at its release default. */
export function storedFlags(
  settings: PlatformSettingsPayload,
  flags: AvailableFlag[],
): Record<string, boolean> {
  const server = settings.featureFlags ?? {};
  return Object.fromEntries(
    flags.map((flag) => [flag.key, typeof server[flag.key] === 'boolean' ? server[flag.key] : !!flag.defaultEnabled]),
  );
}

/** The overrides after a save, as the server records them: a flag at its default has none. */
export function overridesAfter(
  overrides: Record<string, boolean>,
  sent: Record<string, boolean>,
  flags: AvailableFlag[],
): Record<string, boolean> {
  const next = { ...overrides };
  for (const [key, value] of Object.entries(sent)) {
    if (value === defaultOf(flags, key)) delete next[key];
    else next[key] = value;
  }
  return next;
}

// ========================================
// Defaults
// ========================================

const DEFAULT_FORM: LabsFormData = {
  fileSizeLimitMb: '',
  featureFlags: {},
  resets: [],
};

const copyForm = (form: LabsFormData): LabsFormData => ({
  ...form,
  featureFlags: { ...form.featureFlags },
  resets: [...form.resets],
});

// ========================================
// Store
// ========================================

export const useLabsStore = create<LabsStore>()(
  devtools(
    immer((set, get) => ({
      // ── State ──────────────────────────────────────
      form: copyForm(DEFAULT_FORM),
      savedForm: copyForm(DEFAULT_FORM),
      savedFileUploadBytes: null,
      conflicts: [],
      availableFlags: [],
      overrides: {},
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
          state.form.resets = state.form.resets.filter((k) => k !== key);
        }),

      resetFlag: (key) =>
        set((state) => {
          state.form.featureFlags[key] = defaultOf(state.availableFlags, key);
          if (!state.form.resets.includes(key)) state.form.resets.push(key);
        }),

      setForm: (form, availableFlags, overrides = {}, savedFileUploadBytes = null) =>
        set((state) => {
          state.form = copyForm(form);
          state.savedForm = copyForm(form);
          state.savedFileUploadBytes = savedFileUploadBytes;
          state.availableFlags = availableFlags;
          state.overrides = { ...overrides };
          state.conflicts = [];
          state.isLoading = false;
        }),

      rebaseOnServer: (settings) =>
        set((state) => {
          const pending = labsChanges(state.form, state.savedForm, state.availableFlags);
          const stored = storedFlags(settings, state.availableFlags);
          state.conflicts = state.availableFlags
            .map((flag) => flag.key)
            .filter((key) => stored[key] !== state.savedForm.featureFlags[key]);
          const sizeEdited = state.form.fileSizeLimitMb !== state.savedForm.fileSizeLimitMb;
          const editedSize = state.form.fileSizeLimitMb;
          state.savedForm = {
            fileSizeLimitMb: bytesToMb(settings.fileUploadMaxSizeBytes),
            featureFlags: stored,
            resets: [],
          };
          state.savedFileUploadBytes = settings.fileUploadMaxSizeBytes;
          state.overrides = { ...(settings.featureFlagOverrides ?? {}) };
          const resets = state.form.resets;
          state.form = copyForm(state.savedForm);
          state.form.resets = [...resets];
          for (const [key, value] of Object.entries(pending.featureFlags ?? {})) state.form.featureFlags[key] = value;
          if (sizeEdited) state.form.fileSizeLimitMb = editedSize;
        }),

      markSaved: () =>
        set((state) => {
          const sent = labsChanges(state.form, state.savedForm, state.availableFlags).featureFlags ?? {};
          state.overrides = overridesAfter(state.overrides, sent, state.availableFlags);
          if (state.form.fileSizeLimitMb !== '' && state.form.fileSizeLimitMb !== state.savedForm.fileSizeLimitMb) {
            state.savedFileUploadBytes = mbToBytes(Number(state.form.fileSizeLimitMb));
          }
          state.form.resets = [];
          state.savedForm = copyForm(state.form);
          state.conflicts = [];
        }),

      setErrors: (errors) =>
        set((state) => {
          state.errors = errors;
        }),

      discardChanges: () =>
        set((state) => {
          state.form = copyForm(state.savedForm);
          state.errors = {};
          state.conflicts = [];
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
        if (form.resets.length > 0) return true;
        const keys = Object.keys({ ...form.featureFlags, ...savedForm.featureFlags });
        return keys.some((k) => form.featureFlags[k] !== savedForm.featureFlags[k]);
      },
    })),
    { name: 'LabsStore' }
  )
);

resetStoreOnSessionEnd(useLabsStore);

import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { devtools } from 'zustand/middleware';
import type { EditingSkillTarget, EditorTab, SkillCandidate, SkillMetadata } from './types';

// ========================================
// Store shape
// ========================================

/** Prefill data passed when opening the editor from a candidate review. */
export interface EditorPrefill {
  prefillName?: string;
  prefillDescription?: string;
  prefillBody?: string;
  prefillCategory?: string;
  prefillSubcategory?: string;
  prefillTags?: string[];
  candidateId?: string;
}

interface SkillsState {
  // ── Data ──
  skills: SkillMetadata[];
  candidates: SkillCandidate[];

  // ── UI ──
  isLoading: boolean;
  error: string | null;
  searchQuery: string;
  statusFilter: string | null;

  // ── Editor panel ──
  editorOpen: boolean;
  editingSkillName: EditingSkillTarget;
  editorTab: EditorTab;
  editorPrefill: EditorPrefill | null;

  // ── Import dialog ──
  importDialogOpen: boolean;

  // ── Candidates panel ──
  candidatesPanelOpen: boolean;
}

interface SkillsActions {
  setSkills: (skills: SkillMetadata[]) => void;
  setCandidates: (candidates: SkillCandidate[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setSearchQuery: (query: string) => void;
  setStatusFilter: (status: string | null) => void;

  openCreateEditor: () => void;
  openEditEditor: (name: string) => void;
  openEditorPanel: (name: EditingSkillTarget, prefill?: EditorPrefill) => void;
  closeEditor: () => void;
  setEditorTab: (tab: EditorTab) => void;

  openImportDialog: () => void;
  closeImportDialog: () => void;

  openCandidatesPanel: () => void;
  closeCandidatesPanel: () => void;

  reset: () => void;
}

// ========================================
// Initial state
// ========================================

const initialState: SkillsState = {
  skills: [],
  candidates: [],
  isLoading: false,
  error: null,
  searchQuery: '',
  statusFilter: null,
  editorOpen: false,
  editingSkillName: null,
  editorTab: 'content',
  editorPrefill: null,
  importDialogOpen: false,
  candidatesPanelOpen: false,
};

// ========================================
// Store
// ========================================

export const useSkillsStore = create<SkillsState & SkillsActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setSkills: (skills) =>
        set((state) => {
          state.skills = skills;
        }),

      setCandidates: (candidates) =>
        set((state) => {
          state.candidates = candidates;
        }),

      setLoading: (loading) =>
        set((state) => {
          state.isLoading = loading;
        }),

      setError: (error) =>
        set((state) => {
          state.error = error;
        }),

      setSearchQuery: (query) =>
        set((state) => {
          state.searchQuery = query;
        }),

      setStatusFilter: (status) =>
        set((state) => {
          state.statusFilter = status;
        }),

      openCreateEditor: () =>
        set((state) => {
          state.editorOpen = true;
          state.editingSkillName = null;
          state.editorTab = 'content';
          state.editorPrefill = null;
        }),

      openEditEditor: (name) =>
        set((state) => {
          state.editorOpen = true;
          state.editingSkillName = name;
          state.editorTab = 'content';
          state.editorPrefill = null;
        }),

      openEditorPanel: (name, prefill) =>
        set((state) => {
          state.editorOpen = true;
          state.editingSkillName = name;
          state.editorTab = 'content';
          state.editorPrefill = prefill ?? null;
        }),

      closeEditor: () =>
        set((state) => {
          state.editorOpen = false;
          state.editingSkillName = null;
          state.editorPrefill = null;
        }),

      setEditorTab: (tab) =>
        set((state) => {
          state.editorTab = tab;
        }),

      openImportDialog: () =>
        set((state) => {
          state.importDialogOpen = true;
        }),

      closeImportDialog: () =>
        set((state) => {
          state.importDialogOpen = false;
        }),

      openCandidatesPanel: () =>
        set((state) => {
          state.candidatesPanelOpen = true;
        }),

      closeCandidatesPanel: () =>
        set((state) => {
          state.candidatesPanelOpen = false;
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'skills-store' }
  )
);

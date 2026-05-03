'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { AIModelProvider, ConfiguredModel } from './types';
import type { CapabilitySection } from './types';

// ========================================
// State
// ========================================

export type MainSection = 'providers' | 'configured';

interface AIModelsState {
  providers: AIModelProvider[];
  configuredModels: Record<string, ConfiguredModel[]>;

  isLoadingProviders: boolean;
  isLoadingModels: boolean;

  searchQuery: string;
  mainSection: MainSection;
  capabilitySection: CapabilitySection;

  dialogOpen: boolean;
  dialogMode: 'add' | 'edit';
  dialogProvider: AIModelProvider | null;
  dialogCapability: string | null;
  dialogEditModel: ConfiguredModel | null;

  deleteDialogOpen: boolean;
  deleteTarget: { modelType: string; modelKey: string; modelName: string } | null;
}

// ========================================
// Actions
// ========================================

interface AIModelsActions {
  setProviders: (providers: AIModelProvider[]) => void;
  setConfiguredModels: (models: Record<string, ConfiguredModel[]>) => void;

  setLoadingProviders: (loading: boolean) => void;
  setLoadingModels: (loading: boolean) => void;

  setSearchQuery: (query: string) => void;
  setMainSection: (section: MainSection) => void;
  setCapabilitySection: (section: CapabilitySection) => void;

  openAddDialog: (provider: AIModelProvider, capability: string) => void;
  openEditDialog: (provider: AIModelProvider, capability: string, model: ConfiguredModel) => void;
  closeDialog: () => void;

  openDeleteDialog: (modelType: string, modelKey: string, modelName: string) => void;
  closeDeleteDialog: () => void;

  reset: () => void;
}

// ========================================
// Initial state
// ========================================

const initialState: AIModelsState = {
  providers: [],
  configuredModels: {},
  isLoadingProviders: true,
  isLoadingModels: true,
  searchQuery: '',
  mainSection: 'providers',
  capabilitySection: 'text_generation',
  dialogOpen: false,
  dialogMode: 'add',
  dialogProvider: null,
  dialogCapability: null,
  dialogEditModel: null,
  deleteDialogOpen: false,
  deleteTarget: null,
};

// ========================================
// Store
// ========================================

export const useAIModelsStore = create<AIModelsState & AIModelsActions>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setProviders: (providers) =>
        set((s) => {
          s.providers = providers;
        }),
      setConfiguredModels: (models) =>
        set((s) => {
          s.configuredModels = models;
        }),

      setLoadingProviders: (loading) =>
        set((s) => {
          s.isLoadingProviders = loading;
        }),
      setLoadingModels: (loading) =>
        set((s) => {
          s.isLoadingModels = loading;
        }),

      setSearchQuery: (query) =>
        set((s) => {
          s.searchQuery = query;
        }),
      setMainSection: (section) =>
        set((s) => {
          s.mainSection = section;
        }),
      setCapabilitySection: (section) =>
        set((s) => {
          s.capabilitySection = section;
        }),

      openAddDialog: (provider, capability) =>
        set((s) => {
          s.dialogOpen = true;
          s.dialogMode = 'add';
          s.dialogProvider = provider;
          s.dialogCapability = capability;
          s.dialogEditModel = null;
        }),
      openEditDialog: (provider, capability, model) =>
        set((s) => {
          s.dialogOpen = true;
          s.dialogMode = 'edit';
          s.dialogProvider = provider;
          s.dialogCapability = capability;
          s.dialogEditModel = model;
        }),
      closeDialog: () =>
        set((s) => {
          s.dialogOpen = false;
          s.dialogProvider = null;
          s.dialogCapability = null;
          s.dialogEditModel = null;
        }),

      openDeleteDialog: (modelType, modelKey, modelName) =>
        set((s) => {
          s.deleteDialogOpen = true;
          s.deleteTarget = { modelType, modelKey, modelName };
        }),
      closeDeleteDialog: () =>
        set((s) => {
          s.deleteDialogOpen = false;
          s.deleteTarget = null;
        }),

      reset: () => set(() => ({ ...initialState })),
    })),
    { name: 'ai-models-store' }
  )
);

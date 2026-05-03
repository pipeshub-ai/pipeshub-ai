'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type {
  OnboardingStep,
  OnboardingStepId,
  OrgProfileFormData,
  AiModelFormData,
  EmbeddingModelFormData,
  StorageFormData,
  SmtpFormData,
} from './types';

// ===============================
// Default Steps (server-configurable)
// ===============================

export const DEFAULT_ONBOARDING_STEPS: OnboardingStep[] = [
  // {
  //   id: 'org-profile',
  //   title: 'onboarding.steps.orgProfile.title',
  //   description: 'onboarding.steps.orgProfile.description',
  //   required: true,
  // },
  {
    id: 'ai-model',
    title: 'onboarding.steps.aiModel.title',
    description: 'onboarding.steps.aiModel.description',
    required: true,
  },
  {
    id: 'embedding-model',
    title: 'onboarding.steps.embeddingModel.title',
    description: 'onboarding.steps.embeddingModel.description',
    required: true,
  },
  {
    id: 'storage',
    title: 'onboarding.steps.storage.title',
    description: 'onboarding.steps.storage.description',
    required: false,
  },
  {
    id: 'smtp',
    title: 'onboarding.steps.smtp.title',
    description: 'onboarding.steps.smtp.description',
    required: false,
  },
];

// ===============================
// Store Interface
// ===============================

interface OnboardingState {
  // Feature flags (hardcoded for dev, will be server-driven)
  isOnboardingActive: boolean;

  // Step config — can be replaced by server response
  steps: OnboardingStep[];

  // Navigation
  currentStepId: OnboardingStepId;

  // Tracks steps that have been saved/submitted
  completedStepIds: OnboardingStepId[];

  // Org created context (shown in header after step 0)
  orgDisplayName: string;
  orgInitial: string;

  // Form data
  orgProfile: OrgProfileFormData;
  aiModel: AiModelFormData;
  embeddingModel: EmbeddingModelFormData;
  storage: StorageFormData;
  smtp: SmtpFormData;

  // Submission state
  submitting: boolean;
  submitStatus: 'idle' | 'loading' | 'success' | 'error';
}

interface OnboardingActions {
  setSteps: (steps: OnboardingStep[]) => void;
  setCurrentStep: (stepId: OnboardingStepId) => void;
  markStepCompleted: (stepId: OnboardingStepId) => void;
  unmarkStepCompleted: (stepId: OnboardingStepId) => void;
  setOrgContext: (displayName: string) => void;
  setOrgProfile: (data: Partial<OrgProfileFormData>) => void;
  setAiModel: (data: Partial<AiModelFormData>) => void;
  setEmbeddingModel: (data: Partial<EmbeddingModelFormData>) => void;
  setStorage: (data: Partial<StorageFormData>) => void;
  setSmtp: (data: Partial<SmtpFormData>) => void;
  setSubmitting: (submitting: boolean) => void;
  setSubmitStatus: (status: 'idle' | 'loading' | 'success' | 'error') => void;
  setOnboardingActive: (active: boolean) => void;
  reset: () => void;
}

export type OnboardingStore = OnboardingState & OnboardingActions;

// ===============================
// Initial State
// ===============================

const initialState: OnboardingState = {
  isOnboardingActive: false,

  steps: DEFAULT_ONBOARDING_STEPS,
  currentStepId: 'ai-model',
  completedStepIds: [],

  orgDisplayName: '',
  orgInitial: '',

  orgProfile: {
    organizationName: '',
    displayName: '',
    streetAddress: '',
    country: '',
    state: '',
    city: '',
    zipCode: '',
  },
  aiModel: {
    provider: '',
    apiKey: '',
    model: '',
    isReasoning: false,
    isMultimodal: false,
  },
  embeddingModel: {
    providerType: 'default',
    apiKey: '',
    model: '',
    isMultimodal: false,
  },
  storage: {
    providerType: 'local',
  },
  smtp: {
    host: '',
    port: 587,
    fromEmail: '',
    username: '',
    password: '',
  },

  submitting: false,
  submitStatus: 'idle',
};

// ===============================
// Store
// ===============================

export const useOnboardingStore = create<OnboardingStore>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setSteps: (steps) =>
        set((state) => {
          state.steps = steps;
        }),

      setCurrentStep: (stepId) =>
        set((state) => {
          state.currentStepId = stepId;
        }),

      markStepCompleted: (stepId) =>
        set((state) => {
          if (!state.completedStepIds.includes(stepId)) {
            state.completedStepIds.push(stepId);
          }
        }),

      unmarkStepCompleted: (stepId) =>
        set((state) => {
          state.completedStepIds = state.completedStepIds.filter((id) => id !== stepId);
        }),

      setOrgContext: (displayName) =>
        set((state) => {
          state.orgDisplayName = displayName;
          state.orgInitial = displayName.charAt(0).toUpperCase();
        }),

      setOrgProfile: (data) =>
        set((state) => {
          Object.assign(state.orgProfile, data);
        }),

      setAiModel: (data) =>
        set((state) => {
          Object.assign(state.aiModel, data);
        }),

      setEmbeddingModel: (data) =>
        set((state) => {
          Object.assign(state.embeddingModel, data);
        }),

      setStorage: (data) =>
        set((state) => {
          Object.assign(state.storage, data);
        }),

      setSmtp: (data) =>
        set((state) => {
          Object.assign(state.smtp, data);
        }),

      setSubmitting: (submitting) =>
        set((state) => {
          state.submitting = submitting;
        }),

      setSubmitStatus: (status) =>
        set((state) => {
          state.submitStatus = status;
        }),

      setOnboardingActive: (active) =>
        set((state) => {
          state.isOnboardingActive = active;
        }),

      reset: () =>
        set(() => ({ ...initialState })),
    })),
    { name: 'onboarding-store' }
  )
);

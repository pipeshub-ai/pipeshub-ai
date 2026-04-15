'use client';

import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';
import type { TourState, TourStepId } from './types';
import { markTourStepComplete, dismissTour as dismissTourApi } from './api';

// ===============================
// Store Interface
// ===============================

interface OnboardingTourState {
  /** Whether the tour card is currently shown */
  isVisible: boolean;
  /** Full tour state returned by the API — null until first hydration */
  tourState: TourState | null;

  // ── Actions ──────────────────────────────────────
  showTour: () => void;
  /** Mark a step complete via API; updates tourState with the response */
  completeStep: (stepId: TourStepId) => Promise<void>;
  /** Dismiss the tour card permanently */
  dismissTour: () => Promise<void>;
  /** Seed state from the server response (called once on mount) */
  hydrateTour: (tourState: TourState) => void;
}

// ===============================
// Store
// ===============================

export const useOnboardingTourStore = create<OnboardingTourState>()(
  devtools(
    persist(
      immer((set) => ({
        isVisible: false,
        tourState: null,

        showTour: () =>
          set((s) => {
            s.isVisible = true;
          }),

        completeStep: async (stepId) => {
          try {
            const nextState = await markTourStepComplete(stepId);
            set((s) => {
              s.tourState = nextState;
              // Card stays visible; user dismisses manually via close button
            });
          } catch {
            // Keep current state on failure — no rollback needed
          }
        },

        dismissTour: async () => {
          // Hide immediately for responsiveness, then inform the backend.
          // On next page load the API will return status: 'hidden' so the card stays hidden.
          set((s) => {
            s.isVisible = false;
          });
          try {
            await dismissTourApi();
          } catch {
            // Dismissed locally even if API fails
          }
        },

        hydrateTour: (tourState) =>
          set((s) => {
            s.tourState = tourState;
            // 'hidden' means the user has already dismissed — keep the card hidden
            s.isVisible = tourState.status !== 'hidden';
          }),
      })),
      {
        name: 'onboarding-tour',
        // Only persist visibility for this session — dismissal truth lives on the server
        partialize: (s) => ({
          isVisible: s.isVisible,
        }),
      }
    ),
    { name: 'OnboardingTourStore' }
  )
);

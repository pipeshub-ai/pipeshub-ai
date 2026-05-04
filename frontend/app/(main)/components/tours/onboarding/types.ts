// ===============================
// Onboarding Tour Types
// ===============================

export type TourStepId = 'step1' | 'step2' | 'step3';

export interface TourStepDetail {
  title: string;
  relativeLink: string;
}

export interface TourStateActive {
  status: 'active';
  currentStep: TourStepId;
  /** 0–100 — used directly as the pixel loader fill percentage */
  completionPercentage: number;
  title: string;
  subtitle: string;
  stepsOrder: TourStepId[];
  stepsDetails: Partial<Record<TourStepId, TourStepDetail>>;
}

export interface TourStateCompleted {
  status: 'completed';
  title: string;
  subtitle: string;
}

/** Backend sends this after the user dismisses the tour — card should not be shown */
export interface TourStateHidden {
  status: 'hidden';
}

export type TourState = TourStateActive | TourStateCompleted | TourStateHidden;

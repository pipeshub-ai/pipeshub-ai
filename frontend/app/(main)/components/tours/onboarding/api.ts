/**
 * Onboarding Tour API
 *
 * Stubs — return the correct TourState shape until the backend endpoints are ready.
 * Replace the stub bodies with real apiClient calls when the API is available.
 *
 * Real contract:
 *   GET  /api/v1/tour/onboarding          → TourState
 *   POST /api/v1/tour/onboarding/steps    { stepId } → TourState (next state)
 *   POST /api/v1/tour/onboarding/dismiss  → void
 */

import type { TourState, TourStepId } from './types';

// ===============================
// Stub state definitions
// ===============================

const STEP1_STATE: TourState = {
  status: 'active',
  currentStep: 'step1',
  completionPercentage: 0,
  title: "You're Almost There!",
  subtitle: 'Finish the following actions to know what Pipeshub can do',
  stepsOrder: ['step1', 'step2', 'step3'],
  stepsDetails: {
    step1: { title: 'Connect your first app', relativeLink: '/workspace/connectors' },
    step2: { title: 'Create a collection', relativeLink: '/knowledge-base' },
    step3: { title: 'Invite Team Members', relativeLink: '/workspace/users/?panel=invite' },
  },
};

const STEP2_STATE: TourState = {
  status: 'active',
  currentStep: 'step2',
  completionPercentage: 33,
  title: "You're Almost There!",
  subtitle: 'Finish the following actions to know what Pipeshub can do',
  stepsOrder: ['step2', 'step3'],
  stepsDetails: {
    step2: { title: 'Create a collection', relativeLink: '/knowledge-base' },
    step3: { title: 'Invite Team Members', relativeLink: '/workspace/users/?panel=invite' },
  },
};

const STEP3_STATE: TourState = {
  status: 'active',
  currentStep: 'step3',
  completionPercentage: 66,
  title: 'Keep going!',
  subtitle: 'How about sharing the word with your team members?',
  stepsOrder: ['step3'],
  stepsDetails: {
    step3: { title: 'Invite Team Members', relativeLink: '/workspace/users/?panel=invite' },
  },
};

const COMPLETED_STATE: TourState = {
  status: 'completed',
  title: "Let's go!",
  subtitle:
    "You're all set now. If you want to know more check out documentation.",
};

/** Maps a completed stepId to the state that follows it */
const NEXT_STATE: Record<TourStepId, TourState> = {
  step1: STEP2_STATE,
  step2: STEP3_STATE,
  step3: COMPLETED_STATE,
};

// ===============================
// GET tour status
// ===============================

/**
 * Fetch the current tour state for the user.
 * Stub: always starts at step1 (new user).
 */
export async function getTourStatus(): Promise<TourState> {
  // TODO: replace with apiClient.get<TourState>('/api/v1/tour/onboarding')
  return Promise.resolve(STEP1_STATE);
}

// ===============================
// MARK step complete
// ===============================

/**
 * Register a tour step as completed.
 * Returns the new TourState that the client should display.
 * Stub: advances through the predefined state machine.
 */
export async function markTourStepComplete(stepId: TourStepId): Promise<TourState> {
  // TODO: replace with:
  //   const { data } = await apiClient.post<TourState>('/api/v1/tour/onboarding/steps', { stepId });
  //   return data;
  return Promise.resolve(NEXT_STATE[stepId]);
}

// ===============================
// DISMISS the tour
// ===============================

/**
 * Dismiss the tour card permanently.
 * Stub: always resolves.
 */
export async function dismissTour(): Promise<void> {
  // TODO: replace with:
  //   await apiClient.post('/api/v1/tour/onboarding/dismiss');
  return Promise.resolve();
}

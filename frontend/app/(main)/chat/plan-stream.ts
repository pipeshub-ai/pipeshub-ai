'use client';

import type { StreamMessageCallbacks } from './api';
import { useChatStore } from './store';

/** Keeps the slot's plan at the root run's latest `plan` frame (UXR-B1); a run that never plans leaves none. */
export function planStreamCallbacks(
  slotId: string,
  isCurrent: () => boolean = () => true,
): Pick<StreamMessageCallbacks, 'onPlan'> {
  return {
    onPlan: (plan) => {
      if (isCurrent()) useChatStore.getState().updateSlot(slotId, { plan });
    },
  };
}

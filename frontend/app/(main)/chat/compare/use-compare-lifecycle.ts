'use client';

import { useEffect } from 'react';
import { useFeatureFlagsStore, selectChatCompareEnabled } from '@/lib/store/feature-flags-store';
import { useChatStore } from '../store';
import { restoreCompare } from './compare-controller';

/**
 * Brings back the two-column view of a reloaded conversation.
 */
export function useCompareLifecycle(activeSlotId: string | null): void {
  const flagOn = useFeatureFlagsStore(selectChatCompareEnabled);
  const ready = useChatStore((s) => {
    const slot = activeSlotId ? s.slots[activeSlotId] : undefined;
    return Boolean(slot?.convId && !slot.isTemp && slot.isInitialized && !slot.isStreaming);
  });

  useEffect(() => {
    if (flagOn && activeSlotId && ready) void restoreCompare(activeSlotId);
  }, [flagOn, activeSlotId, ready]);
}

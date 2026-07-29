'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

const STORAGE_KEY = 'pipeshub-browser-notifications';
const DEFAULT_SNOOZE_DAYS = 1;

type PromptDecision = 'ask' | 'dismissed' | 'snoozed';

interface BrowserNotificationsState {
  desktopEnabled: boolean;
  promptDecision: PromptDecision;
  snoozedUntil: number | null;
  setDesktopEnabled: (enabled: boolean) => void;
  dismissPrompt: () => void;
  snoozePrompt: (days?: number) => void;
}

export const useBrowserNotificationsStore = create<BrowserNotificationsState>()(
  persist(
    (set) => ({
      desktopEnabled: false,
      promptDecision: 'ask',
      snoozedUntil: null,
      setDesktopEnabled: (enabled) => set({ desktopEnabled: enabled }),
      dismissPrompt: () =>
        set({ promptDecision: 'dismissed', snoozedUntil: null }),
      snoozePrompt: (days = DEFAULT_SNOOZE_DAYS) =>
        set({
          promptDecision: 'snoozed',
          snoozedUntil: Date.now() + days * 24 * 60 * 60 * 1000,
        }),
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        desktopEnabled: state.desktopEnabled,
        promptDecision: state.promptDecision,
        snoozedUntil: state.snoozedUntil,
      }),
    },
  ),
);

export function shouldShowDesktopNotificationPrompt(): boolean {
  const { promptDecision, snoozedUntil } = useBrowserNotificationsStore.getState();
  if (promptDecision === 'dismissed') return false;
  if (promptDecision === 'snoozed' && snoozedUntil != null && Date.now() < snoozedUntil) {
    return false;
  }
  return true;
}

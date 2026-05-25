'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export const PANEL_DEFAULT_WIDTH = 360;
export const PANEL_MIN_WIDTH = 280;
export const PANEL_MAX_WIDTH = 520;

interface NotificationPanelWidthState {
  panelWidth: number;
  setPanelWidth: (width: number) => void;
}

export const useNotificationPanelWidthStore = create<NotificationPanelWidthState>()(
  persist(
    (set) => ({
      panelWidth: PANEL_DEFAULT_WIDTH,
      setPanelWidth: (width) =>
        set({
          panelWidth: Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, width)),
        }),
    }),
    {
      name: 'pipeshub-notification-panel-width',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ panelWidth: state.panelWidth }),
    },
  ),
);

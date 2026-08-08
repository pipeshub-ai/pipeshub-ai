'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { immer } from 'zustand/middleware/immer';

interface OrgState {
  isInitialized: boolean;
}

interface OrgActions {
  setInitialized: (val: boolean) => void;
  clearOrg: () => void;
}

export type OrgStore = OrgState & OrgActions;

const initialState: OrgState = {
  isInitialized: false,
};

export const useOrgStore = create<OrgStore>()(
  devtools(
    immer((set) => ({
      ...initialState,

      setInitialized: (val) =>
        set((state) => {
          state.isInitialized = val;
        }),

      clearOrg: () => set(() => ({ ...initialState })),
    })),
    { name: 'OrgStore' }
  )
);

export const selectIsOrgInitialized = (s: OrgStore) => s.isInitialized;

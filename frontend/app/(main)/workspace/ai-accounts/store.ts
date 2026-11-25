'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { ModelAccountsApi, toAccountsError, isAccountsError } from './api';
import type { Load } from './components/load-state';
import type { AccountsError, ModelAccessView } from './types';

const failed = (error: unknown): { status: 'error'; error: AccountsError } => ({
  status: 'error',
  error: isAccountsError(error) ? error : toAccountsError(error),
});

interface MyAccountsState {
  access: Load<ModelAccessView>;
  loadAccess: () => Promise<void>;
  reset: () => void;
}

const initial = {
  access: { status: 'loading' } as Load<ModelAccessView>,
};

/** "My AI accounts": what the org lets the person use. */
export const useMyAccountsStore = create<MyAccountsState>()(
  devtools(
    (set) => ({
      ...initial,

      loadAccess: async () => {
        set({ access: { status: 'loading' } });
        try {
          set({ access: { status: 'ready', data: await ModelAccountsApi.access() } });
        } catch (error) {
          set({ access: failed(error) });
        }
      },

      reset: () => set(initial),
    }),
    { name: 'my-ai-accounts' },
  ),
);

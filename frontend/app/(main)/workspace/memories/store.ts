'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { Load } from '../ai-accounts/components/load-state';
import { isHarnessError, toHarnessError } from '../harness-ui/refusal';
import { MemoriesApi, type Memory, type MemoryFilter } from './api';

const failed = (error: unknown) =>
  ({ status: 'error', error: isHarnessError(error) ? error : toHarnessError(error) }) as const;

interface MemoriesState {
  filter: MemoryFilter;
  own: Load<Memory[]>;
  loadOwn: (filter?: MemoryFilter, quiet?: boolean) => Promise<void>;
  /** Rejects with a `HarnessError`; the list it touched is re-read. */
  editOwn: (memoryId: string, content: string) => Promise<void>;
  reviewOwn: (memoryId: string, decision: 'accepted' | 'rejected') => Promise<void>;
  removeOwn: (memoryId: string) => Promise<void>;
  /** Whether the person's runs use and keep memories. */
  remember: Load<boolean>;
  loadRemember: () => Promise<void>;
  /** Rejects with a `HarnessError`; the switch keeps what the server last said. */
  setRemember: (remember: boolean) => Promise<void>;
  reset: () => void;
}

const initial = {
  filter: 'all' as MemoryFilter,
  own: { status: 'loading' } as Load<Memory[]>,
  remember: { status: 'loading' } as Load<boolean>,
};

export const useMemoriesStore = create<MemoriesState>()(
  devtools(
    (set, get) => ({
      ...initial,

      loadOwn: async (filter = get().filter, quiet = false) => {
        set({ filter, ...(quiet ? {} : { own: { status: 'loading' } }) });
        try {
          set({ own: { status: 'ready', data: await MemoriesApi.listOwn(filter) } });
        } catch (error) {
          set({ own: failed(error) });
        }
      },

      editOwn: async (memoryId, content) => {
        await MemoriesApi.edit(memoryId, content);
        await get().loadOwn(undefined, true);
      },

      reviewOwn: async (memoryId, decision) => {
        await MemoriesApi.review(memoryId, decision);
        await get().loadOwn(undefined, true);
      },

      removeOwn: async (memoryId) => {
        await MemoriesApi.remove(memoryId);
        await get().loadOwn(undefined, true);
      },

      loadRemember: async () => {
        try {
          set({ remember: { status: 'ready', data: await MemoriesApi.remember() } });
        } catch (error) {
          set({ remember: failed(error) });
        }
      },

      setRemember: async (remember) => {
        set({ remember: { status: 'ready', data: await MemoriesApi.setRemember(remember) } });
      },

      reset: () => set(initial),
    }),
    { name: 'agent-memories' },
  ),
);

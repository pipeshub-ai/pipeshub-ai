'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { Load } from '../ai-accounts/components/load-state';
import { isHarnessError, toHarnessError } from '../harness-ui/refusal';
import { SchedulesApi } from './api';
import type { AgentVersionPin, CreatedSchedule, NewSchedule, Schedule, ScheduleScope } from './types';

const failed = (error: unknown) =>
  ({ status: 'error', error: isHarnessError(error) ? error : toHarnessError(error) }) as const;

interface SchedulesState {
  mine: Load<Schedule[]>;
  load: (scope: ScheduleScope, quiet?: boolean) => Promise<void>;
  /** Rejects with a `HarnessError`; the list is re-read on success. */
  create: (input: NewSchedule) => Promise<CreatedSchedule>;
  act: (scope: ScheduleScope, id: string, action: 'pause' | 'resume' | 'remove') => Promise<void>;
  /** Rejects with a `HarnessError`; the list is re-read on success. */
  pin: (scope: ScheduleScope, id: string, agentVersion: AgentVersionPin) => Promise<void>;
  reset: () => void;
}

const initial = {
  mine: { status: 'loading' } as Load<Schedule[]>,
};

export const useSchedulesStore = create<SchedulesState>()(
  devtools(
    (set, get) => ({
      ...initial,

      load: async (scope, quiet = false) => {
        if (!quiet) set({ [scope]: { status: 'loading' } });
        try {
          set({ [scope]: { status: 'ready', data: await SchedulesApi.list(scope) } });
        } catch (error) {
          set({ [scope]: failed(error) });
        }
      },

      create: async (input) => {
        const created = await SchedulesApi.create(input);
        await get().load('mine', true);
        return created;
      },

      act: async (scope, id, action) => {
        if (action === 'remove') await SchedulesApi.remove(id);
        else await SchedulesApi[action](id);
        await get().load(scope, true);
      },

      pin: async (scope, id, agentVersion) => {
        await SchedulesApi.setAgentVersion(id, agentVersion);
        await get().load(scope, true);
      },

      reset: () => set(initial),
    }),
    { name: 'agent-schedules' },
  ),
);

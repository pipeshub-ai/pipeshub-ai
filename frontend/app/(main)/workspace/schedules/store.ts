'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import type { Load } from '../ai-accounts/components/load-state';
import { isHarnessError, toHarnessError } from '../harness-ui/refusal';
import { SchedulesApi } from './api';
import type { AgentVersionPin, CreatedSchedule, NewSchedule, Schedule, SchedulePatch, ScheduleScope } from './types';

const failed = (error: unknown) =>
  ({ status: 'error', error: isHarnessError(error) ? error : toHarnessError(error) }) as const;

interface SchedulesState {
  mine: Load<Schedule[]>;
  load: (scope: ScheduleScope, quiet?: boolean) => Promise<void>;
  /** Rejects with a `HarnessError`; the list is re-read on success. */
  create: (input: NewSchedule) => Promise<CreatedSchedule>;
  /** Rejects with a `HarnessError`; the list is re-read on success. */
  update: (id: string, patch: SchedulePatch) => Promise<Schedule>;
  /** The person's schedule as the server has it now, re-read with the list; null once it is gone. */
  reread: (id: string) => Promise<Schedule | null>;
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

      update: async (id, patch) => {
        const updated = await SchedulesApi.update(id, patch);
        await get().load('mine', true);
        return updated;
      },

      reread: async (id) => {
        await get().load('mine', true);
        const mine = get().mine;
        return mine.status === 'ready' ? (mine.data.find((schedule) => schedule.id === id) ?? null) : null;
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

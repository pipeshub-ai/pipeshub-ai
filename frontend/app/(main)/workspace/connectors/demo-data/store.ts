'use client';

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import { ConnectorsApi } from '../api';
import { KnowledgeHubApi } from '@/app/(main)/knowledge-base/api';
import { DemoDataApi, type DemoDataStatus } from './api';
import type { Connector, ConnectorScope } from '../types';
import { demoConnectorsIn, hasActiveDemo, hasIndexedRecords, otherConnectorsIn } from './demo-data';

interface DemoDataState {
  /** Demo connector instances (the Acme Corp sample company), enabled or not; empty until found. */
  demoConnectors: Connector[];
  /**
   * Whether some other connector already has indexed records, which is when
   * answers start mixing Acme Corp with the company's own data. `null` until checked.
   */
  realDataIndexed: boolean | null;
  /** This person's demo data switch; `null` until read. */
  status: DemoDataStatus | null;
  loadDemoConnectors: () => Promise<void>;
  checkRealData: () => Promise<void>;
  loadStatus: () => Promise<void>;
  /** Show or hide the demo for this person; `null` goes back to the default. */
  setInclude: (include: boolean | null) => Promise<void>;
  /** Admins only: turn the demo off, or back on, for everyone. */
  setEnabledForEveryone: (enabled: boolean) => Promise<void>;
  /** Forget what was found, e.g. once the demo data has been removed. */
  reset: () => void;
}

// One lookup at a time, however many components ask.
let demoLookup: Promise<void> | null = null;
let realDataLookup: Promise<void> | null = null;
let statusLookup: Promise<void> | null = null;
// Bumped by every change of the switch, so a status read that started before
// it cannot overwrite the answer to the change.
let statusRevision = 0;
// Bumped by reset(), so an answer to a lookup started before it is dropped
// instead of bringing back a demo that has just been removed.
let generation = 0;

/**
 * Whether the user can see an indexed record uploaded to a Collection. The
 * connector list never includes Collections, so uploads need their own check.
 */
async function hasIndexedCollectionRecord(): Promise<boolean> {
  try {
    const res = await KnowledgeHubApi.searchAllRecords({
      origins: 'COLLECTION',
      nodeTypes: 'record',
      indexingStatus: 'COMPLETED',
      flattened: true,
      limit: 1,
      include: undefined,
    });
    return (res.items ?? []).length > 0;
  } catch {
    return false;
  }
}

async function connectorsIn(scope: ConnectorScope): Promise<Connector[]> {
  try {
    return (await ConnectorsApi.getActiveConnectors(scope)).connectors ?? [];
  } catch {
    return [];
  }
}

export const useDemoDataStore = create<DemoDataState>()(
  devtools(
    (set, get) => ({
      demoConnectors: [],
      realDataIndexed: null,
      status: null,

      // Only a positive answer is remembered. Chat is the landing page, so the
      // first lookup of a session usually runs before anyone has turned the
      // demo on; remembering that "no", or a failed lookup, would hide the demo
      // for the rest of the session. A disabled demo is asked about again too,
      // since it may be turned back on.
      loadDemoConnectors: () => {
        if (hasActiveDemo(get().demoConnectors)) return Promise.resolve();
        if (demoLookup) return demoLookup;
        const started = generation;
        const lookup: Promise<void> = ConnectorsApi.getActiveConnectors('team')
          .then((res) => {
            if (started === generation) set({ demoConnectors: demoConnectorsIn(res.connectors ?? []) });
          })
          .catch(() => undefined)
          .finally(() => {
            if (demoLookup === lookup) demoLookup = null;
          });
        demoLookup = lookup;
        return lookup;
      },

      // Same rule: once real data is there it stays there, while "none yet" is
      // asked again next time.
      checkRealData: () => {
        if (get().realDataIndexed === true) return Promise.resolve();
        if (realDataLookup) return realDataLookup;
        const started = generation;
        const settle = (found: boolean) => {
          if (started === generation) set({ realDataIndexed: found });
        };
        const lookup: Promise<void> = (async () => {
          if (await hasIndexedCollectionRecord()) {
            settle(true);
            return;
          }
          const candidates = otherConnectorsIn([
            ...(await connectorsIn('team')),
            ...(await connectorsIn('personal')),
          ]);
          for (const connector of candidates) {
            try {
              const stats = await ConnectorsApi.getConnectorStats(connector._key as string);
              if (hasIndexedRecords(stats.data)) {
                settle(true);
                return;
              }
            } catch {
              // One connector's stats failing says nothing about the others.
            }
          }
          settle(false);
        })().finally(() => {
          if (realDataLookup === lookup) realDataLookup = null;
        });
        realDataLookup = lookup;
        return lookup;
      },

      loadStatus: () => {
        if (statusLookup) return statusLookup;
        const started = generation;
        const revision = statusRevision;
        const lookup: Promise<void> = DemoDataApi.getStatus()
          .then((status) => {
            if (started === generation && revision === statusRevision) set({ status });
          })
          .catch(() => undefined)
          .finally(() => {
            if (statusLookup === lookup) statusLookup = null;
          });
        statusLookup = lookup;
        return lookup;
      },

      // Not swallowed: the caller shows the failure, and the switch stays where it was.
      setInclude: async (include) => {
        const started = generation;
        statusRevision += 1;
        const status = await DemoDataApi.setInclude(include);
        if (started === generation) set({ status });
      },

      setEnabledForEveryone: async (enabled) => {
        const started = generation;
        statusRevision += 1;
        const status = await DemoDataApi.setEnabledForEveryone(enabled);
        if (started === generation) set({ status });
      },

      reset: () => {
        generation += 1;
        demoLookup = null;
        realDataLookup = null;
        statusLookup = null;
        set({ demoConnectors: [], realDataIndexed: null, status: null });
      },
    }),
    { name: 'demo-data-store' },
  ),
);

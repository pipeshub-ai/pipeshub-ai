'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import type { Load } from '../ai-accounts/components/load-state';
import { ControlPlaneApi, isCpError, savedOver, toCpError, type CpError, type PolicySection, type PolicySectionName } from './control-plane-api';
import type { Json } from './pick';

/** A failure as a section editor shows it; a stale revision is a conflict whichever of 409 and 412 the server answers. */
export function sectionError(caught: unknown, write = false): CpError {
  const error = isCpError(caught) ? caught : toCpError(caught, write);
  return write && (error.status === 412 || error.code === 'CP_REVISION_CONFLICT') ? { ...error, kind: 'conflict' } : error;
}

export type SectionLoad = Load<PolicySection> | { status: 'off' };

/**
 * One policy section an editor saves a change at a time (WP-63): read, then each save is checked against the
 * revision read (`If-Match`). A refusal is thrown as a `CpError` for the caller to place under its field; a
 * conflict keeps the editor's change so it can be applied again on the newer revision.
 */
export function usePolicySection(section: PolicySectionName, enabled: boolean) {
  const [load, setLoadState] = useState<SectionLoad>({ status: 'loading' });
  // A save right after a reload (a conflict applied again) must use the revision just read, not the last render's.
  const latest = useRef<PolicySection | null>(null);
  const setLoad = useCallback((next: SectionLoad) => {
    latest.current = next.status === 'ready' ? next.data : null;
    setLoadState(next);
  }, []);

  const read = useCallback(
    async (quiet = false): Promise<PolicySection | null> => {
      if (!quiet) setLoad({ status: 'loading' });
      try {
        const fresh = await ControlPlaneApi.policy(section);
        setLoad({ status: 'ready', data: fresh });
        return fresh;
      } catch (caught) {
        const error = sectionError(caught);
        setLoad(error.off ? { status: 'off' } : { status: 'error', error });
        return null;
      }
    },
    [section, setLoad],
  );

  useEffect(() => {
    if (enabled) void read();
  }, [enabled, read]);

  const current = load.status === 'ready' ? load.data : null;

  /** Saves `change(current value)` on the revision read; resolves to the saved section. */
  const save = useCallback(
    async (change: (value: Json | null) => Json | null): Promise<PolicySection> => {
      const base = latest.current;
      if (base === null) throw sectionError(new Error('not loaded'));
      try {
        const after = savedOver(base, await ControlPlaneApi.savePolicy(section, change(base.value), base.revision));
        setLoad({ status: 'ready', data: after });
        return after;
      } catch (caught) {
        throw sectionError(caught, true);
      }
    },
    [section, setLoad],
  );

  /** Restores an older revision (the org's own section), on the revision read. */
  const restore = useCallback(
    async (toRevision: number): Promise<PolicySection> => {
      const base = latest.current;
      if (base === null) throw sectionError(new Error('not loaded'));
      try {
        const after = savedOver(base, await ControlPlaneApi.rollback(section, toRevision, base.revision));
        setLoad({ status: 'ready', data: after });
        return after;
      } catch (caught) {
        throw sectionError(caught, true);
      }
    },
    [section, setLoad],
  );

  return { load, current, read, save, restore };
}

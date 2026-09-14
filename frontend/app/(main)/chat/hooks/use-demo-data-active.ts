'use client';

import { useEffect, useState } from 'react';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';

/** Connector type registered by the bundled Acme Corp sample-data connector. */
export const DEMO_CONNECTOR_TYPE = 'Demo';

let cached: boolean | null = null;

/**
 * True when the org has an active Demo connector (the Acme Corp sample company),
 * so the new-chat landing can offer its golden questions. Looked up once per
 * page load; a failed lookup just means no demo hints.
 */
export function useDemoDataActive(): boolean {
  const [active, setActive] = useState<boolean>(cached ?? false);

  useEffect(() => {
    if (cached !== null) return;
    let cancelled = false;
    ConnectorsApi.getActiveConnectors('team')
      .then((res) => {
        const found = (res.connectors ?? []).some(
          (c) => c.type === DEMO_CONNECTOR_TYPE && c.isActive
        );
        cached = found;
        if (!cancelled) setActive(found);
      })
      .catch(() => {
        cached = false;
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return active;
}

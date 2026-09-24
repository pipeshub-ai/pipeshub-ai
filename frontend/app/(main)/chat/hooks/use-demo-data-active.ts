'use client';

import { useEffect, useState } from 'react';
import { ConnectorsApi } from '@/app/(main)/workspace/connectors/api';

/** Connector type registered by the bundled Acme Corp sample-data connector. */
export const DEMO_CONNECTOR_TYPE = 'Demo';

/**
 * Only a positive answer is remembered. Chat is the landing page, so the first
 * lookup of a session usually happens before anyone has turned the demo
 * connector on; remembering that "no", or a lookup that failed, would hide the
 * golden questions for the rest of the session.
 */
let cachedActive = false;

/**
 * True when the org has an active Demo connector (the Acme Corp sample company),
 * so the new-chat landing can offer its golden questions. Looked up again on
 * each landing until it finds one; a failed lookup just means no demo hints.
 */
export function useDemoDataActive(): boolean {
  const [active, setActive] = useState<boolean>(cachedActive);

  useEffect(() => {
    if (cachedActive) return;
    let cancelled = false;
    ConnectorsApi.getActiveConnectors('team')
      .then((res) => {
        const found = (res.connectors ?? []).some(
          (c) => c.type === DEMO_CONNECTOR_TYPE && c.isActive
        );
        if (found) cachedActive = true;
        if (!cancelled) setActive(found);
      })
      .catch(() => {
        // No hints this time, and nothing remembered, so the next landing
        // asks again.
        if (!cancelled) setActive(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return active;
}

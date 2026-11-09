'use client';

import { useEffect, useRef, useState } from 'react';
import { Box } from '@radix-ui/themes';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { ConnectorProgressLine } from '@/app/(main)/workspace/connectors/components/connector-progress-line';
import { useConnectorProgress } from '@/app/(main)/workspace/connectors/utils/use-connector-progress';

// At rest the progress hook stops polling; this catches a sync that starts while someone is looking.
const RECHECK_AT_REST_MS = 60_000;

/**
 * The connector card's status line, above that connector's records in All Records,
 * so someone browsing a source sees it syncing or being made searchable.
 * Shows nothing until it has an answer, or when the viewer may not open the connector:
 * All Records lists team connectors to members, but only admins and the creator may
 * read a team connector's progress, so a refusal here is ordinary and stays silent.
 */
export function ConnectorSyncStatus({ connectorId, source }: { connectorId: string; source: string }) {
  const isMobile = useIsMobile();
  const [recheck, setRecheck] = useState(0);
  const answered = useRef(false);
  useEffect(() => {
    const id = window.setInterval(() => {
      // Never after a refusal: only a connector that answered is worth asking again.
      if (answered.current && document.visibilityState === 'visible') setRecheck((n) => n + 1);
    }, RECHECK_AT_REST_MS);
    return () => window.clearInterval(id);
  }, []);
  const view = useConnectorProgress(connectorId, 'summary', recheck, true);
  useEffect(() => {
    answered.current = view != null;
  }, [view]);
  if (!view) return null;
  return (
    <Box style={{ padding: isMobile ? 'var(--space-1) var(--space-2)' : '4px 16px 8px' }}>
      <ConnectorProgressLine view={view} source={source} />
    </Box>
  );
}

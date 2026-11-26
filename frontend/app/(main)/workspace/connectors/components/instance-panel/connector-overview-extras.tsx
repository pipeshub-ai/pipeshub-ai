'use client';

import { IndexingPriorityNotice } from './indexing-priority-notice';

/** Extras at the bottom of a connector's overview tab. Editions plug in via config.ts. */
export function ConnectorOverviewExtras(_props: { connectorId: string | null; refreshKey: number }) {
  return <IndexingPriorityNotice />;
}

'use client';

import type { MutableRefObject } from 'react';

export interface McpManageAccessProps {
  /** The organization server being edited; null while it is being created. */
  serverId: string | null;
  disabled?: boolean;
  /**
   * An edition that saves access sets this; the panel calls it with the server's id once the
   * server itself is saved. It reports its own errors and never throws.
   */
  saveRef: MutableRefObject<((serverId: string) => Promise<void>) | null>;
}

/** Every organization server is open to everyone here, so there is nothing to manage. */
export function McpManageAccess(_props: McpManageAccessProps) {
  return null;
}

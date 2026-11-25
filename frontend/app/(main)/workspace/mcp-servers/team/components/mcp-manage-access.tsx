'use client';

import type { MutableRefObject } from 'react';

export interface McpManageAccessProps {
  /** The organization server being edited; null while it is being created. */
  serverId: string | null;
  disabled?: boolean;
  /**
   * A section that saves access sets this; the panel calls it with the server's id once the
   * server itself is saved. It rejects when access was not saved: the panel then reports it
   * and stays open, and a retry saves the same server again.
   */
  saveRef: MutableRefObject<((serverId: string) => Promise<void>) | null>;
}

/** Every organization server is open to everyone here, so there is nothing to manage. */
export function McpManageAccess(_props: McpManageAccessProps) {
  return null;
}

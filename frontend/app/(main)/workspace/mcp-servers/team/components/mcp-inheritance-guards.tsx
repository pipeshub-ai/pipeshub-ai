'use client';

import type { McpServerInstance } from '../../types';

export function isMcpInstanceReadOnly(_instance: McpServerInstance | null | undefined): boolean {
  return false;
}

/** Badges beside a server's name on its row and card (an edition's: inherited, waiting for approval). OSS: none. */
export function McpInstanceBadges(_props: { instance: McpServerInstance }): React.ReactElement | null {
  return null;
}

/** The slot's earlier name, kept for code written against it. */
export const McpInheritedBadge = McpInstanceBadges;

export function McpInheritedCallout(_props: { instance: McpServerInstance | null | undefined }): React.ReactElement | null {
  return null;
}

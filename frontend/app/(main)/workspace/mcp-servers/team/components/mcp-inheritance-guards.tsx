'use client';

import type { McpServerInstance } from '../../types';

export function isMcpInstanceReadOnly(_instance: McpServerInstance | null | undefined): boolean {
  return false;
}

export function McpInheritedBadge(_props: { instance: McpServerInstance }): React.ReactElement | null {
  return null;
}

export function McpInheritedCallout(_props: { instance: McpServerInstance | null | undefined }): React.ReactElement | null {
  return null;
}

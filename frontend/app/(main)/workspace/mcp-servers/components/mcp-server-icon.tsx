'use client';

import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface McpServerIconProps {
  typeId?: string | null;
  name: string;
  size?: number;
  /** For the MCP mark only; a logo keeps its own colours. */
  color?: string;
}

/**
 * The service's logo, found as the MCP servers page finds it: by catalog type, else by name.
 * A custom server we have no logo for keeps the MCP mark instead of a stock database icon.
 */
export function McpServerIcon({ typeId, name, size = 16, color = 'var(--gray-11)' }: McpServerIconProps) {
  const type = resolveConnectorType(typeId || name);
  if (type === 'generic') return <MaterialIcon name="hub" size={size} color={color} />;
  return <ConnectorIcon type={type} size={size} color={color} />;
}

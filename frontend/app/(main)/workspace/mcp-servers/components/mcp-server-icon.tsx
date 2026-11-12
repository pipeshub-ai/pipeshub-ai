'use client';

import { ConnectorIcon, resolveConnectorType } from '@/app/components/ui/ConnectorIcon';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useMcpServerLogo } from '../logo-store';
import { McpLogoImage } from './mcp-logo-image';

interface McpServerIconProps {
  /** A custom server's uploaded logo wins over everything else. */
  instanceId?: string | null;
  typeId?: string | null;
  name: string;
  size?: number;
  /** For the MCP mark only; a logo keeps its own colours. */
  color?: string;
}

/**
 * The server's logo: the one uploaded for it, else the service's, found as the MCP servers page
 * finds it (by catalog type, else by name). A custom server we have no logo for keeps the MCP
 * mark instead of a stock database icon.
 */
export function McpServerIcon({ instanceId, typeId, name, size = 16, color = 'var(--gray-11)' }: McpServerIconProps) {
  const logo = useMcpServerLogo(instanceId);
  if (logo) return <McpLogoImage src={logo} size={size} />;
  const type = resolveConnectorType(typeId || name);
  if (type === 'generic') return <MaterialIcon name="hub" size={size} color={color} />;
  return <ConnectorIcon type={type} size={size} color={color} />;
}

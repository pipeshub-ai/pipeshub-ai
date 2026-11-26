import type { McpCategory } from './types';

/** The order the catalog shows its categories in, with each one's icon. Names are translated
 * under `workspace.mcpServers.categories`. */
export const MCP_CATEGORIES: readonly { id: McpCategory; icon: string }[] = [
  { id: 'communication', icon: 'forum' },
  { id: 'meetings', icon: 'event' },
  { id: 'docs', icon: 'folder_open' },
  { id: 'projects', icon: 'view_kanban' },
  { id: 'developer', icon: 'code' },
  { id: 'data', icon: 'insights' },
  { id: 'sales', icon: 'handshake' },
  { id: 'support', icon: 'support_agent' },
  { id: 'finance', icon: 'payments' },
  { id: 'design', icon: 'draw' },
  { id: 'automation', icon: 'bolt' },
  { id: 'search', icon: 'travel_explore' },
  { id: 'security', icon: 'admin_panel_settings' },
];

export const categoryIcon = (id: McpCategory | null | undefined): string =>
  MCP_CATEGORIES.find((c) => c.id === id)?.icon ?? 'hub';

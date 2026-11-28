import type { McpAccessPolicy, McpAccessTool } from './api';

/** The sections the tools are shown in, in page order. `other` holds a tool this build does not know. */
export const TOOL_GROUPS = ['read', 'people', 'chat', 'add', 'viewer', 'code', 'other'] as const;
export type ToolGroup = (typeof TOOL_GROUPS)[number];

const GROUP_OF: Record<string, ToolGroup> = {
  pipeshub_sources: 'read',
  pipeshub_search: 'read',
  pipeshub_get_record_content: 'read',
  pipeshub_download_record: 'read',
  pipeshub_indexing_status: 'read',
  pipeshub_directory: 'people',
  pipeshub_entities: 'people',
  pipeshub_find_records: 'people',
  pipeshub_chat: 'chat',
  pipeshub_agents: 'chat',
  pipeshub_create_collection: 'add',
  pipeshub_upload: 'add',
  pipeshub_add_web_source: 'add',
  pipeshub_connect_source: 'add',
  pipeshub_show_citations: 'viewer',
  pipeshub_preview_record: 'viewer',
};

export function groupOf(name: string): ToolGroup {
  if (name.startsWith('pipeshub_code_')) return 'code';
  return GROUP_OF[name] ?? 'other';
}

/** What a tool can do to data, from the hints it declares; `writes` stands in for a server without hints. */
export type ToolKind = 'read' | 'write' | 'change';

export function kindOf(tool: McpAccessTool): ToolKind {
  if (!tool.hints) return tool.writes ? 'write' : 'read';
  if (tool.hints.readOnly) return 'read';
  return tool.hints.destructive ? 'change' : 'write';
}

export type BlockReason = 'mcpOff' | 'writesOff' | 'chatOff';

/** Why the server would not offer the tool under this policy, whatever its own switch says. */
export function blockReason(tool: McpAccessTool, policy: McpAccessPolicy): BlockReason | undefined {
  if (!policy.enabled) return 'mcpOff';
  if (tool.writes && !policy.allowWriteTools) return 'writesOff';
  // The server drops pipeshub_agents along with pipeshub_chat.
  if (tool.name === 'pipeshub_agents' && policy.disabledTools.includes('pipeshub_chat')) return 'chatOff';
  return undefined;
}

export function isOffered(tool: McpAccessTool, policy: McpAccessPolicy): boolean {
  return !blockReason(tool, policy) && !policy.disabledTools.includes(tool.name);
}

export type ToolFilter = 'all' | 'read' | 'writes' | 'off';

/** Whether a tool matches the search text (name, title, summary, arguments, scopes) and the filter. */
export function matchesTool(
  tool: McpAccessTool,
  summary: string | undefined,
  query: string,
  filter: ToolFilter,
  policy: McpAccessPolicy,
): boolean {
  if (filter === 'read' && kindOf(tool) !== 'read') return false;
  if (filter === 'writes' && kindOf(tool) === 'read') return false;
  if (filter === 'off' && isOffered(tool, policy)) return false;
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const text = [
    tool.name,
    tool.title,
    // The translated summary when there is one; otherwise the server's own text, so a tool this build
    // does not know is still found by what it does. Not both: the server's text is written for the model
    // and names other tools, so it would match most searches.
    summary ?? tool.description ?? '',
    ...(tool.scopes ?? []),
    ...(tool.arguments ?? []).map((a) => a.name),
  ]
    .join(' ')
    .toLowerCase();
  return text.includes(q);
}

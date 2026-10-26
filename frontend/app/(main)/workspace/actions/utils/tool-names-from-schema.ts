import { toolsetSchemaRoot } from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';
import type { McpRuleTool } from '@/app/(main)/workspace/mcp-servers/tool-rules';

/** Tool display names from registry schema `tools[]` (fallback lists use instance tools). */
export function toolNamesFromSchema(raw: unknown): string[] {
  const root = toolsetSchemaRoot(raw);
  const tools = root?.tools;
  if (!Array.isArray(tools)) return [];
  return tools
    .map((t) => String((t as { name?: string }).name || '').trim())
    .filter(Boolean);
}

/** The schema's tools as rows of the tool approvals list, each with what it does to data (`kind`,
 * worked out by the server the way the approval gate does). */
export function ruleToolsFromSchema(raw: unknown): McpRuleTool[] {
  const tools = toolsetSchemaRoot(raw)?.tools;
  if (!Array.isArray(tools)) return [];
  return tools.flatMap((tool) => {
    const { name, description, kind } = (tool ?? {}) as Record<string, unknown>;
    if (typeof name !== 'string' || !name.trim()) return [];
    return [
      {
        name,
        ...(typeof description === 'string' && description ? { description } : {}),
        ...(kind === 'read' || kind === 'write' || kind === 'destructive' ? { kind } : {}),
      },
    ];
  });
}

import { toolsetSchemaRoot } from '@/app/(main)/agents/agent-builder/components/toolset-agent-auth-helpers';

/** Tool display names from registry schema `tools[]` (fallback lists use instance tools). */
export function toolNamesFromSchema(raw: unknown): string[] {
  const root = toolsetSchemaRoot(raw);
  const tools = root?.tools;
  if (!Array.isArray(tools)) return [];
  return tools
    .map((t) => String((t as { name?: string }).name || '').trim())
    .filter(Boolean);
}

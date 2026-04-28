/**
 * Derives a namespaced MCP tool name in the format `mcp_{serverType}_{toolName}`.
 *
 * The serverType is normalized to lowercase with all non-alphanumeric characters
 * replaced by underscores, matching the Python `normalize_mcp_server_name()` function.
 *
 * If an existing `namespacedName` is provided and within the length limit, it is
 * returned unchanged to preserve backward compatibility with stored agents.
 */

const MAX_TOOL_NAME_LENGTH = 64;

export function normalizeServerType(serverType: string): string {
  return serverType.toLowerCase().replace(/[^a-z0-9]/g, '_');
}

export function mcpNamespacedName(
  serverType: string | undefined,
  serverName: string,
  toolName: string,
  existingNamespacedName?: string,
): string {
  if (existingNamespacedName && existingNamespacedName.length <= MAX_TOOL_NAME_LENGTH) {
    return existingNamespacedName;
  }
  const key = normalizeServerType(serverType || serverName);
  return `mcp_${key}_${toolName}`;
}

/**
 * The least an MCP client needs to search and chat, with or without an agent: ticked when the
 * OAuth app form opens, and on the consent page. Search needs semantic:write, not read: the
 * Python route it forwards to requires write.
 */
export const DEFAULT_SELECTED_SCOPES = [
  'offline_access',
  'kb:read',
  'connector:read',
  'semantic:write',
  'conversation:chat',
  'agent:read',
  'agent:execute',
] as const;

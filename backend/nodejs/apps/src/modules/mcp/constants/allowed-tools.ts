// SDK upgrades must not expand the public tool surface without review.
// The tools @pipeshub-ai/mcp 4.10.0 registers; each is also gated by the token's scopes.
export const PUBLIC_MCP_TOOLS = Object.freeze([
  'pipeshub_sources',
  'pipeshub_chat',
  'pipeshub_search',
  'pipeshub_download_record',
  'pipeshub_get_record_content',
  'pipeshub_directory',
  'pipeshub_agents',
  'pipeshub_add_web_source',
  'pipeshub_connect_source',
  'pipeshub_create_collection',
  'pipeshub_indexing_status',
  'pipeshub_preview_record',
  'pipeshub_show_citations',
  'pipeshub_upload',
] as const);

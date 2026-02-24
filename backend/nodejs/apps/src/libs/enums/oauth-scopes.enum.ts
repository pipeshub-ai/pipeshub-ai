export const OAuthScopeNames = Object.freeze({
  // Organization Management
  ORG_READ: 'org:read',
  ORG_WRITE: 'org:write',
  ORG_ADMIN: 'org:admin',

  // User Management
  USER_READ: 'user:read',
  USER_WRITE: 'user:write',
  USER_INVITE: 'user:invite',
  USER_DELETE: 'user:delete',

  // User Groups
  USERGROUP_READ: 'usergroup:read',
  USERGROUP_WRITE: 'usergroup:write',

  // Teams
  TEAM_READ: 'team:read',
  TEAM_WRITE: 'team:write',

  // Knowledge Base
  KB_READ: 'kb:read',
  KB_WRITE: 'kb:write',
  KB_DELETE: 'kb:delete',
  KB_UPLOAD: 'kb:upload',

  // Search
  SEARCH_QUERY: 'search:query',
  SEARCH_SEMANTIC: 'search:semantic',

  // Conversations
  CONVERSATION_READ: 'conversation:read',
  CONVERSATION_WRITE: 'conversation:write',
  CONVERSATION_CHAT: 'conversation:chat',

  // Agents
  AGENT_READ: 'agent:read',
  AGENT_WRITE: 'agent:write',
  AGENT_EXECUTE: 'agent:execute',

  // Connectors
  CONNECTOR_READ: 'connector:read',
  CONNECTOR_WRITE: 'connector:write',
  CONNECTOR_SYNC: 'connector:sync',
  CONNECTOR_DELETE: 'connector:delete',

  // Configuration
  CONFIG_READ: 'config:read',
  CONFIG_WRITE: 'config:write',

  // Storage/Documents
  DOCUMENT_READ: 'document:read',
  DOCUMENT_WRITE: 'document:write',
  DOCUMENT_DELETE: 'document:delete',

  // Crawling
  CRAWL_READ: 'crawl:read',
  CRAWL_WRITE: 'crawl:write',
  CRAWL_DELETE: 'crawl:delete',

  // OpenID Connect / Identity
  OPENID: 'openid',
  PROFILE: 'profile',
  EMAIL: 'email',
  OFFLINE_ACCESS: 'offline_access',
} as const);

export type OAuthScopeNames =
  (typeof OAuthScopeNames)[keyof typeof OAuthScopeNames];

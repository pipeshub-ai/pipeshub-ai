export interface ScopeDefinition {
  name: string
  description: string
  category: string
  endpoints: string[]
  requiresUserConsent: boolean
}

export const OAuthScopes: Record<string, ScopeDefinition> = {
  // Organization Management
  'org:read': {
    name: 'org:read',
    description: 'Read organization information',
    category: 'Organization',
    endpoints: ['GET /api/v1/org'],
    requiresUserConsent: true,
  },
  'org:write': {
    name: 'org:write',
    description: 'Update organization settings',
    category: 'Organization',
    endpoints: ['PUT /api/v1/org', 'PATCH /api/v1/org/*'],
    requiresUserConsent: true,
  },
  'org:admin': {
    name: 'org:admin',
    description: 'Full organization administration',
    category: 'Organization',
    endpoints: ['DELETE /api/v1/org/*'],
    requiresUserConsent: true,
  },

  // User Management
  'user:read': {
    name: 'user:read',
    description: 'Read user profiles',
    category: 'Users',
    endpoints: ['GET /api/v1/users', 'GET /api/v1/users/:id'],
    requiresUserConsent: true,
  },
  'user:write': {
    name: 'user:write',
    description: 'Update user profiles',
    category: 'Users',
    endpoints: ['PUT /api/v1/users/:id', 'PATCH /api/v1/users/:id/*'],
    requiresUserConsent: true,
  },
  'user:invite': {
    name: 'user:invite',
    description: 'Invite new users to organization',
    category: 'Users',
    endpoints: ['POST /api/v1/users', 'POST /api/v1/users/bulk/invite'],
    requiresUserConsent: true,
  },
  'user:delete': {
    name: 'user:delete',
    description: 'Delete users from organization',
    category: 'Users',
    endpoints: ['DELETE /api/v1/users/:id'],
    requiresUserConsent: true,
  },

  // User Groups
  'usergroup:read': {
    name: 'usergroup:read',
    description: 'Read user groups',
    category: 'User Groups',
    endpoints: ['GET /api/v1/userGroups'],
    requiresUserConsent: true,
  },
  'usergroup:write': {
    name: 'usergroup:write',
    description: 'Create and manage user groups',
    category: 'User Groups',
    endpoints: ['POST /api/v1/userGroups', 'PUT /api/v1/userGroups/:id'],
    requiresUserConsent: true,
  },

  // Teams
  'team:read': {
    name: 'team:read',
    description: 'Read team information',
    category: 'Teams',
    endpoints: ['GET /api/v1/teams'],
    requiresUserConsent: true,
  },
  'team:write': {
    name: 'team:write',
    description: 'Create and manage teams',
    category: 'Teams',
    endpoints: ['POST /api/v1/teams', 'PUT /api/v1/teams/:id'],
    requiresUserConsent: true,
  },

  // Knowledge Base
  'kb:read': {
    name: 'kb:read',
    description: 'Read knowledge bases and records',
    category: 'Knowledge Base',
    endpoints: ['GET /api/v1/knowledgeBase/*'],
    requiresUserConsent: true,
  },
  'kb:write': {
    name: 'kb:write',
    description: 'Create and update knowledge bases',
    category: 'Knowledge Base',
    endpoints: ['POST /api/v1/knowledgeBase', 'PUT /api/v1/knowledgeBase/:id'],
    requiresUserConsent: true,
  },
  'kb:delete': {
    name: 'kb:delete',
    description: 'Delete knowledge bases and records',
    category: 'Knowledge Base',
    endpoints: ['DELETE /api/v1/knowledgeBase/*'],
    requiresUserConsent: true,
  },
  'kb:upload': {
    name: 'kb:upload',
    description: 'Upload files to knowledge bases',
    category: 'Knowledge Base',
    endpoints: ['POST /api/v1/knowledgeBase/*/upload'],
    requiresUserConsent: true,
  },

  // Search
  'search:query': {
    name: 'search:query',
    description: 'Execute search queries',
    category: 'Search',
    endpoints: ['POST /api/v1/search'],
    requiresUserConsent: true,
  },
  'search:semantic': {
    name: 'search:semantic',
    description: 'Execute semantic search',
    category: 'Search',
    endpoints: ['POST /api/v1/search/semantic'],
    requiresUserConsent: true,
  },

  // Conversations
  'conversation:read': {
    name: 'conversation:read',
    description: 'Read conversations',
    category: 'Conversations',
    endpoints: ['GET /api/v1/conversations', 'GET /api/v1/conversations/show/archives'],
    requiresUserConsent: true,
  },
  'conversation:write': {
    name: 'conversation:write',
    description: 'Create and manage conversations',
    category: 'Conversations',
    endpoints: ['POST /api/v1/conversations/create', 'PUT /api/v1/conversations/*'],
    requiresUserConsent: true,
  },
  'conversation:chat': {
    name: 'conversation:chat',
    description: 'Send messages in conversations',
    category: 'Conversations',
    endpoints: ['POST /api/v1/conversations/*/chat'],
    requiresUserConsent: true,
  },

  // Agents
  'agent:read': {
    name: 'agent:read',
    description: 'Read AI agents',
    category: 'Agents',
    endpoints: ['GET /api/v1/agents'],
    requiresUserConsent: true,
  },
  'agent:write': {
    name: 'agent:write',
    description: 'Create and manage AI agents',
    category: 'Agents',
    endpoints: ['POST /api/v1/agents', 'PUT /api/v1/agents/*'],
    requiresUserConsent: true,
  },
  'agent:execute': {
    name: 'agent:execute',
    description: 'Execute AI agents',
    category: 'Agents',
    endpoints: ['POST /api/v1/agents/*/execute'],
    requiresUserConsent: true,
  },

  // Connectors
  'connector:read': {
    name: 'connector:read',
    description: 'Read connector configurations',
    category: 'Connectors',
    endpoints: ['GET /api/v1/connectors', 'GET /api/v1/connectors/registry'],
    requiresUserConsent: true,
  },
  'connector:write': {
    name: 'connector:write',
    description: 'Create and update connectors',
    category: 'Connectors',
    endpoints: ['POST /api/v1/connectors', 'PUT /api/v1/connectors/*'],
    requiresUserConsent: true,
  },
  'connector:sync': {
    name: 'connector:sync',
    description: 'Trigger connector synchronization',
    category: 'Connectors',
    endpoints: ['POST /api/v1/connectors/*/sync'],
    requiresUserConsent: true,
  },
  'connector:delete': {
    name: 'connector:delete',
    description: 'Delete connectors',
    category: 'Connectors',
    endpoints: ['DELETE /api/v1/connectors/*'],
    requiresUserConsent: true,
  },

  // Configuration (Admin only)
  'config:read': {
    name: 'config:read',
    description: 'Read system configuration',
    category: 'Configuration',
    endpoints: ['GET /api/v1/configurationManager/*'],
    requiresUserConsent: true,
  },
  'config:write': {
    name: 'config:write',
    description: 'Update system configuration',
    category: 'Configuration',
    endpoints: [
      'PUT /api/v1/configurationManager/*',
      'POST /api/v1/configurationManager/*',
    ],
    requiresUserConsent: true,
  },

  // Storage/Documents
  'document:read': {
    name: 'document:read',
    description: 'Read documents',
    category: 'Storage',
    endpoints: ['GET /api/v1/document/*'],
    requiresUserConsent: true,
  },
  'document:write': {
    name: 'document:write',
    description: 'Upload and update documents',
    category: 'Storage',
    endpoints: ['POST /api/v1/document', 'PUT /api/v1/document/*'],
    requiresUserConsent: true,
  },
  'document:delete': {
    name: 'document:delete',
    description: 'Delete documents',
    category: 'Storage',
    endpoints: ['DELETE /api/v1/document/*'],
    requiresUserConsent: true,
  },

  // Crawling Manager
  'crawl:read': {
    name: 'crawl:read',
    description: 'Read crawling jobs',
    category: 'Crawling',
    endpoints: ['GET /api/v1/crawlingManager/*'],
    requiresUserConsent: true,
  },
  'crawl:write': {
    name: 'crawl:write',
    description: 'Create and manage crawling jobs',
    category: 'Crawling',
    endpoints: [
      'POST /api/v1/crawlingManager/*',
      'PUT /api/v1/crawlingManager/*',
    ],
    requiresUserConsent: true,
  },

  // OpenID Connect standard scopes
  openid: {
    name: 'openid',
    description: 'OpenID Connect authentication',
    category: 'Identity',
    endpoints: [],
    requiresUserConsent: false,
  },
  profile: {
    name: 'profile',
    description: 'User profile information (name, picture)',
    category: 'Identity',
    endpoints: [],
    requiresUserConsent: true,
  },
  email: {
    name: 'email',
    description: 'User email address',
    category: 'Identity',
    endpoints: [],
    requiresUserConsent: true,
  },

  // Offline access
  offline_access: {
    name: 'offline_access',
    description: 'Access when user is offline (refresh tokens)',
    category: 'Access',
    endpoints: [],
    requiresUserConsent: true,
  },
}

export const ScopeCategories = [
  'Identity',
  'Access',
  'Organization',
  'Users',
  'User Groups',
  'Teams',
  'Knowledge Base',
  'Search',
  'Conversations',
  'Agents',
  'Connectors',
  'Configuration',
  'Storage',
  'Crawling',
]

export function validateScopes(requestedScopes: string[]): {
  valid: boolean
  invalid: string[]
} {
  const validScopeNames = Object.keys(OAuthScopes)
  const invalid = requestedScopes.filter(
    (scope) => !validScopeNames.includes(scope),
  )
  return {
    valid: invalid.length === 0,
    invalid,
  }
}

export function getScopesByCategory(category: string): ScopeDefinition[] {
  return Object.values(OAuthScopes).filter((scope) => scope.category === category)
}

export function getAllScopesGroupedByCategory(): Record<
  string,
  ScopeDefinition[]
> {
  const grouped: Record<string, ScopeDefinition[]> = {}
  for (const category of ScopeCategories) {
    grouped[category] = getScopesByCategory(category)
  }
  return grouped
}

export function isValidScope(scope: string): boolean {
  return scope in OAuthScopes
}

export function getScopeDefinition(scope: string): ScopeDefinition | undefined {
  return OAuthScopes[scope]
}

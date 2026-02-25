export interface ScopeDefinition {
  name: string
  description: string
  category: string
  requiresUserConsent: boolean
}

export const OAuthScopes: Record<string, ScopeDefinition> = {
  // Organization Management
  'org:read': {
    name: 'org:read',
    description: 'Read organization information',
    category: 'Organization',
    requiresUserConsent: true,
  },
  'org:write': {
    name: 'org:write',
    description: 'Update organization settings',
    category: 'Organization',
    requiresUserConsent: true,
  },
  'org:admin': {
    name: 'org:admin',
    description: 'Full organization administration',
    category: 'Organization',
    requiresUserConsent: true,
  },

  // User Management
  'user:read': {
    name: 'user:read',
    description: 'Read user profiles',
    category: 'Users',
    requiresUserConsent: true,
  },
  'user:write': {
    name: 'user:write',
    description: 'Update user profiles',
    category: 'Users',
    requiresUserConsent: true,
  },
  'user:invite': {
    name: 'user:invite',
    description: 'Invite new users to organization',
    category: 'Users',
    requiresUserConsent: true,
  },
  'user:delete': {
    name: 'user:delete',
    description: 'Delete users from organization',
    category: 'Users',
    requiresUserConsent: true,
  },

  // User Groups
  'usergroup:read': {
    name: 'usergroup:read',
    description: 'Read user groups',
    category: 'User Groups',
    requiresUserConsent: true,
  },
  'usergroup:write': {
    name: 'usergroup:write',
    description: 'Create and manage user groups',
    category: 'User Groups',
    requiresUserConsent: true,
  },

  // Teams
  'team:read': {
    name: 'team:read',
    description: 'Read team information',
    category: 'Teams',
    requiresUserConsent: true,
  },
  'team:write': {
    name: 'team:write',
    description: 'Create and manage teams',
    category: 'Teams',
    requiresUserConsent: true,
  },

  // Knowledge Base
  'kb:read': {
    name: 'kb:read',
    description: 'Read knowledge bases and records',
    category: 'Knowledge Base',
    requiresUserConsent: true,
  },
  'kb:write': {
    name: 'kb:write',
    description: 'Create and update knowledge bases',
    category: 'Knowledge Base',
    requiresUserConsent: true,
  },
  'kb:delete': {
    name: 'kb:delete',
    description: 'Delete knowledge bases and records',
    category: 'Knowledge Base',
    requiresUserConsent: true,
  },
  'kb:upload': {
    name: 'kb:upload',
    description: 'Upload files to knowledge bases',
    category: 'Knowledge Base',
    requiresUserConsent: true,
  },

  // Semantic Search
  'semantic:read': {
    name: 'semantic:read',
    description: 'Read semantic search results and history',
    category: 'Semantic',
    requiresUserConsent: true,
  },
  'semantic:write': {
    name: 'semantic:write',
    description: 'Execute semantic search queries',
    category: 'Semantic',
    requiresUserConsent: true,
  },
  'semantic:delete': {
    name: 'semantic:delete',
    description: 'Delete semantic search history',
    category: 'Semantic',
    requiresUserConsent: true,
  },

  // Conversations
  'conversation:read': {
    name: 'conversation:read',
    description: 'Read conversations',
    category: 'Conversations',
    requiresUserConsent: true,
  },
  'conversation:write': {
    name: 'conversation:write',
    description: 'Create and manage conversations',
    category: 'Conversations',
    requiresUserConsent: true,
  },
  'conversation:chat': {
    name: 'conversation:chat',
    description: 'Send messages in conversations',
    category: 'Conversations',
    requiresUserConsent: true,
  },

  // Agents
  'agent:read': {
    name: 'agent:read',
    description: 'Read AI agents',
    category: 'Agents',
    requiresUserConsent: true,
  },
  'agent:write': {
    name: 'agent:write',
    description: 'Create and manage AI agents',
    category: 'Agents',
    requiresUserConsent: true,
  },
  'agent:execute': {
    name: 'agent:execute',
    description: 'Execute AI agents',
    category: 'Agents',
    requiresUserConsent: true,
  },

  // Connectors
  'connector:read': {
    name: 'connector:read',
    description: 'Read connector configurations',
    category: 'Connectors',
    requiresUserConsent: true,
  },
  'connector:write': {
    name: 'connector:write',
    description: 'Create and update connectors',
    category: 'Connectors',
    requiresUserConsent: true,
  },
  'connector:sync': {
    name: 'connector:sync',
    description: 'Trigger connector synchronization',
    category: 'Connectors',
    requiresUserConsent: true,
  },
  'connector:delete': {
    name: 'connector:delete',
    description: 'Delete connectors',
    category: 'Connectors',
    requiresUserConsent: true,
  },

  // Configuration (Admin only)
  'config:read': {
    name: 'config:read',
    description: 'Read system configuration',
    category: 'Configuration',
    requiresUserConsent: true,
  },
  'config:write': {
    name: 'config:write',
    description: 'Update system configuration',
    category: 'Configuration',
    requiresUserConsent: true,
  },

  // Storage/Documents
  'document:read': {
    name: 'document:read',
    description: 'Read documents',
    category: 'Storage',
    requiresUserConsent: true,
  },
  'document:write': {
    name: 'document:write',
    description: 'Upload and update documents',
    category: 'Storage',
    requiresUserConsent: true,
  },
  'document:delete': {
    name: 'document:delete',
    description: 'Delete documents',
    category: 'Storage',
    requiresUserConsent: true,
  },

  // Crawling Manager
  'crawl:read': {
    name: 'crawl:read',
    description: 'Read crawling jobs',
    category: 'Crawling',
    requiresUserConsent: true,
  },
  'crawl:write': {
    name: 'crawl:write',
    description: 'Create and manage crawling jobs',
    category: 'Crawling',
    requiresUserConsent: true,
  },
  'crawl:delete': {
    name: 'crawl:delete',
    description: 'Delete crawling jobs',
    category: 'Crawling',
    requiresUserConsent: true,
  },

  // OpenID Connect standard scopes
  openid: {
    name: 'openid',
    description: 'OpenID Connect authentication',
    category: 'Identity',
    requiresUserConsent: false,
  },
  profile: {
    name: 'profile',
    description: 'User profile information (name, picture)',
    category: 'Identity',
    requiresUserConsent: true,
  },
  email: {
    name: 'email',
    description: 'User email address',
    category: 'Identity',
    requiresUserConsent: true,
  },

  // Offline access
  offline_access: {
    name: 'offline_access',
    description: 'Access when user is offline (refresh tokens)',
    category: 'Access',
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
  'Semantic',
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

/**
 * Sidebar of the API docs page at /api/v1/docs: heading -> nav item -> tags (accordions).
 * Endpoints and accordions come from the spec; this only groups its tags. A tag with
 * endpoints listed nowhere here still shows, under "Other"; a listed tag without
 * endpoints is skipped. Kept out of the spec so the SDK spec built from it is unchanged.
 * A spec overlay can add to it with a top-level `x-pipeshub-nav` of the same shape.
 */
export interface NavModuleSpec {
  name?: string;
  description?: string;
  icon?: string;
  tags?: string[];
}

export interface NavCategorySpec {
  name?: string;
  description?: string;
  modules?: NavModuleSpec[];
}

export const API_DOCS_NAV: NavCategorySpec[] = [
  {
    name: 'Identity & Access',
    modules: [
      {
        name: 'Authentication',
        icon: 'lock',
        description:
          'User authentication, authorization, and session management',
        tags: [
          'User Account',
          'Organization Auth Config',
          'Personal Access Tokens',
        ],
      },
      {
        name: 'OAuth App Management',
        icon: 'key',
        description:
          'OAuth 2.0 authorization server, app registration, and OpenID Connect',
        tags: ['OAuth Provider', 'OAuth Apps', 'OpenID Connect'],
      },
      {
        name: 'User Management',
        icon: 'user',
        description:
          'Users, service accounts, teams, groups, organizations, and notifications',
        tags: [
          'Users',
          'Service Accounts',
          'Service Tokens',
          'Teams',
          'Organizations',
          'User Groups',
          'Notifications',
        ],
      },
    ],
  },
  {
    name: 'Data Management',
    modules: [
      {
        name: 'Storage',
        icon: 'folder',
        description:
          'Service-to-service document storage (not callable with a user token)',
        tags: ['Internal Storage'],
      },
      {
        name: 'Knowledge Base',
        icon: 'book',
        description: 'Knowledge bases, folders, records, and permissions',
        tags: [
          'Knowledge Base',
          'Knowledge Hub',
          'Folders',
          'Records',
          'Permissions',
          'Upload',
          'Connector',
        ],
      },
      {
        name: 'Artifacts',
        icon: 'image',
        description:
          'Generated artifacts, with preview, download, and versions',
        tags: ['Artifacts'],
      },
    ],
  },
  {
    name: 'Search & AI',
    modules: [
      {
        name: 'Enterprise Search',
        icon: 'search',
        description: 'Conversational AI, semantic search, and code graphs',
        tags: [
          'Conversations',
          'Projects',
          'Code Graph',
          'Semantic Search',
          'Conversational Speech',
        ],
      },
      {
        name: 'Agents',
        icon: 'cpu',
        description: 'Custom AI agents, agent templates, and skills',
        tags: ['Agents', 'Agent Templates', 'Agent Conversations', 'Skills'],
      },
    ],
  },
  {
    name: 'Integrations',
    modules: [
      {
        name: 'Connector Manager',
        icon: 'code',
        description:
          'Third-party integrations, OAuth flows, and data synchronization',
        tags: [
          'Connector Registry',
          'Connector Instances',
          'Connector Configuration',
          'Connector Control',
          'Connector OAuth',
          'Connector Filters',
          'OAuth Configuration',
        ],
      },
      {
        name: 'Toolsets',
        icon: 'tool',
        description:
          'Toolsets that give agents actions in other apps or your own REST APIs',
        tags: [
          'Toolset Registry',
          'Toolset Instances',
          'Toolset Configuration',
          'Toolset OAuth',
          'API Toolsets',
        ],
      },
      {
        name: 'MCP Servers',
        icon: 'server',
        description: 'External MCP servers that agents and users connect to',
        tags: [
          'MCP Server Catalog',
          'MCP Server Instances',
          'MCP Server Credentials',
          'MCP Server OAuth',
          'MCP Servers for Agents',
          'MCP Servers',
        ],
      },
      {
        name: 'MCP',
        icon: 'layers',
        description: 'The PipesHub MCP endpoint at /mcp',
        tags: ['MCP'],
      },
    ],
  },
  {
    name: 'System',
    modules: [
      {
        name: 'Configuration Manager',
        icon: 'gear',
        description: 'System-wide configuration management',
        tags: [
          'Storage Configuration',
          'SMTP Configuration',
          'Authentication Configuration',
          'Platform Settings',
          'AI Models Providers',
          'Configuration Manager',
          'Public URLs',
          'Web Search',
          'Metrics Collection',
        ],
      },
      {
        name: 'Crawling Manager',
        icon: 'clock',
        description: 'Data crawling job scheduling and monitoring',
        tags: ['Crawling Jobs'],
      },
      {
        name: 'Mail',
        icon: 'mail',
        description: 'Service-to-service email sending and SMTP setup',
        tags: ['Mail'],
      },
      {
        name: 'Health',
        icon: 'activity',
        description: 'Health of the deployment, and the build that it runs',
        tags: ['Health'],
      },
      {
        name: 'API Documentation',
        icon: 'book',
        description: 'This API reference and the JSON it renders',
        tags: ['API Documentation'],
      },
    ],
  },
];

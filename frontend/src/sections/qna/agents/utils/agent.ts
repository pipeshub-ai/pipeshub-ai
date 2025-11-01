// src/sections/agents/utils/agent-utils.ts
import type {
  Agent,
  AgentFormData,
  AgentTemplateFormData,
  AgentFilterOptions,
} from 'src/types/agent';

import chatIcon from '@iconify-icons/mdi/chat';
import databaseIcon from '@iconify-icons/mdi/database';
import emailIcon from '@iconify-icons/mdi/email';
import apiIcon from '@iconify-icons/mdi/api';
import toolIcon from '@iconify-icons/mdi/tools';
import calculatorIcon from '@iconify-icons/mdi/calculator';
import { alpha, Theme } from '@mui/material/styles';
// Validation functions
export const validateAgentForm = (data: AgentFormData): Record<string, string> => {
  const errors: Record<string, string> = {};

  if (!data.name.trim()) {
    errors.name = 'Agent name is required';
  } else if (data.name.length < 3) {
    errors.name = 'Agent name must be at least 3 characters long';
  } else if (data.name.length > 50) {
    errors.name = 'Agent name must be less than 50 characters';
  }

  if (!data.description.trim()) {
    errors.description = 'Description is required';
  } else if (data.description.length < 10) {
    errors.description = 'Description must be at least 10 characters long';
  } else if (data.description.length > 500) {
    errors.description = 'Description must be less than 500 characters';
  }

  if (!data.startMessage.trim()) {
    errors.startMessage = 'Start message is required';
  } else if (data.startMessage.length < 10) {
    errors.startMessage = 'Start message must be at least 10 characters long';
  }

  if (!data.systemPrompt.trim()) {
    errors.systemPrompt = 'System prompt is required';
  } else if (data.systemPrompt.length < 20) {
    errors.systemPrompt = 'System prompt must be at least 20 characters long';
  }

  return errors;
};

export const validateAgentTemplateForm = (data: AgentTemplateFormData): Record<string, string> => {
  const errors: Record<string, string> = {};

  if (!data.name.trim()) {
    errors.name = 'Template name is required';
  } else if (data.name.length < 3) {
    errors.name = 'Template name must be at least 3 characters long';
  } else if (data.name.length > 50) {
    errors.name = 'Template name must be less than 50 characters';
  }

  if (!data.description.trim()) {
    errors.description = 'Description is required';
  } else if (data.description.length < 10) {
    errors.description = 'Description must be at least 10 characters long';
  } else if (data.description.length > 500) {
    errors.description = 'Description must be less than 500 characters';
  }

  if (!data.category.trim()) {
    errors.category = 'Category is required';
  }

  if (!data.startMessage.trim()) {
    errors.startMessage = 'Start message is required';
  } else if (data.startMessage.length < 10) {
    errors.startMessage = 'Start message must be at least 10 characters long';
  }

  if (!data.systemPrompt.trim()) {
    errors.systemPrompt = 'System prompt is required';
  } else if (data.systemPrompt.length < 20) {
    errors.systemPrompt = 'System prompt must be at least 20 characters long';
  }

  return errors;
};

// Initial form data
export const getInitialAgentFormData = (): AgentFormData => ({
  name: '',
  description: '',
  startMessage: '',
  systemPrompt: '',
  tools: [],
  models: [],
  apps: [],
  kb: [],
  vectorDBs: [],
  tags: [],
});

export const getInitialTemplateFormData = (): AgentTemplateFormData => ({
  name: '',
  description: '',
  category: '',
  startMessage: '',
  systemPrompt: '',
  tags: [],
});

// Filtering and sorting
export const filterAgents = (agents: Agent[], filters: AgentFilterOptions): Agent[] =>
  agents?.filter((agent) => {
    if (filters.searchQuery) {
      const query = filters.searchQuery.toLowerCase();
      const searchableText =
        `${agent.name} ${agent.description} ${agent.tags.join(' ')}`.toLowerCase();
      if (!searchableText.includes(query)) {
        return false;
      }
    }

    if (filters.tags && filters.tags.length > 0) {
      const hasMatchingTag = filters.tags.some((tag) => agent.tags.includes(tag));
      if (!hasMatchingTag) {
        return false;
      }
    }

    if (filters.createdBy && agent.createdBy !== filters.createdBy) {
      return false;
    }

    if (filters.dateRange) {
      const agentDate = new Date(agent.createdAtTimestamp);
      const startDate = new Date(filters.dateRange.start);
      const endDate = new Date(filters.dateRange.end);

      if (agentDate < startDate || agentDate > endDate) {
        return false;
      }
    }

    return true;
  });

export const sortAgents = (agents: Agent[], sortBy: string, order: 'asc' | 'desc'): Agent[] =>
  [...agents].sort((a, b) => {
    let aValue: any;
    let bValue: any;

    switch (sortBy) {
      case 'name':
        aValue = a.name.toLowerCase();
        bValue = b.name.toLowerCase();
        break;
      case 'createdAt':
        aValue = new Date(a.createdAtTimestamp);
        bValue = new Date(b.createdAtTimestamp);
        break;
      case 'updatedAt':
        aValue = new Date(a.updatedAtTimestamp);
        bValue = new Date(b.updatedAtTimestamp);
        break;
      case 'lastUsedAt':
        aValue = a.lastUsedAt ? new Date(a.lastUsedAt) : new Date(0);
        bValue = b.lastUsedAt ? new Date(b.lastUsedAt) : new Date(0);
        break;
      case 'conversationCount':
        aValue = a.conversationCount;
        bValue = b.conversationCount;
        break;
      default:
        aValue = a.name.toLowerCase();
        bValue = b.name.toLowerCase();
    }

    if (aValue < bValue) return order === 'asc' ? -1 : 1;
    if (aValue > bValue) return order === 'asc' ? 1 : -1;
    return 0;
  });

// Status and formatting utilities
export const getStatusColor = (status: string): string => {
  switch (status) {
    case 'active':
      return 'success';
    case 'inactive':
      return 'error';
    case 'draft':
      return 'warning';
    default:
      return 'default';
  }
};

export const getStatusText = (status: string): string => {
  switch (status) {
    case 'active':
      return 'Active';
    case 'inactive':
      return 'Inactive';
    case 'draft':
      return 'Draft';
    default:
      return 'Unknown';
  }
};

export const formatTimestamp = (timestamp: string): string => {
  const date = new Date(timestamp);
  const now = new Date();
  const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);

  if (diffInSeconds < 60) {
    return 'Just now';
  }
  if (diffInSeconds < 3600) {
    const minutes = Math.floor(diffInSeconds / 60);
    return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
  }
  if (diffInSeconds < 86400) {
    const hours = Math.floor(diffInSeconds / 3600);
    return `${hours} hour${hours > 1 ? 's' : ''} ago`;
  }
  if (diffInSeconds < 604800) {
    const days = Math.floor(diffInSeconds / 86400);
    return `${days} day${days > 1 ? 's' : ''} ago`;
  }
  return date.toLocaleDateString();
};

export const formatConversationCount = (count: number): string => {
  if (count === 0) return 'No conversations';
  if (count === 1) return '1 conversation';
  if (count < 1000) return `${count} conversations`;
  if (count < 1000000) return `${(count / 1000).toFixed(1)}k conversations`;
  return `${(count / 1000000).toFixed(1)}M conversations`;
};

// Template categories
export const TEMPLATE_CATEGORIES = [
  'Customer Support',
  'Content Creation',
  'Data Analysis',
  'Development',
  'Marketing',
  'Sales',
  'HR & Recruitment',
  'Education',
  'Finance',
  'Healthcare',
  'Legal',
  'General Purpose',
] as const;

// Common tools, models, etc.
export const COMMON_TOOLS = [
  'web_search',
  'calculator',
  'code_interpreter',
  'file_upload',
  'image_generator',
  'pdf_reader',
  'email_sender',
  'calendar',
  'database_query',
  'api_caller',
] as const;

export const COMMON_MODELS = [
  'gpt-4',
  'gpt-3.5-turbo',
  'claude-3-opus',
  'claude-3-sonnet',
  'claude-3-haiku',
  'gemini-pro',
  'llama-2-70b',
  'mistral-large',
] as const;

// Export utilities for consistent tag management
export const normalizeTags = (tags: string[]): string[] =>
  tags
    .map((tag) => tag.trim().toLowerCase())
    .filter((tag) => tag.length > 0)
    .filter((tag, index, array) => array.indexOf(tag) === index) // Remove duplicates
    .slice(0, 10); // Limit to 10 tags

export const getTagColor = (tag: string): string => {
  // Generate consistent colors for tags based on their content
  let hash = 0;
  for (let i = 0; i < tag.length; i += 1) {
    // Using mathematical equivalent instead of bitwise operator
    hash = tag.charCodeAt(i) + (hash * 32 - hash);
  }

  const colors = ['primary', 'secondary', 'info', 'success', 'warning', 'error'];
  return colors[Math.abs(hash) % colors.length];
};

export const normalizeDisplayName = (name: string): string =>
  name
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ');

export const formattedProvider = (provider: string): string => {
  switch (provider) {
    case 'azureOpenAI':
      return 'Azure OpenAI';
    case 'openAI':
      return 'OpenAI';
    case 'anthropic':
      return 'Anthropic';
    case 'gemini':
      return 'Gemini';
    case 'claude':
      return 'Claude';
    case 'ollama':
      return 'Ollama';
    case 'bedrock':
      return 'AWS Bedrock';
    case 'xai':
      return 'xAI';
    case 'together':
      return 'Together';
    case 'groq':
      return 'Groq';
    case 'fireworks':
      return 'Fireworks';
    case 'cohere':
      return 'Cohere';
    case 'openAICompatible':
      return 'OpenAI API Compatible';
    case 'mistral':
      return 'Mistral';
    case 'voyage':
      return 'Voyage';
    case 'jinaAI':
      return 'Jina AI';
    case 'sentenceTransformers':
    case 'default':
      return 'Default';
    default:
      return provider;
  }
};

// Helper function to truncate text
export const truncateText = (text: string, maxLength: number = 50): string => {
  if (text.length <= maxLength) return text;
  return `${text.substring(0, maxLength)}...`;
};

// Helper function to get app icon
export const getAppIcon = (appName: string) => {
  const iconMap: { [key: string]: any } = {
    calculator: calculatorIcon,
    gmail: emailIcon,
    google_calendar: apiIcon,
    google_drive: databaseIcon,
    confluence: databaseIcon,
    github: apiIcon,
    jira: apiIcon,
    slack: chatIcon,
    google_drive_enterprise: databaseIcon,
  };
  return iconMap[appName] || toolIcon;
};

// Helper function to group tools by app
export const groupToolsByApp = (availableTools: any[]): Record<string, any[]> => {
  const groupedTools: Record<string, any[]> = {};
  availableTools.forEach((tool) => {
    const appName = tool.app_name;
    if (!groupedTools[appName]) {
      groupedTools[appName] = [];
    }
    groupedTools[appName].push(tool);
  });
  return groupedTools;
};

// Helper function to get app display name
export const getAppDisplayName = (appName: string): string => {
  const nameMap: Record<string, string> = {
    gmail: 'Gmail',
    google_calendar: 'Google Calendar',
    google_drive: 'Google Drive',
    confluence: 'Confluence',
    github: 'GitHub',
    jira: 'Jira',
    slack: 'Slack',
    calculator: 'Calculator',
    google_drive_enterprise: 'Google Drive Enterprise',
  };
  return nameMap[appName] || appName.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
};

// Helper function to normalize app names
export const normalizeAppName = (appName: string): string => {
  const nameMap: Record<string, string> = {
    calculator: 'Calculator',
    gmail: 'Gmail',
    google_calendar: 'Google Calendar',
    google_drive: 'Google Drive',
    confluence: 'Confluence',
    github: 'GitHub',
    jira: 'Jira',
    slack: 'Slack',
    google_drive_enterprise: 'Google Drive Enterprise',
    SLACK: 'Slack',
    GMAIL: 'Gmail',
    GOOGLE_DRIVE: 'Google Drive',
    GOOGLE_WORKSPACE: 'Google Workspace',
    ONEDRIVE: 'OneDrive',
    JIRA: 'Jira',
    CONFLUENCE: 'Confluence',
    GITHUB: 'GitHub',
  };

  return (
    nameMap[appName] ||
    appName
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ')
  );
};

// Helper function to get app memory icon
export const getAppKnowledgeIcon = (appName: string) => {
  const iconMap: Record<string, any> = {
    SLACK: chatIcon,
    GMAIL: emailIcon,
    GOOGLE_DRIVE: databaseIcon,
    GOOGLE_WORKSPACE: databaseIcon,
    ONEDRIVE: databaseIcon,
    JIRA: apiIcon,
    CONFLUENCE: databaseIcon,
    GITHUB: apiIcon,
    // Fallback based on display name
    Slack: chatIcon,
    Gmail: emailIcon,
    'Google Drive': databaseIcon,
    'Google Workspace': databaseIcon,
    OneDrive: databaseIcon,
    Jira: apiIcon,
    Confluence: databaseIcon,
    GitHub: apiIcon,
  };
  return iconMap[appName] || databaseIcon;
};

// Extract agent configuration from flow nodes and edges
export const extractAgentConfigFromFlow = (
  agentName: string,
  nodes: any[],
  edges: any[],
  currentAgent?: Agent | null
) => {
  const tools: string[] = [];
  const models: { provider: string; modelName: string; isReasoning: boolean }[] = [];
  const kb: string[] = [];
  const apps: string[] = [];

  nodes.forEach((node) => {
    if (node.data.type.startsWith('tool-group-')) {
      // Handle tool group nodes - extract all tools from the group
      if (node.data.config?.tools && Array.isArray(node.data.config.tools)) {
        node.data.config.tools.forEach((tool: any) => {
          if (tool.fullName) {
            tools.push(tool.fullName);
          }
        });
      }
    } else if (node.data.type.startsWith('tool-')) {
      // Handle individual tool nodes
      const toolName = node.data.config?.fullName || node.data.config?.toolId;
      if (toolName) {
        tools.push(toolName);
      }
    } else if (node.data.type.startsWith('llm-')) {
      models.push({
        provider: node.data.config.provider || 'azureOpenAI',
        modelName: node.data.config.modelName || node.data.config.model,
        isReasoning: node.data.config.isReasoning || false,
      });
    } else if (node.data.type.startsWith('kb-') && node.data.type !== 'kb-group') {
      // Individual knowledge base nodes
      kb.push(node.data.config.kbId);
    } else if (node.data.type.startsWith('app-') && node.data.type !== 'app-group') {
      // Individual app knowledge nodes (not group node)
      if (node.data.config?.appName && !apps.includes(node.data.config.appName)) {
        apps.push(node.data.config.appName);
      }
    }
  });

  // Handle app-group and kb-group nodes
  const appKnowledgeGroupNode = nodes.find((node) => node.data.type === 'app-group');
  if (appKnowledgeGroupNode && appKnowledgeGroupNode.data.config?.selectedApps) {
    appKnowledgeGroupNode.data.config.selectedApps.forEach((app: string) => {
      if (!apps.includes(app)) {
        apps.push(app);
      }
    });
  }

  const kbGroupNode = nodes.find((node) => node.data.type === 'kb-group');
  if (kbGroupNode && kbGroupNode.data.config?.selectedKBs) {
    kbGroupNode.data.config.selectedKBs.forEach((kbId: string) => {
      if (!kb.includes(kbId)) {
        kb.push(kbId);
      }
    });
  }

  const agentCoreNode = nodes.find((node) => node.data.type === 'agent-core');
  return {
    name: agentName,
    description:
      agentCoreNode?.data.config?.description ||
      currentAgent?.description ||
      'AI agent for task automation and assistance',
    startMessage:
      agentCoreNode?.data.config?.startMessage ||
      currentAgent?.startMessage ||
      'Hello! I am a flow-based AI agent ready to assist you.',
    systemPrompt:
      agentCoreNode?.data.config?.systemPrompt ||
      currentAgent?.systemPrompt ||
      'You are a sophisticated flow-based AI agent that processes information through a visual workflow.',
    tools,
    models,
    apps,
    kb,
    vectorDBs: [],
    tags: currentAgent?.tags || ['flow-based', 'visual-workflow'],
    flow: {
      nodes,
      edges,
    },
  };
};

export const userChipStyle = (isDark: boolean, theme: Theme) => ({
  borderRadius: 0.75,
  height: 24,
  fontSize: '0.75rem',
  fontWeight: 500,
  bgcolor: isDark ? alpha('#ffffff', 0.9) : alpha(theme.palette.primary.main, 0.1),
  color: isDark ? theme.palette.primary.main : theme.palette.primary.main,
  border: `1px solid ${isDark ? alpha(theme.palette.primary.main, 0.2) : alpha(theme.palette.primary.main, 0.2)}`,
  '& .MuiChip-deleteIcon': {
    color: isDark ? alpha(theme.palette.primary.main, 0.7) : alpha(theme.palette.primary.main, 0.7),
    '&:hover': {
      color: isDark ? theme.palette.primary.main : theme.palette.primary.main,
    },
  },
});

export const groupChipStyle = (isDark: boolean, theme: Theme) => ({
  borderRadius: 0.75,
  height: 20,
  fontSize: '0.75rem',
  fontWeight: 500,
  bgcolor: isDark ? alpha('#ffffff', 0.9) : alpha(theme.palette.info.main, 0.1),
  color: isDark ? theme.palette.info.main : theme.palette.info.main,
  border: `1px solid ${isDark ? alpha(theme.palette.info.main, 0.2) : alpha(theme.palette.info.main, 0.2)}`,
  '& .MuiChip-deleteIcon': {
    color: isDark ? alpha(theme.palette.info.main, 0.7) : alpha(theme.palette.info.main, 0.7),
    '&:hover': {
      color: isDark ? theme.palette.info.main : theme.palette.info.main,
    },
  },
});

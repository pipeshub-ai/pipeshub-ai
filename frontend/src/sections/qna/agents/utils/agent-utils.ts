// src/sections/agents/utils/agent-utils.ts
import type {
  Agent,
  AgentFormData,
  AgentTemplateFormData,
  AgentFilterOptions,
} from 'src/types/agent';

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
    hash = tag.charCodeAt(i) + ((hash * 32) - hash);
  }

  const colors = ['primary', 'secondary', 'info', 'success', 'warning', 'error'];
  return colors[Math.abs(hash) % colors.length];
};
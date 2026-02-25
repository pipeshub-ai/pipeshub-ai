// src/sections/agents/utils/agent-utils.ts
import type {
  Agent,
  AgentFormData,
  AgentTemplateFormData,
  AgentFilterOptions,
  ToolsetReference,
  KnowledgeReference,
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
  connectors: [], // Unified array with category field
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

// Internal interface for building toolset data
interface ToolsetDataInternal {
  name: string; // Normalized toolset name (e.g., "googledrive")
  displayName: string; // Display name (e.g., "Google Drive")
  type: string; // Toolset type (e.g., "app")
  tools: {
    name: string; // Tool name (e.g., "get_files_list")
    fullName: string; // Full name (e.g., "googledrive.get_files_list")
    description?: string;
  }[];
}

// Internal interface for building knowledge data
interface KnowledgeDataInternal {
  connectorId: string; // Connector instance ID
  filters: {
    recordGroups?: string[];
    records?: string[];
    [key: string]: any;
  };
  category: 'knowledge' | 'action';
}

// Extract agent configuration from flow nodes and edges
export const extractAgentConfigFromFlow = (
  agentName: string,
  nodes: any[],
  edges: any[],
  currentAgent?: Agent | null,
  shareWithOrg?: boolean
) => {
  const toolsetsInternal: ToolsetDataInternal[] = []; // Toolset objects with nested tools
  const knowledgeInternal: KnowledgeDataInternal[] = []; // Knowledge objects with connectorId and filters (includes both apps and KBs)
  const models: { provider: string; modelName: string; isReasoning: boolean, modelKey: string }[] = [];
  
  // Helper function to add or update toolset with tools
  const addToolsetWithTools = (
    toolsetName: string,
    displayName: string,
    toolsetType: string,
    toolsToAdd: { name: string; fullName: string; description?: string }[]
  ) => {
    if (!toolsetName || toolsToAdd.length === 0) return;
    
    const normalizedName = toolsetName.toLowerCase().replace(/[^a-z0-9]/g, '');
    
    // Find existing toolset
    const existingIndex = toolsetsInternal.findIndex((ts) => ts.name === normalizedName);
    
    if (existingIndex >= 0) {
      // Add tools to existing toolset (avoid duplicates)
      toolsToAdd.forEach((tool) => {
        if (!toolsetsInternal[existingIndex].tools.find((t) => t.fullName === tool.fullName)) {
          toolsetsInternal[existingIndex].tools.push(tool);
        }
      });
    } else {
      // Add new toolset
      toolsetsInternal.push({
        name: normalizedName,
        displayName: displayName || toolsetName,
        type: toolsetType || 'app',
        tools: toolsToAdd,
      });
    }
  };

  // Helper function to add knowledge source
  // Properly merges filters, especially recordGroups and records arrays
  const addKnowledgeSource = (
    connectorId: string,
    filters: { recordGroups?: string[]; records?: string[]; [key: string]: any },
    category: 'knowledge' | 'action' = 'knowledge'
  ) => {
    if (!connectorId) return;
    
    // Normalize filters - ensure recordGroups and records are arrays
    const normalizedFilters = {
      recordGroups: Array.isArray(filters?.recordGroups) ? filters.recordGroups : [],
      records: Array.isArray(filters?.records) ? filters.records : [],
      ...filters,
    };
    
    // Check if knowledge source with same connectorId already exists
    const existingIndex = knowledgeInternal.findIndex((k) => k.connectorId === connectorId);
    
    if (existingIndex >= 0) {
      // Merge with existing entry - combine arrays, preserve other fields
      const existing = knowledgeInternal[existingIndex];
      const existingRecordGroups = Array.isArray(existing.filters?.recordGroups) 
        ? existing.filters.recordGroups 
        : [];
      const existingRecords = Array.isArray(existing.filters?.records) 
        ? existing.filters.records 
        : [];
      
      knowledgeInternal[existingIndex] = {
        connectorId,
        filters: {
          ...existing.filters,
          ...normalizedFilters,
          // Merge arrays, removing duplicates
          recordGroups: [...new Set([...existingRecordGroups, ...normalizedFilters.recordGroups])],
          records: [...new Set([...existingRecords, ...normalizedFilters.records])],
        },
        category,
      };
    } else {
      // Add new entry
      knowledgeInternal.push({
        connectorId,
        filters: normalizedFilters,
        category,
      });
    }
  };

  // Build a set of toolset node IDs that are connected to the agent via edges
  // Only these toolsets should be included in the agent configuration
  const connectedToolsetNodeIds = new Set<string>();
  
  edges.forEach((edge) => {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    const targetNode = nodes.find((n) => n.id === edge.target);
    
    // If a toolset is connected to the agent
    if (sourceNode?.data.type.startsWith('toolset-') && targetNode?.data.type === 'agent-core') {
      connectedToolsetNodeIds.add(sourceNode.id);
    }
    // Also check reverse direction (agent -> toolset)
    if (targetNode?.data.type.startsWith('toolset-') && sourceNode?.data.type === 'agent-core') {
      connectedToolsetNodeIds.add(targetNode.id);
    }
  });

  // Extract toolsets ONLY from nodes that are connected to the agent via edges
  nodes.forEach((node) => {
    // Only process toolsets that are connected to the agent
    if (!node.data.type.startsWith('toolset-') || !connectedToolsetNodeIds.has(node.id)) {
      return;
    }
    
    const toolsetConfig = node.data.config;
    const toolsetName = toolsetConfig?.toolsetName || toolsetConfig?.name || node.data.label || '';
    const displayName = toolsetConfig?.displayName || node.data.label || toolsetName;
    const toolsetType = toolsetConfig?.type || toolsetConfig?.category || 'app';
    const toolsFromConfig: { name: string; fullName: string; description?: string }[] = [];
    
    // Extract tools from the tools array
    if (toolsetConfig?.tools && Array.isArray(toolsetConfig.tools)) {
      toolsetConfig.tools.forEach((tool: any) => {
        const toolName = tool.name || tool.toolName || '';
        const normalizedToolsetName = toolsetName.toLowerCase().replace(/[^a-z0-9]/g, '');
        const toolFullName = tool.fullName || `${normalizedToolsetName}.${toolName}`;
        if (toolFullName && toolName) {
          toolsFromConfig.push({
            name: toolName,
            fullName: toolFullName,
            description: tool.description || '',
          });
        }
      });
    }
    
    // Also check selectedTools array if present and tools array is empty
    if (toolsFromConfig.length === 0 && toolsetConfig?.selectedTools && Array.isArray(toolsetConfig.selectedTools)) {
      toolsetConfig.selectedTools.forEach((selectedToolName: string) => {
        // Find the tool in the tools array if available
        const tool = toolsetConfig?.tools?.find((t: any) => 
          t.name === selectedToolName || t.toolName === selectedToolName
        );
        const normalizedToolsetName = toolsetName.toLowerCase().replace(/[^a-z0-9]/g, '');
        const toolFullName = tool?.fullName || `${normalizedToolsetName}.${selectedToolName}`;
        
        if (!toolsFromConfig.find((t) => t.fullName === toolFullName)) {
          toolsFromConfig.push({
            name: tool?.name || tool?.toolName || selectedToolName,
            fullName: toolFullName,
            description: tool?.description || '',
          });
        }
      });
    }
    
    // Add toolset with its tools
    if (toolsFromConfig.length > 0) {
      addToolsetWithTools(toolsetName, displayName, toolsetType, toolsFromConfig);
    }
  });

  // Build sets of connected node IDs (similar to toolsets)
  const connectedKnowledgeNodeIds = new Set<string>();
  const connectedLLMNodeIds = new Set<string>();
  
  edges.forEach((edge) => {
    const sourceNode = nodes.find((n) => n.id === edge.source);
    const targetNode = nodes.find((n) => n.id === edge.target);
    
    // Knowledge nodes connected to agent
    if ((sourceNode?.data.type.startsWith('kb-') && sourceNode.data.type !== 'kb-group') ||
        (sourceNode?.data.type.startsWith('app-') && sourceNode.data.type !== 'app-group')) {
      if (targetNode?.data.type === 'agent-core' && edge.targetHandle === 'knowledge') {
        connectedKnowledgeNodeIds.add(sourceNode.id);
      }
    }
    
    // LLM nodes connected to agent
    if (sourceNode?.data.type.startsWith('llm-')) {
      if (targetNode?.data.type === 'agent-core' && edge.targetHandle === 'llms') {
        connectedLLMNodeIds.add(sourceNode.id);
      }
    }
  });

  // Extract models and knowledge from nodes - ONLY if connected to agent
  nodes.forEach((node) => {
    if (node.data.type.startsWith('llm-')) {
      // Only include LLM if connected to agent
      if (connectedLLMNodeIds.has(node.id)) {
        models.push({
          provider: node.data.config.provider || 'azureOpenAI',
          modelName: node.data.config.modelName || node.data.config.model,
          isReasoning: node.data.config.isReasoning || false,
          modelKey: node.data.config.modelKey || '',
        });
      }
    } else if (node.data.type.startsWith('kb-') && node.data.type !== 'kb-group') {
      // Individual knowledge base nodes - Convert KB (record group) to knowledge format
      // KBs ARE record groups themselves, so:
      // - connectorId: KB connector instance ID from KB document (e.g., "knowledgeBase_orgId")
      // - filters.recordGroups: Array containing the KB ID (record group ID)
      // - filters.records: Optional array of specific record IDs within the KB
      if (connectedKnowledgeNodeIds.has(node.id)) {
        const kbId = node.data.config?.kbId;
        if (kbId) {
          // KBs are record groups - the kbId IS the record group ID
          // Get KB connector instance ID from config (should be set from KB document's connectorId field)
          // This is the actual KB connector instance ID, not the KB ID itself
          const kbConnectorInstanceId = node.data.config?.connectorInstanceId || 
                                       node.data.config?.kbConnectorId;
          
          if (!kbConnectorInstanceId) {
            // If connectorId is not available, log warning but still proceed with KB ID as fallback
            // The backend should ideally always have the connectorId from the KB document
            console.warn(`KB node ${kbId} missing connectorInstanceId. Using KB ID as fallback.`);
          }
          
          // Extract filters - KBs have the KB ID in recordGroups
          const filters = {
            recordGroups: [kbId], // KB ID is the record group ID
            records: node.data.config?.selectedRecords || node.data.config?.filters?.records || [],
            // Preserve any other filter fields
            ...(node.data.config?.filters || {}),
          };
          
          // Ensure recordGroups includes the kbId
          if (!filters.recordGroups.includes(kbId)) {
            filters.recordGroups = [kbId, ...filters.recordGroups];
          }
          
          // Add to knowledge format - use connectorId from KB document, fallback to KB ID if not available
          addKnowledgeSource(kbConnectorInstanceId || kbId, filters, 'knowledge');
        }
      }
    } else if (node.data.type.startsWith('app-') && node.data.type !== 'app-group') {
      // Individual app knowledge nodes (connector instances) - ONLY if connected to agent
      // Apps are connectors, so:
      // - connectorId: The connector instance ID
      // - filters.recordGroups: Optional array of record group IDs within that connector
      // - filters.records: Optional array of record IDs within that connector
      if (connectedKnowledgeNodeIds.has(node.id)) {
        const connectorInstanceId = node.data.config?.connectorInstanceId || node.data.config?.id;
        
        if (connectorInstanceId) {
          // Extract filters from node config
          // Apps can have recordGroups (record groups within the connector) and records
          const nodeFilters = node.data.config?.filters || {};
          const filters = {
            // Preserve any other filter fields first
            ...nodeFilters,
            // Then override with explicit values
            recordGroups: node.data.config?.selectedRecordGroups || 
                         nodeFilters.recordGroups || 
                         [],
            records: node.data.config?.selectedRecords || 
                    nodeFilters.records || 
                    [],
          };
          
          // Add to knowledge format
          addKnowledgeSource(connectorInstanceId, filters, 'knowledge');
        }
      }
    }
  });

  // Handle app-group nodes - extract knowledge sources from selectedApps
  // ONLY if the app-group node is connected to agent
  const appKnowledgeGroupNode = nodes.find((node) => node.data.type === 'app-group');
  if (appKnowledgeGroupNode) {
    // Check if app-group node is connected to agent (as source)
    const isAppGroupConnected = edges.some((edge) => {
      const sourceNode = nodes.find((n) => n.id === edge.source);
      const targetNode = nodes.find((n) => n.id === edge.target);
      return sourceNode?.id === appKnowledgeGroupNode.id && 
             targetNode?.data.type === 'agent-core' && 
             edge.targetHandle === 'knowledge';
    });
    
    if (isAppGroupConnected && appKnowledgeGroupNode.data.config?.selectedApps) {
      appKnowledgeGroupNode.data.config.selectedApps.forEach((connectorInstanceId: string) => {
        // Get filters for this specific connector
        const connectorFilters = appKnowledgeGroupNode.data.config?.appFilters?.[connectorInstanceId] || {
          recordGroups: [],
          records: [],
        };
        
        // Add to knowledge format
        addKnowledgeSource(connectorInstanceId, connectorFilters, 'knowledge');
      });
    }
  }

  // Handle kb-group nodes - extract KB IDs from selectedKBs and convert to knowledge format
  // ONLY if the kb-group node is connected to agent
  const kbGroupNode = nodes.find((node) => node.data.type === 'kb-group');
  if (kbGroupNode) {
    // Check if kb-group node is connected to agent (as source)
    const isKBGroupConnected = edges.some((edge) => {
      const sourceNode = nodes.find((n) => n.id === edge.source);
      const targetNode = nodes.find((n) => n.id === edge.target);
      return sourceNode?.id === kbGroupNode.id && 
             targetNode?.data.type === 'agent-core' && 
             edge.targetHandle === 'knowledge';
    });
    
    if (isKBGroupConnected && kbGroupNode.data.config?.selectedKBs) {
      // Get KB connector instance IDs from group node config
      // Each KB should have its connectorId stored in kbConnectorIds mapping
      const kbConnectorIds = kbGroupNode.data.config?.kbConnectorIds || {};
      const sharedConnectorId = kbGroupNode.data.config?.connectorInstanceId || 
                                kbGroupNode.data.config?.kbConnectorId;
      
      kbGroupNode.data.config.selectedKBs.forEach((kbId: string) => {
        // Each KB is a record group - convert to knowledge format
        // KBs ARE record groups, so:
        // - connectorId: KB connector instance ID from KB document (e.g., "knowledgeBase_orgId")
        // - filters.recordGroups: Array containing the KB ID (record group ID)
        // - filters.records: Optional array of specific record IDs within the KB
        
        // Get connectorId for this specific KB (from KB document's connectorId field)
        // Should be stored in kbConnectorIds[kbId] when KB is added to the group
        let connectorId = kbConnectorIds[kbId] || sharedConnectorId;
        if (!connectorId) {
          // Fallback: Use kbId as connectorId - backend will resolve to KB connector instance from record group
          // This should ideally not happen if KB nodes properly store connectorId from KB document
          console.warn(`KB group: KB ${kbId} missing connectorId. Using KB ID as fallback.`);
          connectorId = kbId;
        }
        
        // Get filters for this specific KB if available
        const kbSpecificFilters = kbGroupNode.data.config?.kbFilters?.[kbId] || {};
        const filters = {
          ...kbSpecificFilters,
          recordGroups: [kbId], // KB ID is the record group ID - always include it
          records: kbSpecificFilters.records || [],
        };
        
        // Ensure recordGroups includes the kbId (should always be first)
        if (!filters.recordGroups.includes(kbId)) {
          filters.recordGroups = [kbId, ...filters.recordGroups];
        }
        
        // Add to knowledge format - use connectorId from KB document
        addKnowledgeSource(connectorId, filters, 'knowledge');
      });
    }
  }

  const agentCoreNode = nodes.find((node) => node.data.type === 'agent-core');
  
  // Convert internal toolset data to ToolsetReference format (with id)
  const toolsets: ToolsetReference[] = toolsetsInternal.map((ts) => ({
    id: ts.name, // Use name as id since it's unique per toolset
    name: ts.name,
    displayName: ts.displayName,
    type: ts.type,
    tools: ts.tools,
  }));
  
  // Convert internal knowledge data to KnowledgeReference format (with id)
  const knowledge: KnowledgeReference[] = knowledgeInternal.map((k, index) => ({
    id: k.connectorId || `knowledge-${index}`,
    connectorId: k.connectorId,
    filters: k.filters,
  }));
  
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
    instructions:
      agentCoreNode?.data.config?.instructions ||
      currentAgent?.instructions ||
      undefined,
    toolsets, // ToolsetReference[] with id
    knowledge, // KnowledgeReference[] with id (includes both apps and KBs)
    models,
    tags: currentAgent?.tags || ['flow-based', 'visual-workflow'],
    shareWithOrg: shareWithOrg !== undefined ? shareWithOrg : (currentAgent?.shareWithOrg ?? false),
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

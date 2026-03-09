// src/sections/qna/agents/hooks/useNodeTemplates.ts
import { useMemo } from 'react';
import brainIcon from '@iconify-icons/mdi/brain';
import chatIcon from '@iconify-icons/mdi/chat';
import collectionIcon from '@iconify-icons/mdi/folder-multiple';
import emailIcon from '@iconify-icons/mdi/email';
import apiIcon from '@iconify-icons/mdi/api';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import replyIcon from '@iconify-icons/mdi/reply';
import type { Connector } from 'src/sections/accountdetails/connectors/types/types';
import {
  groupToolsByApp,
  getAppDisplayName,
  getAppIcon,
  truncateText,
  normalizeDisplayName,
} from '../../utils/agent';
import type { UseAgentBuilderNodeTemplatesReturn, NodeTemplate } from '../../types/agent';

export const useAgentBuilderNodeTemplates = (
  availableTools: any[],
  availableModels: any[],
  availableKnowledgeBases: any[],
  activeAgentConnectors: Connector[],
  configuredConnectors: Connector[]
): UseAgentBuilderNodeTemplatesReturn => {
  // configuredConnectors is now passed as a parameter to avoid redundant API calls
  
  const nodeTemplates: NodeTemplate[] = useMemo(() => {
    const groupedTools = groupToolsByApp(availableTools);
    const allConnectors = [...configuredConnectors];
    
    // Create dynamic app memory nodes from connector data
    const dynamicAppKnowledgeNodes = allConnectors.map(connector => ({
      type: `app-${connector.name.toLowerCase().replace(/\s+/g, '-')}`,
      label: normalizeDisplayName(connector.name),
      description: `Connect to ${connector.name} data and content`,
      icon: connector.iconPath, // Will be overridden by dynamic icon in sidebar
      defaultConfig: {
        appName: connector.name.toUpperCase(),
        type: connector.type,
        appDisplayName: connector.name,
        searchScope: connector.scope,
        iconPath: connector.iconPath,
        scope: connector.scope,
      },
      inputs: ['query'],
      outputs: ['context'],
      category: 'knowledge' as const,
    }));

    // Create connector instance nodes for both Knowledge and Tools sections
    // Each connector instance will be shown in both sections with appropriate categorization
    const connectorGroupNodes = configuredConnectors.map(connector => ({
      type: `connector-group-${connector._key}`,
      label: normalizeDisplayName(connector.name),
      description: `${connector.type} connector instance - Use in Tools or Knowledge`,
      icon: collectionIcon, // Will be overridden by dynamic icon in sidebar
      defaultConfig: {
        id: connector._key,
        name: connector.name,
        type: connector.type,
        appGroup: connector.appGroup,
        authType: connector.authType,
        iconPath: connector.iconPath,
        scope: connector.scope,
      },
      inputs: ['query', 'tools'], // Can be used for both Knowledge and Tools
      outputs: ['context', 'actions'], // Outputs depend on usage
      category: 'connectors' as const,
    }));
    
    const templates: NodeTemplate[] = [
      // Agent Node (central orchestrator)
      {
        type: 'agent-core',
        label: normalizeDisplayName('Agent'),
        description: 'Orchestrates tools, knowledge, and multiple LLMs',
        icon: sparklesIcon,
        defaultConfig: {
          systemPrompt: 'You are a helpful assistant.',
          instructions: '',
          startMessage: 'Hello! I am ready to assist you. How can I help you today?',
          routing: 'auto',
          allowMultipleLLMs: true,
        },
        inputs: ['input', 'actions', 'knowledge', 'llms'],
        outputs: ['response'],
        category: 'agent',
      },
      // Input Nodes
      {
        type: 'user-input',
        label: normalizeDisplayName('User Input'),
        description: 'Receives user messages and queries',
        icon: chatIcon,
        defaultConfig: { placeholder: 'Enter your message...', inputType: 'text' },
        inputs: [],
        outputs: ['message'],
        category: 'inputs',
      },
      // LLM Nodes - Generated from available models
      ...availableModels.map((model: any) => {
        const modelName = model.modelName || 'Unknown Model';
        const modelFriendlyName = model.modelFriendlyName;
        // Use friendly name for display, fallback to modelName
        const displayName = modelFriendlyName || modelName;
        const normalizedName = displayName.trim();
        
        // Create unique type identifier using provider and modelName to avoid conflicts
        const uniqueTypeId = `${model.provider}-${model.modelKey}-${model.modelName}`.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();

        return {
          type: `llm-${uniqueTypeId}`,
          label: normalizedName,
          description: `${model.provider} AI model for text generation`,
          icon: brainIcon,
          defaultConfig: {
            modelKey: model.modelKey,
            modelName: model.modelName,
            modelFriendlyName,
            provider: model.provider,
            modelType: model.modelType,
            temperature: 0.7,
            maxTokens: 1000,
            isMultimodal: model.isMultimodal || false,
            isDefault: model.isDefault || false,
            isReasoning: model.isReasoning || false,
          },
          inputs: [],
          outputs: ['response'],
          category: 'llm' as const,
        };
      }),

      // Grouped Tool Nodes - One node per app with all tools
      ...Object.entries(groupedTools).map(([appName, tools]) => ({
        type: `tool-group-${appName}`,
        label: normalizeDisplayName(`${getAppDisplayName(appName)} Tools`),
        description: `All ${getAppDisplayName(appName)} tools and actions`,
        icon: getAppIcon(appName),
        defaultConfig: {
          appName,
          appDisplayName: getAppDisplayName(appName),
          tools: tools.map((tool) => ({
            toolId: tool.tool_id,
            fullName: tool.full_name,
            toolName: tool.tool_name,
            description: tool.description,
            parameters: tool.parameters || [],
          })),
          selectedTools: tools.map((tool) => tool.tool_id), // All tools selected by default
        },
        inputs: ['input'],
        outputs: ['output'],
        category: 'tools' as const,
      })),

      // Individual Tool Nodes (for granular control)
      ...availableTools.map((tool) => ({
        type: `tool-${tool.tool_id}`,
        label: normalizeDisplayName(tool.tool_name.replace(/_/g, ' ')),
        description: tool.description || `${tool.app_name} tool`,
        icon: getAppIcon(tool.app_name),
        defaultConfig: {
          toolId: tool.tool_id,
          fullName: tool.full_name,
          appName: tool.app_name,
          parameters: tool.parameters || [],
        },
        inputs: ['input'],
        outputs: ['output'],
        category: 'tools' as const,
      })),

      // App Memory Group Node - For connecting to all apps (dynamic)
      {
        type: 'app-group',
        label: 'Apps',
        description: `Connect to data from integrated applications (${allConnectors.length} apps)`,
        icon: apiIcon,
        defaultConfig: {
          apps: allConnectors.map(connector => ({
            id: connector._key, // Connector instance ID
            name: connector.name,
            type: connector.type,
            displayName: connector.name,
            scope: connector.scope,
            iconPath: connector.iconPath,
          })),
          selectedApps: allConnectors.slice(0, 3).map(connector => connector._key), // Default to first 3 apps - use connector instance IDs
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge' as const,
      },

      // Individual App Memory Nodes - Dynamic from connector data
      ...dynamicAppKnowledgeNodes,
      ...connectorGroupNodes,

      // Knowledge Base Group Node
      {
        type: 'kb-group',
        label: 'Collections',
        description: `All collections (${availableKnowledgeBases.length} collections)`,
        icon: collectionIcon,
        defaultConfig: {
          knowledgeBases: availableKnowledgeBases.map((k) => ({ 
            id: k.id, 
            name: k.name,
            connectorId: k.connectorId, // KB connector instance ID from KB document
          })),
          selectedKBs: availableKnowledgeBases.map((kb) => kb.id), // All KBs selected by default
          // Store connectorId mapping for each KB
          kbConnectorIds: availableKnowledgeBases.reduce((acc, kb) => {
            acc[kb.id] = kb.connectorId; // Map KB ID to its connector instance ID
            return acc;
          }, {} as Record<string, string>),
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge' as const,
      },

      // Individual Knowledge Base Nodes (for granular control)
      ...availableKnowledgeBases.map((kb) => ({
        type: `kb-${kb.id}`,
        label: `${truncateText(kb.name, 20)}`,
        description: truncateText(`Collection for information retrieval`, 40),
        icon: collectionIcon,
        defaultConfig: {
          kbId: kb.id, // KB ID (record group ID)
          kbName: kb.name,
          connectorInstanceId: kb.connectorId, // KB connector instance ID from KB document (e.g., "knowledgeBase_orgId")
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge' as const,
      })),

      // Output Nodes
      {
        type: 'chat-response',
        label: 'Chat Response',
        description: 'Send response to user in chat interface',
        icon: replyIcon,
        defaultConfig: { format: 'text', includeMetadata: false },
        inputs: ['response'],
        outputs: [],
        category: 'outputs',
      },
    ];

    return templates;
  }, [availableTools, availableModels, availableKnowledgeBases, configuredConnectors]);

  return { nodeTemplates };
};

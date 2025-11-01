// src/sections/qna/agents/hooks/useNodeTemplates.ts
import { useMemo } from 'react';
import brainIcon from '@iconify-icons/mdi/brain';
import chatIcon from '@iconify-icons/mdi/chat';
import databaseIcon from '@iconify-icons/mdi/database';
import emailIcon from '@iconify-icons/mdi/email';
import apiIcon from '@iconify-icons/mdi/api';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import replyIcon from '@iconify-icons/mdi/reply';
import { useConnectors } from '../../../../accountdetails/connectors/context';
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
  availableKnowledgeBases: any[]
): UseAgentBuilderNodeTemplatesReturn => {
  // Get connector data from the hook
  const { activeConnectors } = useConnectors();
  
  const nodeTemplates: NodeTemplate[] = useMemo(() => {
    const groupedTools = groupToolsByApp(availableTools);
    const allConnectors = [...activeConnectors];
    
    // Create dynamic app memory nodes from connector data
    const dynamicAppKnowledgeNodes = allConnectors.map(connector => ({
      type: `app-${connector.name.toLowerCase().replace(/\s+/g, '-')}`,
      label: normalizeDisplayName(connector.name),
      description: `Connect to ${connector.name} data and content`,
      icon: databaseIcon, // Will be overridden by dynamic icon in sidebar
      defaultConfig: {
        appName: connector.name.toUpperCase(),
        appDisplayName: connector.name,
        searchScope: 'all',
      },
      inputs: ['query'],
      outputs: ['context'],
      category: 'knowledge' as const,
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
        const normalizedName = modelName
          .replace(/[^a-zA-Z0-9]/g, ' ')
          .replace(/\s+/g, ' ')
          .trim();
        
        // Create unique type identifier using provider and modelName to avoid conflicts
        const uniqueTypeId = `${model.provider}-${modelName}`.replace(/[^a-zA-Z0-9]/g, '-').toLowerCase();
        
        return {
          type: `llm-${uniqueTypeId}`,
          label: normalizeDisplayName(normalizedName),
          description: `${model.provider} AI model for text generation`,
          icon: brainIcon,
          defaultConfig: {
            modelKey: model.modelKey,
            modelName: model.modelName,
            provider: model.provider,
            modelType: model.modelType,
            temperature: 0.7,
            maxTokens: 1000,
            isMultimodal: model.isMultimodal || false,
            isDefault: model.isDefault || false,
            isReasoning: model.isReasoning || false,
          },
          inputs: ['prompt', 'context'],
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
            name: connector.name,
            type: connector.name.toUpperCase(),
            displayName: connector.name,
          })),
          selectedApps: allConnectors.slice(0, 3).map(connector => connector.name.toUpperCase()), // Default to first 3 apps
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge' as const,
      },

      // Individual App Memory Nodes - Dynamic from connector data
      ...dynamicAppKnowledgeNodes,

      // Knowledge Base Group Node
      {
        type: 'kb-group',
        label: 'Knowledge Bases',
        description: `All knowledge bases (${availableKnowledgeBases.length} KBs)`,
        icon: databaseIcon,
        defaultConfig: {
          knowledgeBases: availableKnowledgeBases.map((k) => ({ id: k.id, name: k.name })),
          selectedKBs: availableKnowledgeBases.map((kb) => kb.id), // All KBs selected by default
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge' as const,
      },

      // Individual Knowledge Base Nodes (for granular control)
      ...availableKnowledgeBases.map((kb) => ({
        type: `kb-${kb.id}`,
        label: `KB: ${truncateText(kb.name, 20)}`,
        description: truncateText(`Knowledge base for information retrieval`, 40),
        icon: databaseIcon,
        defaultConfig: {
          kbId: kb.id,
          kbName: kb.name,
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
  }, [availableTools, availableModels, availableKnowledgeBases, activeConnectors]);

  return { nodeTemplates };
};

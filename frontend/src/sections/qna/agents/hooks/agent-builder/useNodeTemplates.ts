// src/sections/qna/agents/hooks/useNodeTemplates.ts
import { useMemo } from 'react';
import brainIcon from '@iconify-icons/mdi/brain';
import chatIcon from '@iconify-icons/mdi/chat';
import databaseIcon from '@iconify-icons/mdi/database';
import emailIcon from '@iconify-icons/mdi/email';
import apiIcon from '@iconify-icons/mdi/api';
import sparklesIcon from '@iconify-icons/mdi/auto-awesome';
import replyIcon from '@iconify-icons/mdi/reply';
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
  const nodeTemplates: NodeTemplate[] = useMemo(() => {
    const groupedTools = groupToolsByApp(availableTools);
    const templates: NodeTemplate[] = [
      // Agent Node (central orchestrator)
      {
        type: 'agent-core',
        label: normalizeDisplayName('Agent'),
        description: 'Orchestrates tools, memory, and multiple LLMs',
        icon: sparklesIcon,
        defaultConfig: {
          systemPrompt: 'You are a helpful assistant.',
          startMessage: 'Hello! I am ready to assist you. How can I help you today?',
          routing: 'auto',
          allowMultipleLLMs: true,
        },
        inputs: ['input', 'actions', 'memory', 'llms'],
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
        return {
          type: `llm-${model.modelKey || modelName.replace(/[^a-zA-Z0-9]/g, '-')}`,
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

      // App Memory Group Node - For connecting to all apps
      {
        type: 'app-group',
        label: 'Apps',
        description: 'Connect to data from integrated applications',
        icon: apiIcon,
        defaultConfig: {
          apps: [
            { name: 'Slack', type: 'SLACK', displayName: 'Slack' },
            { name: 'Gmail', type: 'GMAIL', displayName: 'Gmail' },
            { name: 'Google Drive', type: 'GOOGLE_DRIVE', displayName: 'Google Drive' },
            { name: 'Google Workspace', type: 'GOOGLE_WORKSPACE', displayName: 'Google Workspace' },
            { name: 'OneDrive', type: 'ONEDRIVE', displayName: 'OneDrive' },
            { name: 'Jira', type: 'JIRA', displayName: 'Jira' },
            { name: 'Confluence', type: 'CONFLUENCE', displayName: 'Confluence' },
            { name: 'GitHub', type: 'GITHUB', displayName: 'GitHub' },
          ],
          selectedApps: ['SLACK', 'GMAIL', 'GOOGLE_DRIVE'], // Default selected apps
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },

      // Individual App Memory Nodes - For connecting to specific app data
      {
        type: 'app-slack',
        label: normalizeDisplayName('Slack'),
        description: 'Connect to Slack messages and conversations',
        icon: chatIcon,
        defaultConfig: {
          appName: 'SLACK',
          appDisplayName: 'Slack',
          searchScope: 'all', // all, channels, dms
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },
      {
        type: 'app-gmail',
        label: normalizeDisplayName('Gmail'),
        description: 'Connect to Gmail emails and conversations',
        icon: emailIcon,
        defaultConfig: {
          appName: 'GMAIL',
          appDisplayName: 'Gmail',
          searchScope: 'all', // all, inbox, sent, drafts
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },
      {
        type: 'app-google-drive',
        label: 'Google Drive',
        description: 'Connect to Google Drive files and folders',
        icon: databaseIcon,
        defaultConfig: {
          appName: 'GOOGLE_DRIVE',
          appDisplayName: 'Google Drive',
          fileTypes: ['all'], // all, documents, images, etc.
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },
      {
        type: 'app-google-workspace',
        label: normalizeDisplayName('Google Workspace'),
        description: 'Connect to Google Workspace (Docs, Sheets, Drive)',
        icon: databaseIcon,
        defaultConfig: {
          appName: 'GOOGLE_WORKSPACE',
          appDisplayName: 'Google Workspace',
          services: ['drive', 'docs', 'sheets'], // which services to include
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },
      {
        type: 'app-onedrive',
        label: normalizeDisplayName('OneDrive'),
        description: 'Connect to OneDrive files and documents',
        icon: databaseIcon,
        defaultConfig: {
          appName: 'ONEDRIVE',
          appDisplayName: 'OneDrive',
          fileTypes: ['all'], // all, documents, images, etc.
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },
      {
        type: 'app-jira',
        label: normalizeDisplayName('Jira'),
        description: 'Connect to Jira issues and project data',
        icon: apiIcon,
        defaultConfig: {
          appName: 'JIRA',
          appDisplayName: 'Jira',
          searchScope: 'all', // all, issues, projects
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },
      {
        type: 'app-confluence',
        label: normalizeDisplayName('Confluence'),
        description: 'Connect to Confluence pages and spaces',
        icon: databaseIcon,
        defaultConfig: {
          appName: 'CONFLUENCE',
          appDisplayName: 'Confluence',
          searchScope: 'all', // all, pages, spaces
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },
      {
        type: 'app-github',
        label: normalizeDisplayName('GitHub'),
        description: 'Connect to GitHub repositories and issues',
        icon: apiIcon,
        defaultConfig: {
          appName: 'GITHUB',
          appDisplayName: 'GitHub',
          searchScope: 'all', // all, repos, issues
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'memory' as const,
      },

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
        category: 'memory' as const,
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
        category: 'memory' as const,
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
  }, [availableTools, availableModels, availableKnowledgeBases]);

  return { nodeTemplates };
};

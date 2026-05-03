'use client';

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { KnowledgeBaseForBuilder, AgentToolsListRow } from '../../types';
import type { AvailableLlmModel } from '@/chat/types';
import type { NodeTemplate } from '../types';
import {
  normalizeDisplayName,
  truncateText,
  getAppDisplayName,
  getAppIconName,
} from '../display-utils';

function groupToolsByApp(tools: AgentToolsListRow[]): Record<string, AgentToolsListRow[]> {
  const grouped: Record<string, AgentToolsListRow[]> = {};
  tools.forEach((tool) => {
    const app = tool.app_name || 'other';
    if (!grouped[app]) grouped[app] = [];
    grouped[app].push(tool);
  });
  return grouped;
}

export function useAgentBuilderNodeTemplates(
  availableTools: AgentToolsListRow[],
  availableModels: AvailableLlmModel[],
  availableKnowledgeBases: KnowledgeBaseForBuilder[],
  configuredConnectors: Connector[]
): { nodeTemplates: NodeTemplate[] } {
  const { t } = useTranslation();
  const nodeTemplates = useMemo(() => {
    const groupedTools = groupToolsByApp(availableTools);
    const dynamicAppKnowledgeNodes: NodeTemplate[] = configuredConnectors.map((connector) => ({
      type: `app-${connector.name.toLowerCase().replace(/\s+/g, '-')}`,
      label: normalizeDisplayName(connector.name),
      description: t('agentBuilder.connectorDataDescription', { name: connector.name }),
      icon: connector.iconPath || 'cloud',
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
      category: 'knowledge',
    }));

    const connectorGroupNodes: NodeTemplate[] = configuredConnectors
      .filter((c): c is Connector & { _key: string } => Boolean(c._key))
      .map((connector) => ({
      type: `connector-group-${connector._key}`,
      label: normalizeDisplayName(connector.name),
      description: t('agentBuilder.connectorGroupDescription', { type: connector.type }),
      icon: 'hub',
      defaultConfig: {
        id: connector._key,
        name: connector.name,
        type: connector.type,
        appGroup: connector.appGroup,
        authType: connector.authType,
        iconPath: connector.iconPath,
        scope: connector.scope,
      },
      inputs: ['query', 'tools'],
      outputs: ['context', 'actions'],
      category: 'connectors',
    }));

    const templates: NodeTemplate[] = [
      {
        type: 'agent-core',
        label: normalizeDisplayName(t('agentBuilder.coreNodeTitle')),
        description: t('agentBuilder.coreNodeTemplateDescription'),
        icon: 'auto_awesome',
        defaultConfig: {
          systemPrompt: t('agentBuilder.defaultSystemPrompt'),
          instructions: '',
          startMessage: t('agentBuilder.defaultStartMessage'),
          routing: 'auto',
          allowMultipleLLMs: true,
        },
        inputs: ['input', 'toolsets', 'knowledge', 'llms'],
        outputs: ['response'],
        category: 'agent',
      },
      {
        type: 'conditional-check',
        label: normalizeDisplayName('Condition Check'),
        description: t('agentBuilder.nodeDescConditional', { defaultValue: 'Evaluate the previous result and route to pass or fail path' }),
        icon: 'rule',
        defaultConfig: {
          mode: 'contains',
          expectedValue: 'done',
          caseSensitive: false,
          regexPattern: '',
          jsonPath: '',
          minLength: 0,
          maxLength: 0,
          passOnEmpty: false,
        },
        inputs: ['input'],
        outputs: ['pass', 'fail'],
        category: 'agent',
      },
      {
        type: 'user-input',
        label: normalizeDisplayName(t('agentBuilder.nodeLabelChatInput')),
        description: t('agentBuilder.nodeDescUserMessages'),
        icon: 'chat',
        defaultConfig: { placeholder: t('agentBuilder.nodeTemplateInputPlaceholder'), inputType: 'text' },
        inputs: [],
        outputs: ['message'],
        category: 'inputs',
      },
      ...availableModels.map((model) => {
        const displayName =
          model.modelFriendlyName?.trim() || model.modelName || t('agentBuilder.placeholderModel');
        const uniqueTypeId = `${model.provider}-${model.modelKey}-${model.modelName}`.replace(
          /[^a-zA-Z0-9]/g,
          '-'
        ).toLowerCase();
        return {
          type: `llm-${uniqueTypeId}`,
          label: displayName,
          description: t('agentBuilder.llmProviderModel', { provider: model.provider }),
          icon: 'psychology',
          defaultConfig: {
            modelKey: model.modelKey,
            modelName: model.modelName,
            modelFriendlyName: model.modelFriendlyName,
            provider: model.provider,
            modelType: model.modelType,
            isMultimodal: model.isMultimodal,
            isDefault: model.isDefault,
            isReasoning: model.isReasoning,
          },
          inputs: [],
          outputs: ['response'],
          category: 'llm' as const,
        };
      }),
      ...Object.entries(groupedTools).map(([appName, appTools]) => ({
        type: `tool-group-${appName}`,
        label: normalizeDisplayName(
          t('agentBuilder.paletteToolGroupLabel', { app: getAppDisplayName(appName) })
        ),
        description: t('agentBuilder.allToolsForAppDescription', { app: getAppDisplayName(appName) }),
        icon: getAppIconName(appName),
        defaultConfig: {
          appName,
          appDisplayName: getAppDisplayName(appName),
          tools: appTools.map((tool) => ({
            toolId: tool.tool_id,
            fullName: tool.full_name,
            toolName: tool.tool_name,
            description: tool.description,
            parameters: tool.parameters || [],
          })),
          selectedTools: appTools.map((tool) => tool.tool_id),
        },
        inputs: ['input'],
        outputs: ['output'],
        category: 'tools' as const,
      })),
      ...availableTools.map((tool) => ({
        type: `tool-${tool.tool_id}`,
        label: normalizeDisplayName(tool.tool_name.replace(/_/g, ' ')),
        description: tool.description || t('agentBuilder.toolInlineSuffix', { name: tool.app_name }),
        icon: getAppIconName(tool.app_name),
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
      {
        type: 'app-group',
        label: t('agentBuilder.groupApps'),
        description: t('agentBuilder.appGroupTemplateDescription', {
          count: configuredConnectors.length,
        }),
        icon: 'apps',
        defaultConfig: {
          apps: configuredConnectors.map((c) => ({
            id: c._key,
            name: c.name,
            type: c.type,
            displayName: c.name,
            scope: c.scope,
            iconPath: c.iconPath,
          })),
          selectedApps: configuredConnectors.map((c) => c._key).filter(Boolean) as string[],
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge',
      },
      ...dynamicAppKnowledgeNodes,
      ...connectorGroupNodes,
      {
        type: 'kb-group',
        label: t('agentBuilder.groupCollections'),
        description: t('agentBuilder.kbGroupTemplateDescription', {
          count: availableKnowledgeBases.length,
        }),
        icon: 'folder',
        defaultConfig: {
          knowledgeBases: availableKnowledgeBases.map((k) => ({
            id: k.id,
            name: k.name,
            connectorId: k.connectorId,
          })),
          selectedKBs: availableKnowledgeBases.map((kb) => kb.id),
          kbConnectorIds: availableKnowledgeBases.reduce<Record<string, string>>((acc, kb) => {
            acc[kb.id] = kb.connectorId;
            return acc;
          }, {}),
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge',
      },
      ...availableKnowledgeBases.map((kb) => ({
        type: `kb-${kb.id}`,
        label: truncateText(kb.name, 22),
        description: t('agentBuilder.kbNodeTemplateDescription'),
        icon: 'folder',
        defaultConfig: {
          kbId: kb.id,
          kbName: kb.name,
          connectorInstanceId: kb.connectorId,
        },
        inputs: ['query'],
        outputs: ['context'],
        category: 'knowledge' as const,
      })),
      {
        type: 'web-search',
        label: t('agentBuilder.webSearch'),
        description: t('Web Search'),
        icon: 'public',
        defaultConfig: {
          provider: '',
          providerKey: '',
          providerLabel: '',
        },
        inputs: ['query'],
        outputs: ['results'],
        category: 'tools',
      },
      {
        type: 'chat-response',
        label: t('agentBuilder.nodeLabelChatOutput'),
        description: t('agentBuilder.nodeTemplateChatResponseDesc'),
        icon: 'reply',
        defaultConfig: { format: 'text', includeMetadata: false },
        inputs: ['response'],
        outputs: [],
        category: 'outputs',
      },
    ];

    return templates;
  }, [availableTools, availableModels, availableKnowledgeBases, configuredConnectors, t]);

  return { nodeTemplates };
}

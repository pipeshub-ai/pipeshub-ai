'use client';

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, IconButton, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { FlowNodeData } from '../types';
import {
  AGENT_TOOLSET_FALLBACK_ICON,
  formattedProvider,
  normalizeDisplayName,
  resolveNodeHeaderIconErrorFallback,
  resolveNodeHeaderIconUrl,
} from '../display-utils';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import { NodeHandles } from './node-handles';
import { AgentCoreNode } from './agent-core-node';
import { ToolsetFlowNode } from './toolset-flow-node';
import { McpServerFlowNode } from './mcp-server-flow-node';
import { FLOW_NODE_CARD, FLOW_NODE_PANEL_BG, FLOW_NODE_WELL, getFlowNodeChrome } from '../flow-theme';

export type FlowNodeProps = {
  id: string;
  data: FlowNodeData;
  selected: boolean;
  onDelete?: (nodeId: string) => void;
  readOnly?: boolean;
};

function subtitleFor(
  data: FlowNodeData,
  t: (key: string, options?: Record<string, unknown>) => string
): string {
  if (data.type.startsWith('llm-')) {
    return formattedProvider((data.config?.provider as string) || '');
  }
  if (data.type === 'kb-group') {
    const count = ((data.config?.knowledgeBases as unknown[]) || []).length;
    if (!count) return t('agentBuilder.nodeKbGroupFallback');
    return t(
      count === 1 ? 'agentBuilder.nodeKbGroupSubtitleSingular' : 'agentBuilder.nodeKbGroupSubtitle',
      { count }
    );
  }
  if (data.type === 'app-group') {
    const count = ((data.config?.apps as unknown[]) || []).length;
    if (!count) return t('agentBuilder.nodeAppGroupFallback');
    return t(
      count === 1 ? 'agentBuilder.nodeAppGroupSubtitleSingular' : 'agentBuilder.nodeAppGroupSubtitle',
      { count }
    );
  }
  if (data.type.startsWith('kb-')) return t('agentBuilder.nodeKbSubtitle');
  if (data.type.startsWith('app-')) return t('agentBuilder.nodeAppSubtitle');
  return data.description || '';
}

function NodeCardShell(props: {
  selected: boolean;
  header: React.ReactNode;
  body?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const { selected, header, body, children } = props;
  return (
    <Box
      className="flow-node-surface"
      style={{
        width: 276,
        boxSizing: 'border-box',
        borderRadius: FLOW_NODE_CARD.radius,
        border: selected ? '1px solid var(--gray-11)' : FLOW_NODE_CARD.borderIdle,
        background: FLOW_NODE_PANEL_BG,
        boxShadow: selected ? FLOW_NODE_CARD.shadowSelected : FLOW_NODE_CARD.shadow,
        position: 'relative',
        overflow: 'visible',
      }}
    >
      <Box
        style={{
          borderBottom: '1px solid var(--agent-flow-node-border)',
          background: 'var(--agent-flow-node-header-bg)',
        }}
      >
        {header}
      </Box>
      {body ? (
        <Box px="3" py="2" style={{ background: FLOW_NODE_PANEL_BG }}>
          {body}
        </Box>
      ) : null}
      {children}
    </Box>
  );
}

export const FlowNode = React.memo(function FlowNode({
  id,
  data,
  selected,
  onDelete,
  readOnly,
}: FlowNodeProps) {
  const { t } = useTranslation();
  const chrome = useMemo(() => getFlowNodeChrome(data.type), [data.type]);

  if (data.type === 'agent-core') {
    return <AgentCoreNode id={id} data={data} selected={selected} readOnly={readOnly} />;
  }

  if (data.type.startsWith('toolset-')) {
    return (
      <ToolsetFlowNode id={id} data={data} selected={selected} readOnly={readOnly} onDelete={onDelete} />
    );
  }

  if (data.type.startsWith('mcp-server-')) {
    return (
      <McpServerFlowNode id={id} data={data} selected={selected} readOnly={readOnly} onDelete={onDelete} />
    );
  }

  const subtitle =
    data.type === 'user-input'
      ? t('agentBuilder.nodeDescUserMessages')
      : data.type === 'chat-response'
        ? t('agentBuilder.nodeDescChatReply')
        : subtitleFor(data, t);
  const headerLabel =
    data.type === 'user-input'
      ? t('agentBuilder.nodeLabelChatInput')
      : data.type === 'chat-response'
        ? t('agentBuilder.nodeLabelChatOutput')
        : normalizeDisplayName(data.label);
  const icon = data.icon as string | undefined;
  const trimmedIcon = typeof icon === 'string' ? icon.trim() : '';
  const headerIconUrl = resolveNodeHeaderIconUrl(data);
  const isIconUrl = Boolean(headerIconUrl);
  const materialIconName =
    trimmedIcon && !trimmedIcon.startsWith('/') && !trimmedIcon.startsWith('http') ? trimmedIcon : 'widgets';
  const headerIconErrorFallback = resolveNodeHeaderIconErrorFallback(data);

  let groupBody: React.ReactNode = null;
  if (data.type === 'app-group') {
    const apps = (data.config?.apps as Array<{ id?: string; displayName?: string; name?: string; iconPath?: string }>) || [];
    if (apps.length > 0) {
      const shown = apps.slice(0, 5);
      groupBody = (
        <Box
          style={{
            borderTop: '1px solid var(--agent-flow-node-border)',
            marginTop: 4,
            paddingTop: 6,
            display: 'flex',
            flexDirection: 'column',
            gap: 3,
          }}
        >
          {shown.map((app, i) => (
            <Flex
              key={i}
              align="center"
              style={{
                minWidth: 0,
                gap: 8,
                background: FLOW_NODE_WELL.background,
                border: FLOW_NODE_WELL.border,
                borderRadius: FLOW_NODE_WELL.radius,
                padding: '5px 8px',
              }}
            >
              {app.iconPath ? (
                <ThemeableAssetIcon
                  {...themeableAssetIconPresets.flowNodeWell}
                  src={app.iconPath}
                  size={12}
                  fallbackSrc={AGENT_TOOLSET_FALLBACK_ICON}
                />
              ) : (
                <MaterialIcon name="cloud" size={12} color="var(--agent-flow-text-muted)" />
              )}
              <Text size="1" style={{ color: 'var(--agent-flow-text)', lineHeight: 1.35, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {app.displayName || app.name || ''}
              </Text>
            </Flex>
          ))}
          {apps.length > 5 ? (
            <Badge size="1" variant="soft" color="gray" highContrast>
              {t('agentBuilder.moreItems', { count: apps.length - 5 })}
            </Badge>
          ) : null}
        </Box>
      );
    }
  } else if (data.type === 'kb-group') {
    const kbs = (data.config?.knowledgeBases as Array<{ id: string; name: string }>) || [];
    if (kbs.length > 0) {
      const shown = kbs.slice(0, 5);
      groupBody = (
        <Box
          style={{
            borderTop: '1px solid var(--agent-flow-node-border)',
            marginTop: 4,
            paddingTop: 6,
            display: 'flex',
            flexDirection: 'column',
            gap: 3,
          }}
        >
          {shown.map((kb, i) => (
            <Flex
              key={i}
              align="center"
              style={{
                minWidth: 0,
                gap: 8,
                background: FLOW_NODE_WELL.background,
                border: FLOW_NODE_WELL.border,
                borderRadius: FLOW_NODE_WELL.radius,
                padding: '5px 8px',
              }}
            >
              <MaterialIcon name="folder_open" size={12} color="var(--agent-flow-text-muted)" />
              <Text size="1" style={{ color: 'var(--agent-flow-text)', lineHeight: 1.35, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {kb.name}
              </Text>
            </Flex>
          ))}
          {kbs.length > 5 ? (
            <Badge size="1" variant="soft" color="gray" highContrast>
              {t('agentBuilder.moreItems', { count: kbs.length - 5 })}
            </Badge>
          ) : null}
        </Box>
      );
    }
  }

  return (
    <div className="flow-node-card">
      <NodeCardShell
        selected={selected}
        body={groupBody}
        header={
          <Flex align="center" justify="between" gap="2" px="3" py="2">
            <Flex align="center" gap="2" style={{ minWidth: 0 }}>
              <Flex
                align="center"
                justify="center"
                style={{ flexShrink: 0, lineHeight: 0 }}
                aria-hidden
              >
                {isIconUrl ? (
                  <ThemeableAssetIcon
                    {...themeableAssetIconPresets.flowNodeHeader}
                    src={headerIconUrl}
                    size={22}
                    color={chrome.iconColor}
                    fallbackSrc={headerIconErrorFallback}
                  />
                ) : (
                  <MaterialIcon name={materialIconName} size={22} color={chrome.iconColor} />
                )}
              </Flex>
              <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                <Text
                  weight="medium"
                  style={{
                    wordBreak: 'break-word',
                    color: 'var(--agent-flow-text)',
                    lineHeight: '20px',
                    fontSize: 14,
                  }}
                >
                  {headerLabel}
                </Text>
                {subtitle ? (
                  <Text
                    size="1"
                    style={{
                      display: 'block',
                      color: 'var(--agent-flow-text-muted)',
                      lineHeight: '16px',
                    }}
                  >
                    {subtitle}
                  </Text>
                ) : null}
              </Flex>
            </Flex>
            {!readOnly && data.type !== 'user-input' && data.type !== 'chat-response' && onDelete ? (
              <span className="flow-node-delete" style={{ flexShrink: 0 }}>
                <IconButton
                  size="1"
                  variant="ghost"
                  color="gray"
                  onClick={() => onDelete(id)}
                  aria-label={t('agentBuilder.removeNodeAriaLabel')}
                >
                  <MaterialIcon name="close" size={18} color="var(--agent-flow-text)" />
                </IconButton>
              </span>
            ) : null}
          </Flex>
        }
      >
        <NodeHandles data={data} />
      </NodeCardShell>
    </div>
  );
});

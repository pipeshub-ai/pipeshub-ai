'use client';

import React, { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, IconButton, Badge, Dialog, Button, TextField, Select, Switch } from '@radix-ui/themes';
import { useReactFlow } from '@xyflow/react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import type { FlowNodeData } from '../types';
import {
  AGENT_TOOLSET_FALLBACK_ICON,
  formattedProvider,
  normalizeDisplayName,
  resolveNodeConnectorType,
  resolveNodeHeaderIconErrorFallback,
  resolveNodeHeaderIconUrl,
} from '../display-utils';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import { NodeHandles } from './node-handles';
import { AgentCoreNode } from './agent-core-node';
import { ToolsetFlowNode } from './toolset-flow-node';
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
  const { setNodes } = useReactFlow();
  const chrome = useMemo(() => getFlowNodeChrome(data.type), [data.type]);
  const [conditionOpen, setConditionOpen] = useState(false);
  const [conditionConfig, setConditionConfig] = useState<Record<string, unknown>>({});

  const conditionMode = String(data.config?.mode ?? 'contains');
  const modeLabel = useMemo(() => {
    const modeLabels: Record<string, string> = {
      contains: 'Contains',
      not_contains: 'Not Contains',
      equals: 'Equals',
      not_equals: 'Not Equals',
      starts_with: 'Starts With',
      ends_with: 'Ends With',
      regex: 'Regex',
      min_length: 'Min Length',
      max_length: 'Max Length',
      is_empty: 'Is Empty',
      not_empty: 'Not Empty',
      json_path_equals: 'JSON Path',
    };
    return modeLabels[conditionMode] ?? conditionMode ?? 'Contains';
  }, [conditionMode]);

  const openConditionEditor = useCallback(() => {
    setConditionConfig({
      mode: String(data.config?.mode ?? 'contains'),
      expectedValue: String(data.config?.expectedValue ?? ''),
      regexPattern: String(data.config?.regexPattern ?? ''),
      jsonPath: String(data.config?.jsonPath ?? ''),
      minLength: Number(data.config?.minLength ?? 0),
      maxLength: Number(data.config?.maxLength ?? 0),
      caseSensitive: Boolean(data.config?.caseSensitive ?? false),
      passOnEmpty: Boolean(data.config?.passOnEmpty ?? false),
    });
    setConditionOpen(true);
  }, [data.config]);

  const saveConditionConfig = useCallback(() => {
    const nextMode = String(conditionConfig.mode ?? 'contains');
    const nextConfig: Record<string, unknown> = {
      ...data.config,
      mode: nextMode,
      expectedValue: String(conditionConfig.expectedValue ?? ''),
      regexPattern: String(conditionConfig.regexPattern ?? ''),
      jsonPath: String(conditionConfig.jsonPath ?? ''),
      minLength: Math.max(0, Number(conditionConfig.minLength ?? 0) || 0),
      maxLength: Math.max(0, Number(conditionConfig.maxLength ?? 0) || 0),
      caseSensitive: Boolean(conditionConfig.caseSensitive ?? false),
      passOnEmpty: Boolean(conditionConfig.passOnEmpty ?? false),
    };

    setNodes((nodes) =>
      nodes.map((node) =>
        node.id === id
          ? {
              ...node,
              data: {
                ...node.data,
                config: nextConfig,
              },
            }
          : node
      )
    );
    setConditionOpen(false);
  }, [conditionConfig, data.config, id, setNodes]);

  const showExpectedValue = !['is_empty', 'not_empty', 'min_length', 'max_length'].includes(
    String(conditionConfig.mode ?? conditionMode)
  );
  const showRegexPattern = String(conditionConfig.mode ?? conditionMode) === 'regex';
  const showJsonPath = String(conditionConfig.mode ?? conditionMode) === 'json_path_equals';
  const showMinLength = String(conditionConfig.mode ?? conditionMode) === 'min_length';
  const showMaxLength = String(conditionConfig.mode ?? conditionMode) === 'max_length';
  const showCaseSensitive = ['contains', 'not_contains', 'equals', 'not_equals', 'starts_with', 'ends_with'].includes(
    String(conditionConfig.mode ?? conditionMode)
  );

  if (data.type === 'agent-core') {
    return <AgentCoreNode id={id} data={data} selected={selected} readOnly={readOnly} />;
  }

  if (data.type === 'conditional-check') {
    return (
      <>
        <div className="flow-node-card">
          <NodeCardShell
            selected={selected}
            body={
              <Flex align="center" gap="2" wrap="wrap">
                <Badge size="1" variant="soft" color="gray" highContrast>
                  {`Mode: ${modeLabel}`}
                </Badge>
                {String(data.config?.passOnEmpty ?? false) === 'true' || data.config?.passOnEmpty === true ? (
                  <Badge size="1" variant="surface" color="gray">
                    Pass on empty
                  </Badge>
                ) : null}
              </Flex>
            }
            header={
              <Flex align="center" justify="between" gap="2" px="3" py="2">
                <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
                  <Flex
                    align="center"
                    justify="center"
                    style={{
                      flexShrink: 0,
                      lineHeight: 0,
                      width: 32,
                      height: 32,
                      borderRadius: 'var(--radius-2)',
                      background: 'var(--gray-a2)',
                      border: '1px solid var(--gray-6)',
                    }}
                    aria-hidden
                  >
                    <MaterialIcon name="rule" size={22} color={chrome.iconColor} />
                  </Flex>
                  <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                    <Text
                      weight="medium"
                      style={{
                        wordBreak: 'break-word',
                        color: 'var(--agent-flow-text)',
                        lineHeight: '20px',
                        fontSize: 14,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      Condition Check
                    </Text>
                    <Text
                      size="1"
                      style={{
                        display: 'block',
                        color: 'var(--agent-flow-text-muted)',
                        lineHeight: '16px',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {`Mode: ${modeLabel}`}
                    </Text>
                  </Flex>
                </Flex>
                <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
                  {!readOnly ? (
                    <IconButton
                      size="1"
                      variant="soft"
                      color="gray"
                      onClick={openConditionEditor}
                      aria-label="Edit condition"
                    >
                      <MaterialIcon name="edit" size={16} color="var(--agent-flow-text)" />
                    </IconButton>
                  ) : null}
                  {!readOnly && onDelete ? (
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
              </Flex>
            }
          >
            <NodeHandles data={data} />
          </NodeCardShell>
        </div>

        <Dialog.Root open={conditionOpen} onOpenChange={setConditionOpen}>
          <Dialog.Content style={{ maxWidth: 560 }}>
            <Dialog.Title>Condition Check</Dialog.Title>
            <Flex direction="column" gap="3" mt="2">
              <Box>
                <Text size="2" weight="bold" mb="1" style={{ display: 'block' }}>
                  Mode
                </Text>
                <Select.Root
                  value={String(conditionConfig.mode ?? conditionMode)}
                  onValueChange={(value) => setConditionConfig((prev) => ({ ...prev, mode: value }))}
                  size="2"
                >
                  <Select.Trigger style={{ width: '100%' }} />
                  <Select.Content>
                    <Select.Item value="contains">Contains text</Select.Item>
                    <Select.Item value="not_contains">Does not contain text</Select.Item>
                    <Select.Item value="equals">Equals text</Select.Item>
                    <Select.Item value="not_equals">Not equals text</Select.Item>
                    <Select.Item value="starts_with">Starts with</Select.Item>
                    <Select.Item value="ends_with">Ends with</Select.Item>
                    <Select.Item value="regex">Regex match</Select.Item>
                    <Select.Item value="min_length">Minimum length</Select.Item>
                    <Select.Item value="max_length">Maximum length</Select.Item>
                    <Select.Item value="is_empty">Is empty</Select.Item>
                    <Select.Item value="not_empty">Is not empty</Select.Item>
                    <Select.Item value="json_path_equals">JSON path equals</Select.Item>
                  </Select.Content>
                </Select.Root>
              </Box>

              {showExpectedValue ? (
                <Box>
                  <Text size="2" weight="bold" mb="1" style={{ display: 'block' }}>
                    Expected value
                  </Text>
                  <TextField.Root
                    size="2"
                    value={String(conditionConfig.expectedValue ?? '')}
                    onChange={(e) =>
                      setConditionConfig((prev) => ({ ...prev, expectedValue: e.target.value }))
                    }
                    placeholder="Enter value"
                  />
                </Box>
              ) : null}

              {showRegexPattern ? (
                <Box>
                  <Text size="2" weight="bold" mb="1" style={{ display: 'block' }}>
                    Regex pattern
                  </Text>
                  <TextField.Root
                    size="2"
                    value={String(conditionConfig.regexPattern ?? '')}
                    onChange={(e) =>
                      setConditionConfig((prev) => ({ ...prev, regexPattern: e.target.value }))
                    }
                    placeholder="score:\\s*(9|10)"
                  />
                </Box>
              ) : null}

              {showJsonPath ? (
                <Box>
                  <Text size="2" weight="bold" mb="1" style={{ display: 'block' }}>
                    JSON path
                  </Text>
                  <TextField.Root
                    size="2"
                    value={String(conditionConfig.jsonPath ?? '')}
                    onChange={(e) =>
                      setConditionConfig((prev) => ({ ...prev, jsonPath: e.target.value }))
                    }
                    placeholder="answer.status"
                  />
                </Box>
              ) : null}

              {showMinLength ? (
                <Box>
                  <Text size="2" weight="bold" mb="1" style={{ display: 'block' }}>
                    Minimum length
                  </Text>
                  <TextField.Root
                    type="number"
                    min={0}
                    size="2"
                    value={String(conditionConfig.minLength ?? 0)}
                    onChange={(e) =>
                      setConditionConfig((prev) => ({ ...prev, minLength: Number(e.target.value) || 0 }))
                    }
                  />
                </Box>
              ) : null}

              {showMaxLength ? (
                <Box>
                  <Text size="2" weight="bold" mb="1" style={{ display: 'block' }}>
                    Maximum length
                  </Text>
                  <TextField.Root
                    type="number"
                    min={0}
                    size="2"
                    value={String(conditionConfig.maxLength ?? 0)}
                    onChange={(e) =>
                      setConditionConfig((prev) => ({ ...prev, maxLength: Number(e.target.value) || 0 }))
                    }
                  />
                </Box>
              ) : null}

              {showCaseSensitive ? (
                <Flex align="center" justify="between">
                  <Text size="2">Case sensitive</Text>
                  <Switch
                    checked={Boolean(conditionConfig.caseSensitive ?? false)}
                    onCheckedChange={(value) =>
                      setConditionConfig((prev) => ({ ...prev, caseSensitive: value === true }))
                    }
                  />
                </Flex>
              ) : null}

              <Flex align="center" justify="between">
                <Text size="2">Pass on empty</Text>
                <Switch
                  checked={Boolean(conditionConfig.passOnEmpty ?? false)}
                  onCheckedChange={(value) =>
                    setConditionConfig((prev) => ({ ...prev, passOnEmpty: value === true }))
                  }
                />
              </Flex>

              <Flex gap="2" justify="end">
                <Dialog.Close>
                  <Button variant="soft" color="gray">
                    {t('action.cancel')}
                  </Button>
                </Dialog.Close>
                <Button onClick={saveConditionConfig}>{t('action.save')}</Button>
              </Flex>
            </Flex>
          </Dialog.Content>
        </Dialog.Root>
      </>
    );
  }

  if (data.type.startsWith('toolset-')) {
    return (
      <ToolsetFlowNode id={id} data={data} selected={selected} readOnly={readOnly} onDelete={onDelete} />
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
  const headerConnectorType = resolveNodeConnectorType(data);

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
                {headerConnectorType ? (
                  <ConnectorIcon type={headerConnectorType} size={22} color={chrome.iconColor} />
                ) : isIconUrl ? (
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

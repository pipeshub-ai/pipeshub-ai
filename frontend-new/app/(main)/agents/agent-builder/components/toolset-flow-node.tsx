'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, IconButton, Popover, Separator, Badge } from '@radix-ui/themes';
import { useReactFlow } from '@xyflow/react';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { FlowNodeData } from '../types';
import { AGENT_TOOLSET_FALLBACK_ICON, normalizeDisplayName } from '../display-utils';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import { NodeHandles } from './node-handles';
import { FLOW_NODE_CARD, FLOW_NODE_PANEL_BG, FLOW_NODE_WELL, getFlowNodeChrome } from '../flow-theme';

export type ToolsetFlowTool = {
  name: string;
  fullName: string;
  description?: string;
  toolsetName?: string;
};

function toolKey(t: ToolsetFlowTool): string {
  return t.fullName || t.name;
}

function mergeToolsUnique(a: ToolsetFlowTool[], b: ToolsetFlowTool[]): ToolsetFlowTool[] {
  const map = new Map<string, ToolsetFlowTool>();
  [...a, ...b].forEach((t) => {
    const k = toolKey(t);
    if (k) map.set(k, t);
  });
  return Array.from(map.values());
}

function toolTitle(t: ToolsetFlowTool): string {
  return t.name.replace(/_/g, ' ');
}

export function ToolsetFlowNode({
  id,
  data,
  selected,
  readOnly,
  onDelete,
}: {
  id: string;
  data: FlowNodeData;
  selected: boolean;
  readOnly?: boolean;
  onDelete?: (nodeId: string) => void;
}) {
  const { t } = useTranslation();
  const { setNodes } = useReactFlow();
  const chrome = useMemo(() => getFlowNodeChrome(data.type), [data.type]);

  const cfg = (data.config || {}) as Record<string, unknown>;
  const instanceName = String(cfg.instanceName ?? '').trim();
  const productLabel = String(cfg.displayName ?? '').trim() || String(cfg.toolsetName ?? '').trim();
  const primaryTitle = instanceName
    ? normalizeDisplayName(instanceName)
    : normalizeDisplayName(productLabel || data.label);
  const productLine = productLabel ? normalizeDisplayName(productLabel) : '';
  const secondarySubtitle =
    instanceName && productLine && primaryTitle !== productLine
      ? productLine
      : t('agentBuilder.toolsetNodeSubtitle');

  const tools = (cfg.tools as ToolsetFlowTool[]) || [];
  const availableTools = useMemo(() => {
    const c = (data.config || {}) as Record<string, unknown>;
    const tList = (c.tools as ToolsetFlowTool[]) || [];
    const avail = (c.availableTools as ToolsetFlowTool[]) || [];
    return avail.length > 0 ? avail : tList;
  }, [data.config]);

  const toolsToAdd = useMemo(
    () => availableTools.filter((a) => !tools.some((t) => toolKey(t) === toolKey(a))),
    [availableTools, tools]
  );

  const [addToolsOpen, setAddToolsOpen] = useState(false);

  useEffect(() => {
    if (toolsToAdd.length === 0) setAddToolsOpen(false);
  }, [toolsToAdd.length]);

  const isIconUrl = (icon: string | undefined) =>
    Boolean(icon && (icon.startsWith('/') || icon.startsWith('http')));

  const handleAddTool = useCallback(
    (tool: ToolsetFlowTool) => {
      setNodes((nodes) =>
        nodes.map((node) => {
          if (node.id !== id) return node;
          const c = (node.data.config || {}) as Record<string, unknown>;
          const cur = (c.tools as ToolsetFlowTool[]) || [];
          const sel = (c.selectedTools as string[]) || [];
          const key = tool.name;
          if (cur.some((t) => toolKey(t) === toolKey(tool))) return node;
          return {
            ...node,
            data: {
              ...node.data,
              config: {
                ...c,
                tools: [...cur, tool],
                selectedTools: sel.includes(key) ? sel : [...sel, key],
                availableTools: mergeToolsUnique((c.availableTools as ToolsetFlowTool[]) || [], [tool]),
              },
            },
          };
        })
      );
    },
    [id, setNodes]
  );

  const handleRemoveTool = useCallback(
    (toolName: string) => {
      setNodes((nodes) =>
        nodes.map((node) => {
          if (node.id !== id) return node;
          const c = (node.data.config || {}) as Record<string, unknown>;
          const cur = (c.tools as ToolsetFlowTool[]) || [];
          const sel = (c.selectedTools as string[]) || [];
          return {
            ...node,
            data: {
              ...node.data,
              config: {
                ...c,
                tools: cur.filter((t) => t.name !== toolName),
                selectedTools: sel.filter((s) => s !== toolName),
              },
            },
          };
        })
      );
    },
    [id, setNodes]
  );

  const icon = (data.icon as string) || (cfg.iconPath as string) || 'extension';

  return (
    <div className="flow-node-card">
    <Box
      className="flow-node-surface"
      style={{
        width: 340,
        maxWidth: 'min(360px, 92vw)',
        boxSizing: 'border-box',
        borderRadius: FLOW_NODE_CARD.radius,
        border: selected ? '1px solid var(--gray-11)' : FLOW_NODE_CARD.borderIdle,
        background: FLOW_NODE_PANEL_BG,
        boxShadow: selected ? FLOW_NODE_CARD.shadowSelected : FLOW_NODE_CARD.shadow,
        position: 'relative',
        overflow: 'visible',
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <NodeHandles data={data} />

      <Box
        style={{
          borderBottom: '1px solid var(--agent-flow-node-border)',
          background: 'var(--agent-flow-node-header-bg)',
        }}
      >
        <Flex align="center" justify="between" gap="2" px="3" py="2">
          <Flex align="center" gap="2" style={{ minWidth: 0 }}>
            <Flex align="center" justify="center" style={{ flexShrink: 0, lineHeight: 0 }} aria-hidden>
              {isIconUrl(icon) ? (
                <ThemeableAssetIcon
                  {...themeableAssetIconPresets.flowNodeHeader}
                  src={icon}
                  size={22}
                  color={chrome.iconColor}
                  fallbackSrc={AGENT_TOOLSET_FALLBACK_ICON}
                />
              ) : (
                <MaterialIcon name={icon} size={22} color={chrome.iconColor} />
              )}
            </Flex>
            <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
              <Text
                weight="medium"
                style={{ color: 'var(--agent-flow-text)', lineHeight: '20px', fontSize: 14 }}
              >
                {primaryTitle}
              </Text>
              <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', lineHeight: '16px' }}>
                {secondarySubtitle}
              </Text>
            </Flex>
          </Flex>
          {!readOnly && onDelete ? (
            <span className="flow-node-delete" style={{ flexShrink: 0 }}>
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(id);
                }}
                aria-label={t('agentBuilder.removeNodeAriaLabel')}
              >
                <MaterialIcon name="close" size={18} color="var(--agent-flow-text)" />
              </IconButton>
            </span>
          ) : null}
        </Flex>
      </Box>

      <Box px="3" py="3" style={{ background: FLOW_NODE_PANEL_BG }}>
        <Flex align="center" justify="between" gap="2" mb="2">
          <Flex align="center" gap="2">
            <MaterialIcon name="build" size={16} color="var(--agent-flow-text-muted)" />
            <Text size="1" weight="medium" style={{ color: 'var(--agent-flow-text)' }}>
              {t('agentBuilder.toolsLabel')}
            </Text>
            <Badge size="1" variant="soft" color="gray" highContrast>
              {tools.length}
            </Badge>
          </Flex>
          {!readOnly && toolsToAdd.length > 0 ? (
            <Popover.Root open={addToolsOpen} onOpenChange={setAddToolsOpen}>
              <Popover.Trigger>
                <IconButton
                  type="button"
                  size="1"
                  variant="soft"
                  color="gray"
                  className="toolset-flow-add-tools"
                  aria-label={t('agentBuilder.addToolsAriaLabel')}
                  aria-expanded={addToolsOpen}
                  onClick={(e) => e.stopPropagation()}
                >
                  <MaterialIcon name="add" size={18} color="var(--agent-flow-text)" />
                </IconButton>
              </Popover.Trigger>
              <Popover.Content
                side="bottom"
                align="end"
                sideOffset={4}
                collisionPadding={12}
                onClick={(e) => e.stopPropagation()}
                style={{
                  padding: 0,
                  width: 248,
                  maxWidth: 'min(248px, calc(100vw - 20px))',
                  borderRadius: 'var(--radius-2)',
                  border: '1px solid var(--agent-flow-node-border)',
                  backgroundColor: FLOW_NODE_PANEL_BG,
                  boxShadow: FLOW_NODE_CARD.shadow,
                  overflow: 'hidden',
                  color: 'var(--agent-flow-text)',
                }}
              >
                <Box
                  px="2"
                  py="2"
                  style={{
                    borderBottom: '1px solid var(--agent-flow-node-border)',
                    background: 'var(--agent-flow-section-header-bg)',
                  }}
                >
                  <Text size="1" weight="medium" style={{ color: 'var(--agent-flow-text)', lineHeight: '18px' }}>
                    {t('agentBuilder.addToolsPopoverTitle')}
                  </Text>
                </Box>
                <Box
                  style={{
                    maxHeight: 216,
                    overflowY: 'auto',
                    overflowX: 'hidden',
                    padding: 4,
                  }}
                  onWheel={(e) => e.stopPropagation()}
                >
                  <Flex direction="column" gap="1">
                  {toolsToAdd.map((tool) => (
                    <button
                      key={toolKey(tool)}
                      type="button"
                      title={tool.description || tool.fullName || toolTitle(tool)}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleAddTool(tool);
                      }}
                      style={{
                        display: 'block',
                        width: '100%',
                        textAlign: 'left',
                        border: 'none',
                        borderRadius: 'var(--radius-1)',
                        background: 'transparent',
                        cursor: 'pointer',
                        padding: '5px 8px',
                        color: 'inherit',
                        font: 'inherit',
                      }}
                      onMouseEnter={(e) => {
                        e.currentTarget.style.background = 'var(--agent-flow-hover-surface)';
                      }}
                      onMouseLeave={(e) => {
                        e.currentTarget.style.background = 'transparent';
                      }}
                      onMouseDown={(e) => e.stopPropagation()}
                    >
                      <Text
                        as="div"
                        size="1"
                        weight="medium"
                        style={{
                          color: 'var(--agent-flow-text)',
                          lineHeight: '17px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {toolTitle(tool)}
                      </Text>
                      {tool.description ? (
                        <Text
                          as="div"
                          size="1"
                          style={{
                            marginTop: 1,
                            color: 'var(--agent-flow-text-muted)',
                            fontSize: 11,
                            lineHeight: '15px',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {tool.description}
                        </Text>
                      ) : null}
                    </button>
                  ))}
                  </Flex>
                </Box>
              </Popover.Content>
            </Popover.Root>
          ) : null}
        </Flex>

        {tools.length === 0 ? (
          <Box
            py="4"
            px="2"
            style={{
              textAlign: 'center',
              borderRadius: FLOW_NODE_WELL.radius,
              border: '1px dashed var(--gray-7)',
              background: FLOW_NODE_WELL.background,
            }}
          >
            <MaterialIcon name="handyman" size={28} color="var(--agent-flow-text-muted)" />
            <Text size="1" style={{ display: 'block', marginTop: 8, color: 'var(--agent-flow-text)' }}>
              {t('agentBuilder.noToolsSelected')}
            </Text>
            {!readOnly && toolsToAdd.length > 0 ? (
              <Text size="1" style={{ display: 'block', marginTop: 4, color: 'var(--agent-flow-text-muted)' }}>
                {t('agentBuilder.addToolsHint')}
              </Text>
            ) : null}
          </Box>
        ) : (
          <Box
            style={{
              maxHeight: 280,
              overflowY: 'auto',
              borderRadius: FLOW_NODE_WELL.radius,
              border: FLOW_NODE_WELL.border,
              background: FLOW_NODE_WELL.background,
            }}
            onWheel={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
          >
            {tools.map((tool, index) => (
              <Box key={toolKey(tool)}>
                {index > 0 ? <Separator size="4" /> : null}
                <Flex
                  align="center"
                  justify="between"
                  gap="2"
                  px="2"
                  py="2"
                  style={{
                    background:
                      index % 2 === 1 ? 'var(--agent-flow-zebra-row)' : 'var(--agent-flow-well-bg)',
                  }}
                >
                  <Box
                    style={{
                      width: 3,
                      alignSelf: 'stretch',
                      minHeight: 36,
                      borderRadius: 2,
                      background: 'var(--gray-8)',
                      flexShrink: 0,
                      opacity: 0.9,
                    }}
                  />
                  <Box style={{ minWidth: 0, flex: 1 }}>
                    <Text size="2" weight="medium" style={{ color: 'var(--agent-flow-text)' }}>
                      {tool.name.replace(/_/g, ' ')}
                    </Text>
                    {tool.description ? (
                      <Text
                        size="1"
                        style={{
                          color: 'var(--agent-flow-text-muted)',
                          marginTop: 4,
                          display: '-webkit-box',
                          WebkitLineClamp: 2,
                          WebkitBoxOrient: 'vertical',
                          overflow: 'hidden',
                        }}
                      >
                        {tool.description}
                      </Text>
                    ) : null}
                  </Box>
                  {!readOnly ? (
                    <IconButton
                      type="button"
                      size="1"
                      variant="soft"
                      color="red"
                      className="toolset-flow-remove-tool"
                      aria-label={t('agentBuilder.removeToolAriaLabel', { name: tool.name })}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRemoveTool(tool.name);
                      }}
                    >
                      <MaterialIcon name="remove" size={18} />
                    </IconButton>
                  ) : null}
                </Flex>
              </Box>
            ))}
          </Box>
        )}
      </Box>
    </Box>
    </div>
  );
}

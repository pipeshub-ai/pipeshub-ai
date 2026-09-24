'use client';

import React, { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, IconButton, Separator, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { FlowNodeData } from '../types';
import { normalizeDisplayName } from '../display-utils';
import { NodeHandles } from './node-handles';
import { FLOW_NODE_CARD, FLOW_NODE_PANEL_BG, FLOW_NODE_WELL, getFlowNodeChrome } from '../flow-theme';

export type McpFlowTool = {
  name: string;
  fullName: string;
  description?: string;
};

function toolKey(t: McpFlowTool): string {
  return t.fullName || t.name;
}

/**
 * Renders one attached MCP server instance. Unlike `ToolsetFlowNode`, tools are read-only
 * here — the drop handler attaches the instance's full discovered/selected tool list at
 * drop time (see `canvas-drop-handler.ts`'s `mcp-server` branch); there is no per-tool
 * add/remove affordance because MCP tool schemas are re-discovered live at chat time
 * (`MCPToolProvider`), not pinned per selection like toolset tools.
 */
export function McpFlowNode({
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
  const chrome = useMemo(() => getFlowNodeChrome(data.type), [data.type]);

  const cfg = (data.config || {}) as Record<string, unknown>;
  const displayName = String(cfg.displayName ?? '').trim() || String(cfg.name ?? '').trim();
  const primaryTitle = normalizeDisplayName(displayName || data.label);
  const tools = (cfg.tools as McpFlowTool[]) || [];

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
                <MaterialIcon name="hub" size={22} color={chrome.iconColor} />
              </Flex>
              <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                <Text weight="medium" style={{ color: 'var(--agent-flow-text)', lineHeight: '20px', fontSize: 14 }}>
                  {primaryTitle}
                </Text>
                <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', lineHeight: '16px' }}>
                  {t('agentBuilder.mcpServerNodeSubtitle')}
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
          <Flex align="center" gap="2" mb="2">
            <MaterialIcon name="build" size={16} color="var(--agent-flow-text-muted)" />
            <Text size="1" weight="medium" style={{ color: 'var(--agent-flow-text)' }}>
              {t('agentBuilder.toolsLabel')}
            </Text>
            <Badge size="1" variant="soft" color="gray" highContrast>
              {tools.length}
            </Badge>
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
                    gap="2"
                    px="2"
                    py="2"
                    style={{
                      background: index % 2 === 1 ? 'var(--agent-flow-zebra-row)' : 'var(--agent-flow-well-bg)',
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

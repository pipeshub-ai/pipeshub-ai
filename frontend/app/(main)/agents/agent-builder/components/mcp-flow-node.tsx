'use client';

import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useReactFlow } from '@xyflow/react';
import { Box, Flex, Text, IconButton, Separator, Badge, Popover, Switch } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { FlowNodeData } from '../types';
import { normalizeDisplayName } from '../display-utils';
import type { McpFlowTool } from '../sidebar-mcp-utils';
import { NodeHandles } from './node-handles';
import { FLOW_NODE_CARD, FLOW_NODE_PANEL_BG, FLOW_NODE_WELL, getFlowNodeChrome } from '../flow-theme';
import { McpAgentRulesContext, McpLiveServersContext, mcpLiveView, toolsMissingFromServer } from '../mcp-live-servers';
import { McpStatusBadge } from '../../../workspace/mcp-servers/connection-state';
import { McpToolRulesDialog } from '../../../workspace/mcp-servers/components/mcp-tool-rules-dialog';
import { ruleToolFromName, ruleToolsFromInfo } from '../../../workspace/mcp-servers/tool-rules';

/**
 * One attached MCP server instance. With "All tools" on, the agent gets whatever the server
 * offers at chat time, including tools it adds later; otherwise exactly the listed tools.
 * The server's live listing (`McpLiveServersContext`) decides what can be added and flags saved
 * tools it no longer offers; `availableTools`, what discovery returned when the server or its
 * tools were dropped, stands in until that listing arrives or when it couldn't list them.
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
  const { setNodes } = useReactFlow();
  const chrome = useMemo(() => getFlowNodeChrome(data.type), [data.type]);

  const cfg = (data.config || {}) as Record<string, unknown>;
  const displayName = String(cfg.displayName ?? '').trim() || String(cfg.name ?? '').trim();
  const primaryTitle = normalizeDisplayName(displayName || data.label);
  const allTools = cfg.allTools === true;
  const savedTools = useMemo(() => (cfg.tools as McpFlowTool[]) || [], [cfg.tools]);
  const liveServers = useContext(McpLiveServersContext);
  const live = useMemo(() => mcpLiveView(liveServers, String(cfg.instanceId ?? '')), [liveServers, cfg.instanceId]);
  const liveTools = live.kind === 'live' ? live.tools : null;
  // With "All tools" on, the agent gets what the server offers now, so that is what's shown.
  const tools = allTools && liveTools ? liveTools : savedTools;
  const availableTools = useMemo(() => {
    if (liveTools) return liveTools;
    const avail = (cfg.availableTools as McpFlowTool[]) || [];
    return avail.length > 0 ? avail : savedTools;
  }, [liveTools, cfg.availableTools, savedTools]);
  const missingTools = useMemo(
    () => (allTools ? new Set<string>() : toolsMissingFromServer(savedTools, liveTools)),
    [allTools, savedTools, liveTools]
  );
  const toolsToAdd = useMemo(
    () => (allTools ? [] : availableTools.filter((a) => !tools.some((tool) => tool.name === a.name))),
    [allTools, availableTools, tools]
  );

  const [addToolsOpen, setAddToolsOpen] = useState(false);
  const [rulesOpen, setRulesOpen] = useState(false);
  const agentKey = useContext(McpAgentRulesContext);
  const instanceId = String(cfg.instanceId ?? '');
  // The rules cover the tools this agent has; the live list says what each does to data. A tool
  // it doesn't have is known by its name only, so its rule is kept unless the name deletes.
  const ruleTools = useMemo(() => {
    const listed = new Map((liveServers?.find((server) => server._id === instanceId)?.tools ?? []).map((tool) => [tool.name, tool]));
    return tools.map((tool) => {
      const info = listed.get(tool.name);
      return info ? ruleToolsFromInfo([info])[0] : ruleToolFromName(tool.name, tool.description);
    });
  }, [tools, liveServers, instanceId]);
  useEffect(() => {
    if (toolsToAdd.length === 0) setAddToolsOpen(false);
  }, [toolsToAdd.length]);

  const updateConfig = useCallback(
    (update: (config: Record<string, unknown>) => Record<string, unknown>) => {
      setNodes((nodes) =>
        nodes.map((node) =>
          node.id === id
            ? { ...node, data: { ...node.data, config: update((node.data.config || {}) as Record<string, unknown>) } }
            : node
        )
      );
    },
    [id, setNodes]
  );

  const setAllTools = useCallback(
    (on: boolean) => {
      updateConfig((c) => {
        const avail =
          liveTools ??
          (((c.availableTools as McpFlowTool[]) || []).length
            ? (c.availableTools as McpFlowTool[])
            : ((c.tools as McpFlowTool[]) || []));
        return on ? { ...c, allTools: true, tools: avail } : { ...c, allTools: false };
      });
    },
    [updateConfig, liveTools]
  );

  const handleAddTool = useCallback(
    (tool: McpFlowTool) => {
      updateConfig((c) => {
        const cur = (c.tools as McpFlowTool[]) || [];
        return cur.some((existing) => existing.name === tool.name) ? c : { ...c, tools: [...cur, tool] };
      });
    },
    [updateConfig]
  );

  // Taking a tool out of "All tools" leaves the rest selected, explicitly: the ones on show.
  const handleRemoveTool = useCallback(
    (toolName: string) => {
      updateConfig((c) => ({
        ...c,
        allTools: false,
        tools: (c.allTools === true && liveTools ? liveTools : (c.tools as McpFlowTool[]) || []).filter(
          (tool) => tool.name !== toolName
        ),
      }));
    },
    [updateConfig, liveTools]
  );

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
                <Flex align="center" gap="2" wrap="wrap">
                  <Text size="1" style={{ color: 'var(--agent-flow-text-muted)', lineHeight: '16px' }}>
                    {t('agentBuilder.mcpServerNodeSubtitle')}
                  </Text>
                  {live.kind === 'unavailable' ? (
                    <Badge size="1" color="red">
                      {t('agentBuilder.mcpServerUnavailable')}
                    </Badge>
                  ) : live.kind === 'live' && live.state !== 'ready' ? (
                    <McpStatusBadge state={live.state} />
                  ) : null}
                </Flex>
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
          {live.kind === 'unavailable' ? (
            <Text as="div" size="1" mb="2" style={{ color: 'var(--red-11)' }}>
              {t('agentBuilder.mcpServerUnavailableHint')}
            </Text>
          ) : null}
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
            <Flex align="center" gap="2">
              {agentKey && instanceId && liveServers ? (
                <IconButton
                  type="button"
                  size="1"
                  variant="soft"
                  color="gray"
                  title={t('workspace.mcpServers.toolRules.open')}
                  aria-label={t('workspace.mcpServers.toolRules.open')}
                  onClick={(e) => {
                    e.stopPropagation();
                    setRulesOpen(true);
                  }}
                >
                  <MaterialIcon name="rule" size={16} color="var(--agent-flow-text)" />
                </IconButton>
              ) : null}
              <Text as="label" size="1" style={{ color: 'var(--agent-flow-text-muted)' }}>
                <Flex align="center" gap="1">
                  <Switch
                    size="1"
                    checked={allTools}
                    disabled={readOnly}
                    onCheckedChange={setAllTools}
                    onClick={(e) => e.stopPropagation()}
                  />
                  {t('agentBuilder.mcpAllTools')}
                </Flex>
              </Text>
              {!readOnly && toolsToAdd.length > 0 ? (
                <Popover.Root open={addToolsOpen} onOpenChange={setAddToolsOpen}>
                  <Popover.Trigger>
                    <IconButton
                      type="button"
                      size="1"
                      variant="soft"
                      color="gray"
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
                      padding: 4,
                      width: 248,
                      maxWidth: 'min(248px, calc(100vw - 20px))',
                      maxHeight: 240,
                      overflowY: 'auto',
                      borderRadius: 'var(--radius-2)',
                      border: '1px solid var(--agent-flow-node-border)',
                      backgroundColor: FLOW_NODE_PANEL_BG,
                      boxShadow: FLOW_NODE_CARD.shadow,
                      color: 'var(--agent-flow-text)',
                    }}
                  >
                    <Text as="div" size="1" weight="medium" style={{ padding: '4px 8px' }}>
                      {t('agentBuilder.addToolsPopoverTitle')}
                    </Text>
                    {toolsToAdd.map((tool) => (
                      <button
                        key={tool.name}
                        type="button"
                        title={tool.description || tool.name}
                        onClick={(e) => {
                          e.stopPropagation();
                          handleAddTool(tool);
                        }}
                        onMouseDown={(e) => e.stopPropagation()}
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
                          fontSize: 12,
                        }}
                      >
                        {tool.name.replace(/_/g, ' ')}
                      </button>
                    ))}
                  </Popover.Content>
                </Popover.Root>
              ) : null}
            </Flex>
          </Flex>

          {allTools ? (
            <Text as="div" size="1" mb="2" style={{ color: 'var(--agent-flow-text-muted)' }}>
              {t('agentBuilder.mcpAllToolsHint')}
            </Text>
          ) : null}

          {tools.length === 0 ? (
            <Box
              py="4"
              px="2"
              style={{
                textAlign: 'center',
                borderRadius: FLOW_NODE_WELL.radius,
                border: allTools ? '1px dashed var(--gray-7)' : '1px dashed var(--red-8)',
                background: FLOW_NODE_WELL.background,
              }}
            >
              <MaterialIcon name="handyman" size={28} color="var(--agent-flow-text-muted)" />
              <Text size="1" style={{ display: 'block', marginTop: 8, color: 'var(--agent-flow-text)' }}>
                {allTools ? t('agentBuilder.mcpAllToolsNoneYet') : t('agentBuilder.mcpNoToolsSelected')}
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
                <Box key={tool.name}>
                  {index > 0 ? <Separator size="4" /> : null}
                  <Flex
                    align="center"
                    justify="between"
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
                      <Flex align="center" gap="2" wrap="wrap">
                        <Text size="2" weight="medium" style={{ color: 'var(--agent-flow-text)' }}>
                          {tool.name.replace(/_/g, ' ')}
                        </Text>
                        {missingTools.has(tool.name) ? (
                          <Badge size="1" color="amber" title={t('agentBuilder.mcpToolNotOnServerHint')}>
                            {t('agentBuilder.mcpToolNotOnServer')}
                          </Badge>
                        ) : null}
                      </Flex>
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
      {rulesOpen && agentKey ? (
        <McpToolRulesDialog
          open
          onOpenChange={setRulesOpen}
          target={{ kind: 'agent', agentKey, instanceId }}
          serverName={primaryTitle}
          tools={ruleTools}
          readOnly={readOnly}
        />
      ) : null}
    </div>
  );
}

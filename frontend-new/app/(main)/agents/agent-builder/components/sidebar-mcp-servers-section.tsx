'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Text, TextField, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { CHAT_ITEM_HEIGHT } from '@/app/components/sidebar';
import type { MCPServerInstance, MCPServerTool } from '@/app/(main)/workspace/mcp-servers/types';
import { SidebarCategoryRow } from './sidebar-category-row';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';
import { toggleKeyedBoolean } from '../sidebar-expand-utils';
import { AGENT_MCP_FALLBACK_ICON } from '../display-utils';
import { mcpNamespacedName } from '../mcp-naming';

/** MCP instance row: collapsed by default; user expands to see tools. */
const DEFAULT_MCP_INSTANCE_EXPANDED = false;

type McpSidebarStatus = 'authenticated' | 'needs_authentication' | 'registry';

function getMcpSidebarStatus(server: MCPServerInstance): McpSidebarStatus {
  if (server.isFromRegistry && !server.instanceId) return 'registry';
  if (server.isAuthenticated) return 'authenticated';
  return 'needs_authentication';
}

function buildMcpServerDragPayload(
  server: MCPServerInstance
): Record<string, string> {
  const tools = server.tools ?? [];
  const instanceId = server.instanceId || '';
  const serverType = server.serverType || '';
  const serverName = server.instanceName || server.serverType || '';
  return {
    'application/reactflow': `mcp-server-${server.instanceId || server.instanceName || server.serverType}`,
    mcpServerName: serverName,
    mcpDisplayName: server.displayName || server.instanceName || '',
    mcpServerType: serverType,
    mcpInstanceId: instanceId,
    mcpTools: JSON.stringify(
      tools.map((t) => ({
        name: t.name,
        namespacedName: mcpNamespacedName(serverType || undefined, serverName, t.name, t.namespacedName),
        description: t.description || '',
        inputSchema: t.inputSchema,
      }))
    ),
    mcpSelectedTools: JSON.stringify(tools.map((t) => t.name)),
    mcpIconPath: server.iconPath || '',
    isMcpConfigured: String(Boolean(server.isConfigured !== false)),
    isMcpAuthenticated: String(Boolean(server.isAuthenticated)),
    type: 'mcp-server',
  };
}

function buildMcpToolDragPayload(
  tool: MCPServerTool,
  server: MCPServerInstance
): Record<string, string> {
  const instanceId = server.instanceId || '';
  const serverType = server.serverType || '';
  const serverName = server.instanceName || server.serverType || '';
  const namespacedName = mcpNamespacedName(serverType || undefined, serverName, tool.name, tool.namespacedName);
  return {
    'application/reactflow': `mcp-server-${server.instanceId || server.instanceName || server.serverType}`,
    mcpServerName: serverName,
    mcpDisplayName: server.displayName || server.instanceName || '',
    mcpServerType: serverType,
    mcpInstanceId: instanceId,
    mcpTools: JSON.stringify([
      {
        name: tool.name,
        namespacedName,
        description: tool.description || '',
        inputSchema: tool.inputSchema,
      },
    ]),
    mcpSelectedTools: JSON.stringify([tool.name]),
    mcpIconPath: server.iconPath || '',
    isMcpConfigured: String(Boolean(server.isConfigured !== false)),
    isMcpAuthenticated: String(Boolean(server.isAuthenticated)),
    type: 'mcp-tool',
    mcpToolName: tool.name,
    mcpToolNamespacedName: namespacedName,
  };
}

function applyDragData(e: React.DragEvent, data: Record<string, string>) {
  e.dataTransfer.effectAllowed = 'move';
  Object.entries(data).forEach(([k, v]) => {
    if (v != null) e.dataTransfer.setData(k, v);
  });
}

function McpToolRow(props: {
  tool: MCPServerTool;
  server: MCPServerInstance;
  needsConfiguration: boolean;
  structureLocked?: boolean;
  onBlocked?: () => void;
}) {
  const { tool, server, needsConfiguration, structureLocked = false, onBlocked } = props;
  const blocked = needsConfiguration || structureLocked;
  const payload = buildMcpToolDragPayload(tool, server);

  return (
    <Box
      draggable={!blocked}
      onDragStart={(e) => {
        if (blocked) {
          e.preventDefault();
          onBlocked?.();
          return;
        }
        applyDragData(e, payload);
      }}
      mb="1"
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        minHeight: CHAT_ITEM_HEIGHT,
        padding: '0 12px',
        boxSizing: 'border-box',
        gap: 8,
        cursor: blocked ? 'not-allowed' : 'grab',
        opacity: blocked ? 0.55 : 1,
        borderRadius: 'var(--radius-1)',
        border: '1px solid transparent',
        backgroundColor: 'transparent',
      }}
      className={
        blocked
          ? 'agent-builder-draggable-row agent-builder-draggable-row--disabled'
          : 'agent-builder-draggable-row'
      }
    >
      <MaterialIcon name="settings_ethernet" size={16} color="var(--slate-11)" />
      <span
        style={{
          flex: 1,
          fontSize: 14,
          lineHeight: '20px',
          color: 'var(--slate-11)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          textAlign: 'left',
        }}
      >
        {tool.name}
      </span>
    </Box>
  );
}

/** Groups MCP server instances by their serverType. */
function groupMcpServersByType(
  servers: MCPServerInstance[]
): Record<string, MCPServerInstance[]> {
  const grouped: Record<string, MCPServerInstance[]> = {};
  servers.forEach((s) => {
    const key = s.serverType || 'custom';
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(s);
  });
  return grouped;
}

export function SidebarMcpServersSection(props: {
  mcpServers: MCPServerInstance[];
  loading: boolean;
  refreshMcpServers: (
    agentKey?: string | null,
    isServiceAccount?: boolean,
    search?: string
  ) => Promise<void>;
  loadMoreMcpServers: () => void | Promise<void>;
  mcpServersHasMore: boolean;
  mcpServersLoadingMore: boolean;
  activeMcpServerTypes: string[];
  isServiceAccount: boolean;
  agentKey: string | null;
  onNotify: (message: string) => void;
  structureLocked?: boolean;
  onPaletteStructureDragBlocked?: () => void;
}) {
  const {
    mcpServers,
    loading,
    refreshMcpServers,
    loadMoreMcpServers,
    mcpServersHasMore,
    mcpServersLoadingMore,
    activeMcpServerTypes,
    isServiceAccount,
    agentKey,
    onNotify,
    structureLocked = false,
    onPaletteStructureDragBlocked,
  } = props;

  const { t } = useTranslation();
  const [searchInput, setSearchInput] = useState('');
  const [expandedServers, setExpandedServers] = useState<Record<string, boolean>>({});
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const loadMoreRef = useRef(loadMoreMcpServers);
  useEffect(() => {
    loadMoreRef.current = loadMoreMcpServers;
  }, [loadMoreMcpServers]);

  const onServerToggle = useCallback((key: string, defaultWhenUnset: boolean) => {
    setExpandedServers((p) => toggleKeyedBoolean(p, key, defaultWhenUnset));
  }, []);

  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchInput(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        void refreshMcpServers(agentKey, isServiceAccount, value);
      }, 400);
    },
    [agentKey, isServiceAccount, refreshMcpServers]
  );

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    []
  );

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !mcpServersLoadingMore && mcpServersHasMore) {
          void loadMoreRef.current();
        }
      },
      { threshold: 0.1 }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [mcpServersHasMore, mcpServersLoadingMore]);

  const notifyStructureDragBlocked = useCallback(() => {
    if (onPaletteStructureDragBlocked) onPaletteStructureDragBlocked();
    else onNotify(t('agentBuilder.viewerPaletteDragBlocked'));
  }, [onPaletteStructureDragBlocked, onNotify, t]);

  const handleUnconfiguredDrag = useCallback(
    (server: MCPServerInstance, isFromRegistry: boolean) => {
      if (isFromRegistry) {
        onNotify(t('agentBuilder.mcpServerNotConfiguredNotify', { name: server.displayName }));
        return;
      }
      const reason = !server.isAuthenticated
        ? t('agentBuilder.notAuthenticatedReason')
        : t('agentBuilder.notConfiguredReason');
      onNotify(t('agentBuilder.mcpServerNotReady', { name: server.displayName, reason }));
    },
    [onNotify, t]
  );

  const handleDuplicateDrag = useCallback(
    (server: MCPServerInstance) => {
      onNotify(
        t('agentBuilder.mcpServerDuplicate', {
          name: server.displayName || server.instanceName || server.serverType,
        })
      );
    },
    [onNotify, t]
  );

  const serversByType = useMemo(() => groupMcpServersByType(mcpServers), [mcpServers]);

  return (
    <Box style={{ minWidth: 0 }}>
      <Box pb="2" style={{ minWidth: 0 }}>
        <TextField.Root
          size="2"
          variant="surface"
          color="gray"
          placeholder={t('agentBuilder.searchMcpServers')}
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          disabled={loading}
        >
          <TextField.Slot side="left">
            <MaterialIcon name="search" size={18} color="var(--slate-11)" />
          </TextField.Slot>
        </TextField.Root>
      </Box>

      {loading ? <AgentBuilderPaletteSkeletonList count={4} /> : null}

      {!loading && mcpServers.length === 0 ? (
        <Box pl="3" py="2">
          <Text size="1" style={{ color: 'var(--slate-11)', fontStyle: 'italic' }}>
            {searchInput.trim()
              ? t('agentBuilder.noMcpServersMatch', { query: searchInput })
              : t('agentBuilder.noMcpServers')}
          </Text>
        </Box>
      ) : null}

      {!loading
        ? Object.entries(serversByType).map(([serverType, typeServers]) => {
            const first = typeServers[0];
            const typeKey = `mcp-type-${serverType}`;
            const isTypeExpanded = expandedServers[typeKey] ?? true;

            return (
              <Box key={serverType} mb="2">
                <SidebarCategoryRow
                  groupLabel={first.displayName || serverType}
                  groupIcon={first.iconPath}
                  groupIconFallbackSrc={AGENT_MCP_FALLBACK_ICON}
                  groupMaterialIcon={!first.iconPath ? 'dns' : undefined}
                  itemCount={typeServers.length}
                  isExpanded={isTypeExpanded}
                  onToggle={() => onServerToggle(typeKey, true)}
                >
                  {typeServers.map((server) => {
                    const instKey = `mcp-${server.instanceId || server.instanceName || server.serverType}`;
                    const isInstanceExpanded =
                      expandedServers[instKey] ?? DEFAULT_MCP_INSTANCE_EXPANDED;
                    const isFromRegistry = !server.instanceId || server.isFromRegistry === true;
                    const needsConfiguration =
                      isFromRegistry || !server.isAuthenticated || server.isConfigured === false;
                    const status = getMcpSidebarStatus(server);

                    const normalizedType = (
                      server.instanceId ||
                      server.instanceName ||
                      server.serverType ||
                      ''
                    ).toLowerCase();
                    const isDuplicate = activeMcpServerTypes.some(
                      (t) => t.toLowerCase() === normalizedType
                    );

                    const dragPayload = buildMcpServerDragPayload(server);
                    const dragBlocked = structureLocked || needsConfiguration || isDuplicate;
                    const dragType = dragBlocked
                      ? undefined
                      : dragPayload['application/reactflow'];

                    const onDragAttempt = structureLocked
                      ? notifyStructureDragBlocked
                      : isDuplicate
                        ? () => handleDuplicateDrag(server)
                        : needsConfiguration
                          ? () => handleUnconfiguredDrag(server, isFromRegistry)
                          : undefined;

                    const statusTooltip =
                      status === 'authenticated'
                        ? t('agentBuilder.mcpServerAuthenticated')
                        : status === 'needs_authentication'
                          ? t('agentBuilder.mcpServerNeedsAuth')
                          : t('agentBuilder.mcpServerRegistry');

                    return (
                      <SidebarCategoryRow
                        key={server.instanceId || server.instanceName || server.serverType}
                        groupLabel={server.instanceName || server.displayName || server.serverType}
                        groupIcon={server.iconPath}
                        groupIconFallbackSrc={AGENT_MCP_FALLBACK_ICON}
                        groupMaterialIcon={!server.iconPath ? 'dns' : undefined}
                        itemCount={(server.tools ?? []).length}
                        isExpanded={isInstanceExpanded}
                        onToggle={() => onServerToggle(instKey, DEFAULT_MCP_INSTANCE_EXPANDED)}
                        dragType={dragType}
                        dragData={dragBlocked ? undefined : dragPayload}
                        onDragAttempt={onDragAttempt}
                        showConfigureIcon={isFromRegistry || needsConfiguration}
                        onConfigureClick={
                          isFromRegistry
                            ? () =>
                                onNotify(
                                  t('agentBuilder.mcpServerNotConfiguredNotify', {
                                    name: server.displayName,
                                  })
                                )
                            : undefined
                        }
                        configureTooltip={statusTooltip}
                        toolsetStatus={status}
                      >
                        {(server.tools ?? []).map((tool) => (
                          <McpToolRow
                            key={`${server.instanceId}-${tool.name}`}
                            tool={tool}
                            server={server}
                            needsConfiguration={needsConfiguration}
                            structureLocked={structureLocked}
                            onBlocked={
                              structureLocked
                                ? notifyStructureDragBlocked
                                : () => handleUnconfiguredDrag(server, isFromRegistry)
                            }
                          />
                        ))}
                      </SidebarCategoryRow>
                    );
                  })}
                </SidebarCategoryRow>
              </Box>
            );
          })
        : null}

      {!loading ? <Box ref={sentinelRef} style={{ height: 1 }} aria-hidden /> : null}

      {!loading && mcpServersHasMore ? (
        <Button
          type="button"
          variant="ghost"
          size="1"
          color="gray"
          disabled={mcpServersLoadingMore}
          onClick={() => void loadMoreMcpServers()}
          style={{ marginTop: 8 }}
        >
          {mcpServersLoadingMore ? t('agentBuilder.loadingMore') : t('agentBuilder.loadMore')}
        </Button>
      ) : null}
    </Box>
  );
}

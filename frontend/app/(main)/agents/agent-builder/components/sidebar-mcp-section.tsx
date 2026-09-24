'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { CHAT_ITEM_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import {
  buildMcpServerDragPayload,
  collectActiveMcpInstanceIdsFromNodes,
  collectActiveMcpTypeIdsFromNodes,
  getMcpSidebarStatus,
  isMcpTypeIdConflict,
} from '../sidebar-mcp-utils';
import type { McpInstanceIdFlowNode } from '../sidebar-mcp-utils';
import { SidebarCategoryRow } from './sidebar-category-row';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';
import { McpCredentialsDialog } from './agent-mcp-credentials-dialog';
import { isMcpOAuthSuccessMessageType } from '../../../workspace/mcp-servers/oauth/mcp-oauth-window-messages';

export function AgentBuilderMcpSection(props: {
  mcpServers: McpMyServerEntry[];
  loading: boolean;
  refreshMcpServers: () => Promise<void>;
  mcpMergeCheckNodes: McpInstanceIdFlowNode[];
  agentKey?: string | null;
  isServiceAccount?: boolean;
  onNotify: (message: string) => void;
  /** Viewer without edit: block MCP drags onto the canvas. */
  structureLocked?: boolean;
  onPaletteStructureDragBlocked?: () => void;
}) {
  const {
    mcpServers,
    loading,
    refreshMcpServers,
    mcpMergeCheckNodes,
    agentKey = null,
    isServiceAccount = false,
    onNotify,
    structureLocked = false,
    onPaletteStructureDragBlocked,
  } = props;

  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [credentialsDialogInstance, setCredentialsDialogInstance] = useState<McpMyServerEntry | null>(
    null
  );

  const activeInstanceIds = collectActiveMcpInstanceIdsFromNodes(mcpMergeCheckNodes);
  const activeTypeIds = collectActiveMcpTypeIdsFromNodes(mcpMergeCheckNodes);

  const notifyStructureDragBlocked = useCallback(() => {
    if (onPaletteStructureDragBlocked) onPaletteStructureDragBlocked();
    else onNotify(t('agentBuilder.viewerPaletteDragBlocked'));
  }, [onPaletteStructureDragBlocked, onNotify, t]);

  const notifyUnauthenticated = useCallback(
    (entry: McpMyServerEntry) => {
      onNotify(
        t('agentBuilder.toolsetNotReadyNotify', {
          name: entry.name,
          reason: t('agentBuilder.notAuthenticatedReason'),
        })
      );
    },
    [onNotify, t]
  );

  const notifyDuplicate = useCallback(
    (entry: McpMyServerEntry) => {
      onNotify(t('agentBuilder.mcpServerAlreadyAttachedNotify', { name: entry.name }));
    },
    [onNotify, t]
  );

  useEffect(() => {
    const onOAuthMessage = (event: MessageEvent) => {
      if (typeof window !== 'undefined' && event.origin !== window.location.origin) return;
      if (!isMcpOAuthSuccessMessageType(event.data?.type)) return;
      void refreshMcpServers();
    };
    window.addEventListener('message', onOAuthMessage);
    return () => window.removeEventListener('message', onOAuthMessage);
  }, [refreshMcpServers]);

  if (loading) {
    return <AgentBuilderPaletteSkeletonList count={3} />;
  }

  if (mcpServers.length === 0) {
    return (
      <Text size="1" style={{ color: 'var(--slate-11)', fontStyle: 'italic', padding: '4px 8px' }}>
        {t('agentBuilder.noMcpServersAvailable')}
      </Text>
    );
  }

  return (
    <Box>
      {mcpServers.map((entry) => {
        const key = `mcp-row-${entry._id}`;
        const isExpanded = expanded[key] ?? false;
        const status = getMcpSidebarStatus(entry);
        const isDuplicate =
          activeInstanceIds.has(entry._id) || isMcpTypeIdConflict(activeTypeIds, entry._id, entry.typeId);
        const dragBlocked = !entry.isAuthenticated || isDuplicate;
        const dragPayload = buildMcpServerDragPayload(entry);
        const dragType = dragBlocked ? undefined : dragPayload['application/reactflow'];

        const onDragAttempt = structureLocked
          ? notifyStructureDragBlocked
          : isDuplicate
            ? () => notifyDuplicate(entry)
            : !entry.isAuthenticated
              ? () => notifyUnauthenticated(entry)
              : undefined;

        const showConfigureIcon = entry.authMode !== 'none' && !entry.useAdminAuth;

        return (
          <SidebarCategoryRow
            key={entry._id}
            groupLabel={entry.name}
            groupMaterialIcon={entry.isCustom ? 'dns' : 'hub'}
            itemCount={entry.tools.length}
            isExpanded={isExpanded}
            onToggle={() => setExpanded((p) => ({ ...p, [key]: !isExpanded }))}
            dragType={structureLocked ? undefined : dragType}
            dragData={structureLocked || dragBlocked ? undefined : dragPayload}
            onDragAttempt={onDragAttempt}
            showConfigureIcon={showConfigureIcon}
            onConfigureClick={
              showConfigureIcon && !structureLocked ? () => setCredentialsDialogInstance(entry) : undefined
            }
            configureTooltip={
              isServiceAccount
                ? entry.isAuthenticated
                  ? t('agentBuilder.manageAgentCredentialsTooltip')
                  : t('agentBuilder.setAgentCredentialsTooltip')
                : entry.isAuthenticated
                  ? t('agentBuilder.configureMcpTooltip')
                  : t('agentBuilder.authenticateMcpTooltip')
            }
            configureUseKeyIcon={isServiceAccount}
            configureIconColor="var(--slate-11)"
            toolsetStatus={status}
          >
            {entry.tools.length === 0 ? (
              <Text size="1" style={{ color: 'var(--slate-11)', fontStyle: 'italic', padding: '4px 8px' }}>
                {t('agentBuilder.noToolsSelected')}
              </Text>
            ) : (
              entry.tools.map((tool) => (
                <Box
                  key={tool.namespacedName || tool.name}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    width: '100%',
                    minWidth: 0,
                    minHeight: CHAT_ITEM_HEIGHT,
                    padding: '0 12px',
                    boxSizing: 'border-box',
                    gap: 8,
                  }}
                >
                  <MaterialIcon
                    name="build"
                    size={ICON_SIZE_DEFAULT}
                    color="var(--slate-11)"
                    style={{ flexShrink: 0, lineHeight: 0 }}
                  />
                  <span
                    style={{
                      flex: 1,
                      minWidth: 0,
                      fontSize: 14,
                      color: 'var(--slate-11)',
                      whiteSpace: 'normal',
                      overflowWrap: 'anywhere',
                      wordBreak: 'break-word',
                    }}
                  >
                    {tool.name.replace(/_/g, ' ')}
                  </span>
                </Box>
              ))
            )}
          </SidebarCategoryRow>
        );
      })}

      {credentialsDialogInstance ? (
        <McpCredentialsDialog
          instance={credentialsDialogInstance}
          agentKey={isServiceAccount ? agentKey : null}
          onClose={() => setCredentialsDialogInstance(null)}
          onSuccess={refreshMcpServers}
          onNotify={onNotify}
        />
      ) : null}
    </Box>
  );
}

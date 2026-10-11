'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Text } from '@radix-ui/themes';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import { isPersonalMcpInstance } from '../../../workspace/mcp-servers/types';
import {
  buildMcpServerDragPayload,
  buildMcpToolDragPayload,
  collectActiveMcpTypeIdsFromNodes,
  getMcpSidebarStatus,
  isMcpTypeIdConflict,
} from '../sidebar-mcp-utils';
import type { McpInstanceIdFlowNode } from '../sidebar-mcp-utils';
import { SidebarCategoryRow } from './sidebar-category-row';
import { SidebarToolDragRow } from './sidebar-draggable-row';
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
  /** Shared with the org (or a service account): only org servers can be attached. */
  agentShared?: boolean;
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
    agentShared = false,
    onNotify,
    structureLocked = false,
    onPaletteStructureDragBlocked,
  } = props;

  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [credentialsDialogInstance, setCredentialsDialogInstance] = useState<McpMyServerEntry | null>(
    null
  );

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

  const notifyPersonalOnSharedAgent = useCallback(
    (entry: McpMyServerEntry) => {
      onNotify(t('agentBuilder.mcpPersonalOnSharedAgentNotify', { name: entry.name }));
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
        const isPersonal = isPersonalMcpInstance(entry);
        const personalBlocked = isPersonal && agentShared;
        // Dropping the same instance again merges into its node; only another instance of
        // the same type is refused.
        const isTypeConflict = isMcpTypeIdConflict(activeTypeIds, entry._id, entry.typeId);
        const dragBlocked = structureLocked || !entry.isAuthenticated || isTypeConflict || personalBlocked;
        const dragPayload = buildMcpServerDragPayload(entry);
        const dragType = dragBlocked ? undefined : dragPayload['application/reactflow'];

        const onDragAttempt = structureLocked
          ? notifyStructureDragBlocked
          : personalBlocked
            ? () => notifyPersonalOnSharedAgent(entry)
            : isTypeConflict
              ? () => notifyDuplicate(entry)
              : !entry.isAuthenticated
                ? () => notifyUnauthenticated(entry)
                : undefined;

        const showConfigureIcon = entry.authMode !== 'none' && !entry.useAdminAuth;

        return (
          <SidebarCategoryRow
            key={entry._id}
            groupLabel={entry.name}
            groupMaterialIcon={isPersonal ? 'person' : entry.isCustom ? 'dns' : 'hub'}
            itemCount={entry.tools.length}
            isExpanded={isExpanded}
            onToggle={() => setExpanded((p) => ({ ...p, [key]: !isExpanded }))}
            dragType={dragType}
            dragData={dragBlocked ? undefined : dragPayload}
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
                <SidebarToolDragRow
                  key={tool.namespacedName || tool.name}
                  name={tool.name}
                  description={tool.description}
                  data={buildMcpToolDragPayload(entry, tool)}
                  disabled={dragBlocked}
                  onBlocked={onDragAttempt}
                />
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

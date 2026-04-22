'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { CHAT_ITEM_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import {
  buildToolDragPayload,
  buildToolsetDragPayload,
  getToolsetSidebarStatus,
  groupToolsetsByType,
  normalizeToolsetTypeKey,
} from '../sidebar-toolset-utils';
import { normalizePaletteLabel } from '../display-utils';
import { SidebarCategoryRow } from './sidebar-category-row';
import { UserToolsetConfigDialog } from './user-toolset-config-dialog';
import { isToolsetOAuthSuccessMessageType } from '@/app/(main)/toolsets/oauth/toolset-oauth-window-messages';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';
import { toggleKeyedBoolean } from '../sidebar-expand-utils';

/** Toolset type row (e.g. Slack): expanded by default so instance rows are listed. */
const DEFAULT_TOOLSET_TYPE_EXPANDED = true;
/** Toolset instance row: collapsed by default; user expands to see tools (Knowledge stops at instance rows). */
const DEFAULT_TOOLSET_INSTANCE_EXPANDED = false;

function applyToolDrag(e: React.DragEvent, data: Record<string, string>) {
  e.dataTransfer.effectAllowed = 'move';
  Object.entries(data).forEach(([k, v]) => {
    if (v != null) e.dataTransfer.setData(k, v);
  });
}

/** Row-level drag/configure flags for one toolset instance (shared by single-type and grouped UI). */
function getToolsetPaletteRowState(
  ts: BuilderSidebarToolset,
  ui: {
    isFromRegistry: boolean;
    forceShowConfigureIcon: boolean;
    configureUseKeyIcon: boolean;
    configureIconColor: string;
    configureTooltip: string;
  },
  activeInstanceIdSet: Set<string>,
  activeTypeKeysNoInstanceSet: Set<string>,
  structureLocked: boolean,
  orgCredentialUiLocked: boolean,
  isServiceAccount: boolean,
  onStructureDragBlocked: () => void,
  onDuplicate: () => void,
  onUnconfigured: () => void
) {
  const needsConfiguration = !ts.isConfigured || !ts.isAuthenticated;
  const normalizedType = normalizeToolsetTypeKey(ts.toolsetType || ts.name || '');
  const instanceId = ts.instanceId?.trim();
  const dup = instanceId
    ? activeInstanceIdSet.has(instanceId)
    : activeTypeKeysNoInstanceSet.has(normalizedType);
  const dragPayload = buildToolsetDragPayload(ts);
  const dragBlocked = structureLocked || needsConfiguration || dup;
  const dragType = dragBlocked ? undefined : dragPayload['application/reactflow'];
  const showCfg = ui.forceShowConfigureIcon || (!isServiceAccount && needsConfiguration);
  const cfgClickable = showCfg && !orgCredentialUiLocked;
  const configureLocked = orgCredentialUiLocked && showCfg;
  const onDragAttempt = structureLocked
    ? onStructureDragBlocked
    : dup
      ? onDuplicate
      : needsConfiguration
        ? onUnconfigured
        : undefined;

  return {
    needsConfiguration,
    dragPayload,
    dragBlocked,
    dragType,
    showCfg,
    cfgClickable,
    configureLocked,
    onDragAttempt,
  };
}

function ToolDragRow(props: {
  tool: BuilderSidebarToolset['tools'][0];
  toolset: BuilderSidebarToolset;
  needsConfiguration: boolean;
  /** Viewer without edit: block dragging tools onto the canvas. */
  structureLocked?: boolean;
  onBlocked?: () => void;
}) {
  const { tool, toolset, needsConfiguration, structureLocked = false, onBlocked } = props;
  const payload = buildToolDragPayload(tool, toolset);
  const blocked = needsConfiguration || structureLocked;
  return (
    <Box
      draggable={!blocked}
      onDragStart={(e) => {
        if (blocked) {
          e.preventDefault();
          onBlocked?.();
          return;
        }
        applyToolDrag(e, payload);
      }}
      mb="1"
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        minWidth: 0,
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
      <MaterialIcon name="build" size={ICON_SIZE_DEFAULT} color="var(--slate-11)" style={{ flexShrink: 0, lineHeight: 0 }} />
      <span
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 14,
          color: 'var(--slate-11)',
          whiteSpace: 'normal',
          overflowWrap: 'anywhere',
          wordBreak: 'break-word',
          textAlign: 'left',
        }}
      >
        {normalizePaletteLabel(tool.name)}
      </span>
    </Box>
  );
}

export function AgentBuilderToolsetsSection(props: {
  toolsets: BuilderSidebarToolset[];
  loading: boolean;
  refreshToolsets: (
    agentKey?: string | null,
    isServiceAccount?: boolean,
    search?: string
  ) => Promise<void>;
  activeToolsetInstanceIds: string[];
  activeToolsetTypeKeysWithoutInstance: string[];
  isServiceAccount: boolean;
  agentKey: string | null;
  onManageAgentToolsetCredentials?: (ts: BuilderSidebarToolset) => void;
  onNotify: (message: string) => void;
  /** Viewer without edit: block tool/toolset drags onto the canvas. */
  structureLocked?: boolean;
  /** SA viewer without edit: block search and org credential controls only. */
  orgCredentialUiLocked?: boolean;
  /** Same toast as main palette when structure-only drag is blocked (from sidebar). */
  onPaletteStructureDragBlocked?: () => void;
}) {
  const {
    toolsets,
    loading,
    refreshToolsets,
    activeToolsetInstanceIds,
    activeToolsetTypeKeysWithoutInstance,
    isServiceAccount,
    agentKey,
    onManageAgentToolsetCredentials,
    onNotify,
    structureLocked = false,
    orgCredentialUiLocked = false,
    onPaletteStructureDragBlocked,
  } = props;

  const { t } = useTranslation();
  const [searchInput, setSearchInput] = useState('');
  const [expandedApps, setExpandedApps] = useState<Record<string, boolean>>({});
  const [userConfigToolset, setUserConfigToolset] = useState<BuilderSidebarToolset | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeInstanceIdSet = useMemo(
    () => new Set(activeToolsetInstanceIds.filter(Boolean)),
    [activeToolsetInstanceIds]
  );
  const activeTypeKeysNoInstanceSet = useMemo(
    () => new Set(activeToolsetTypeKeysWithoutInstance.map(normalizeToolsetTypeKey).filter(Boolean)),
    [activeToolsetTypeKeysWithoutInstance]
  );

  const onAppToggle = useCallback((key: string, defaultWhenUnset: boolean) => {
    setExpandedApps((p) => toggleKeyedBoolean(p, key, defaultWhenUnset));
  }, []);

  const handleSearchChange = useCallback(
    (value: string) => {
      if (orgCredentialUiLocked) return;
      setSearchInput(value);
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => {
        void refreshToolsets(agentKey, isServiceAccount, value);
      }, 400);
    },
    [agentKey, orgCredentialUiLocked, isServiceAccount, refreshToolsets]
  );

  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (pollRef.current) clearInterval(pollRef.current);
    },
    []
  );

  useEffect(() => {
    const onOAuthMessage = async (event: MessageEvent) => {
      if (typeof window !== 'undefined' && event.origin !== window.location.origin) return;
      const messageType = event.data?.type;
      if (!isToolsetOAuthSuccessMessageType(messageType)) return;
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
      await refreshToolsets(agentKey, isServiceAccount, searchInput);
      onNotify(t('agentBuilder.oauthSuccessNotify'));
    };
    window.addEventListener('message', onOAuthMessage);
    return () => window.removeEventListener('message', onOAuthMessage);
  }, [agentKey, isServiceAccount, onNotify, refreshToolsets, searchInput, t]);

  const buildUiState = useCallback(
    (ts: BuilderSidebarToolset) => {
      const isFromRegistry = ts.isFromRegistry === true || !ts.instanceId;
      if (isServiceAccount && !isFromRegistry) {
        return {
          isFromRegistry,
          forceShowConfigureIcon: true,
          configureUseKeyIcon: true,
          configureIconColor: 'var(--slate-11)',
          configureTooltip: ts.isAuthenticated
            ? t('agentBuilder.manageAgentCredentialsTooltip')
            : t('agentBuilder.setAgentCredentialsTooltip'),
        };
      }
      if (isFromRegistry) {
        return {
          isFromRegistry,
          forceShowConfigureIcon: true,
          configureUseKeyIcon: false,
          configureIconColor: 'var(--slate-10)',
          configureTooltip: t('agentBuilder.notConfiguredTooltip'),
        };
      }
      return {
        isFromRegistry,
        forceShowConfigureIcon: false,
        configureUseKeyIcon: false,
        configureIconColor: 'var(--slate-11)',
        configureTooltip:
          ts.isConfigured && !ts.isAuthenticated
            ? t('agentBuilder.authenticateToolsetTooltip')
            : t('agentBuilder.configureToolsetTooltip'),
      };
    },
    [isServiceAccount, t]
  );

  const handleConfigureClick = useCallback(
    (ts: BuilderSidebarToolset) => {
      if (orgCredentialUiLocked) {
        onNotify(t('agentBuilder.paletteActionBlockedViewOnly'));
        return;
      }
      const instanceId = ts.instanceId || '';
      const isFromRegistry = ts.isFromRegistry === true || !instanceId;

      if (isServiceAccount && onManageAgentToolsetCredentials && !isFromRegistry) {
        onManageAgentToolsetCredentials(ts);
        return;
      }
      if (isFromRegistry) {
        onNotify(
          t('agentBuilder.toolsetNotConfiguredNotify', {
            name: ts.instanceName?.trim() || ts.displayName || ts.name || '',
          })
        );
        return;
      }

      setUserConfigToolset(ts);
    },
    [orgCredentialUiLocked, isServiceAccount, onManageAgentToolsetCredentials, onNotify, t]
  );

  const toolsetsByType = useMemo(() => groupToolsetsByType(toolsets), [toolsets]);

  const handleUnconfiguredDrag = useCallback(
    (ts: BuilderSidebarToolset, isFromRegistry: boolean) => {
      if (isFromRegistry) {
        onNotify(
          t('agentBuilder.toolsetNotConfiguredNotify', {
            name: ts.instanceName?.trim() || ts.displayName || ts.name || '',
          })
        );
        return;
      }
      const reason = !ts.isConfigured
        ? t('agentBuilder.notConfiguredReason')
        : t('agentBuilder.notAuthenticatedReason');
      onNotify(
        t('agentBuilder.toolsetNotReadyNotify', {
          name: ts.instanceName?.trim() || ts.displayName || ts.name || '',
          reason,
        })
      );
    },
    [onNotify, t]
  );

  const handleDuplicateDrag = useCallback(
    (ts: BuilderSidebarToolset) => {
      onNotify(
        t('agentBuilder.toolsetDuplicateNotify', {
          name: normalizePaletteLabel(ts.toolsetType || ts.name),
        })
      );
    },
    [onNotify, t]
  );

  const notifyStructureDragBlocked = useCallback(() => {
    if (onPaletteStructureDragBlocked) onPaletteStructureDragBlocked();
    else onNotify(t('agentBuilder.viewerPaletteDragBlocked'));
  }, [onPaletteStructureDragBlocked, onNotify, t]);

  const orgCredentialLockedTooltip = t('agentBuilder.paletteActionBlockedViewOnly');

  return (
    <Box style={{ minWidth: 0 }}>
      <Box pb="2" style={{ minWidth: 0 }}>
        <TextField.Root
          className="agent-builder-toolsets-search"
          size="2"
          variant="surface"
          color="gray"
          placeholder={t('agentBuilder.searchToolsets')}
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          disabled={loading || orgCredentialUiLocked}
        >
          <TextField.Slot side="left">
            <MaterialIcon name="search" size={18} color="var(--slate-11)" />
          </TextField.Slot>
        </TextField.Root>
      </Box>

      {loading ? (
        <AgentBuilderPaletteSkeletonList count={6} />
      ) : null}

      {!loading && toolsets.length === 0 ? (
        <Box pl="3" py="2">
          <Text size="1" style={{ color: 'var(--slate-11)', fontStyle: 'italic' }}>
            {searchInput.trim()
            ? t('agentBuilder.noToolsetsMatch', { query: searchInput })
            : t('agentBuilder.noToolsetsAvailable')}
          </Text>
        </Box>
      ) : null}

      {!loading
        ? Object.entries(toolsetsByType).map(([toolsetType, typeToolsets]) => {
            const first = typeToolsets[0];
            const typeKey = `toolset-type-${toolsetType}`;
            const isTypeExpanded = expandedApps[typeKey] ?? DEFAULT_TOOLSET_TYPE_EXPANDED;

            return (
              <Box key={toolsetType} mb="2">
                <SidebarCategoryRow
                  groupLabel={normalizePaletteLabel((first.toolsetType || toolsetType) as string)}
                  groupIcon={first.iconPath}
                  itemCount={typeToolsets.length}
                  isExpanded={isTypeExpanded}
                  onToggle={() => onAppToggle(typeKey, DEFAULT_TOOLSET_TYPE_EXPANDED)}
                >
                  {typeToolsets.map((ts) => {
                    const instKey = `toolset-${ts.instanceId || ts.name.toLowerCase()}`;
                    const isInstanceExpanded =
                      expandedApps[instKey] ?? DEFAULT_TOOLSET_INSTANCE_EXPANDED;
                    const ui = buildUiState(ts);
                    const {
                      needsConfiguration,
                      dragPayload,
                      dragBlocked,
                      dragType,
                      showCfg,
                      cfgClickable,
                      configureLocked,
                      onDragAttempt,
                    } = getToolsetPaletteRowState(
                      ts,
                      ui,
                      activeInstanceIdSet,
                      activeTypeKeysNoInstanceSet,
                      structureLocked,
                      orgCredentialUiLocked,
                      isServiceAccount,
                      notifyStructureDragBlocked,
                      () => handleDuplicateDrag(ts),
                      () => handleUnconfiguredDrag(ts, ui.isFromRegistry)
                    );

                    return (
                      <SidebarCategoryRow
                        key={ts.instanceId || ts.displayName}
                        groupLabel={normalizePaletteLabel(ts.instanceName || ts.displayName || ts.name || '')}
                        groupIcon={ts.iconPath}
                        itemCount={ts.tools.length}
                        isExpanded={isInstanceExpanded}
                        onToggle={() => onAppToggle(instKey, DEFAULT_TOOLSET_INSTANCE_EXPANDED)}
                        dragType={dragType}
                        dragData={dragBlocked ? undefined : dragPayload}
                        onDragAttempt={onDragAttempt}
                        showConfigureIcon={showCfg}
                        onConfigureClick={
                          cfgClickable ? () => void handleConfigureClick(ts) : undefined
                        }
                        configureDisabled={configureLocked}
                        configureDisabledTooltip={orgCredentialLockedTooltip}
                        configureTooltip={ui.configureTooltip}
                        configureUseKeyIcon={ui.configureUseKeyIcon}
                        configureIconColor={ui.configureIconColor}
                        toolsetStatus={getToolsetSidebarStatus(ts)}
                      >
                        {ts.tools.map((tool) => (
                          <ToolDragRow
                            key={`${ts.instanceId}-${tool.fullName || tool.name}`}
                            tool={tool}
                            toolset={ts}
                            needsConfiguration={needsConfiguration}
                            structureLocked={structureLocked}
                            onBlocked={
                              structureLocked
                                ? notifyStructureDragBlocked
                                : () => handleUnconfiguredDrag(ts, ui.isFromRegistry)
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

      {userConfigToolset?.instanceId ? (
        <UserToolsetConfigDialog
          key={userConfigToolset.instanceId}
          toolset={userConfigToolset}
          instanceId={userConfigToolset.instanceId}
          onClose={() => setUserConfigToolset(null)}
          onSuccess={async () => {
            await refreshToolsets(agentKey, isServiceAccount, searchInput);
          }}
          onNotify={onNotify}
        />
      ) : null}
    </Box>
  );
}

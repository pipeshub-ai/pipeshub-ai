'use client';

import React, { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, TextField, IconButton, ScrollArea } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { CONTENT_PADDING, HEADER_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { NodeTemplate } from '../types';
import { filterTemplatesBySearch, groupConnectorInstances, prepareDragData } from '../sidebar-utils';
import type { ToolsetTypeKeyFlowNode } from '../sidebar-toolset-utils';
import { toggleKeyedBoolean } from '../sidebar-expand-utils';
import { AGENT_LLM_FALLBACK_ICON, AGENT_TOOLSET_FALLBACK_ICON, resolveLlmProviderIconPath } from '../display-utils';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import { AgentBuilderToolsetsSection } from './sidebar-toolsets-section';
import { SidebarCategoryRow } from './sidebar-category-row';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';
import type { AgentWebSearchAttachment } from '../types';

const PALETTE_ROW_MIN_HEIGHT = 44;
const PALETTE_ICON_SIZE = 20;

/** Matches `expanded[k] ?? true` for keys not seeded in `useState` (e.g. `knowledge-connector-*`). */
const DEFAULT_KNOWLEDGE_NEST_EXPANDED = true;

const paletteRowLabelStyle: React.CSSProperties = {
  flex: 1,
  minWidth: 0,
  fontSize: 15,
  fontWeight: 500,
  lineHeight: '22px',
  color: 'var(--olive-12)',
  whiteSpace: 'normal',
  overflowWrap: 'anywhere',
  wordBreak: 'break-word',
  textAlign: 'left',
};

function applyDragData(event: React.DragEvent, entries: Record<string, string>) {
  event.dataTransfer.effectAllowed = 'move';
  Object.entries(entries).forEach(([k, v]) => {
    if (v != null) event.dataTransfer.setData(k, v);
  });
}

function DraggableRow({
  children,
  disabled,
  data,
  onBlocked,
  comfortable = false,
}: {
  children: React.ReactNode;
  disabled?: boolean;
  data: Record<string, string>;
  onBlocked?: () => void;
  /** Taller rows for main palette items (models, knowledge, connectors). */
  comfortable?: boolean;
}) {
  return (
    <Box
      draggable={!disabled}
      onDragStart={(e) => {
        if (disabled) {
          e.preventDefault();
          onBlocked?.();
          return;
        }
        applyDragData(e, data);
      }}
      style={{
        display: 'flex',
        alignItems: 'center',
        width: '100%',
        minWidth: 0,
        minHeight: comfortable ? PALETTE_ROW_MIN_HEIGHT : 36,
        padding: comfortable ? '0 14px' : '0 12px',
        boxSizing: 'border-box',
        gap: comfortable ? 10 : 8,
        cursor: disabled ? 'not-allowed' : 'grab',
        opacity: disabled ? 0.55 : 1,
        borderRadius: comfortable ? 'var(--radius-2)' : 'var(--radius-1)',
        border: comfortable ? '1px solid var(--olive-3)' : '1px solid transparent',
        backgroundColor: comfortable ? 'var(--olive-2)' : 'transparent',
      }}
      className={
        disabled
          ? 'agent-builder-draggable-row agent-builder-draggable-row--disabled'
          : 'agent-builder-draggable-row'
      }
    >
      {children}
    </Box>
  );
}

export function AgentBuilderSidebar(props: {
  open: boolean;
  width: number;
  loading: boolean;
  nodeTemplates: NodeTemplate[];
  configuredConnectors: Connector[];
  toolsets: BuilderSidebarToolset[];
  activeToolsetTypeKeys: Set<string>;
  toolsetMergeCheckNodes: ToolsetTypeKeyFlowNode[];
  refreshToolsets: (
    agentKey?: string | null,
    isServiceAccount?: boolean,
    search?: string
  ) => Promise<void>;
  onNotify: (message: string) => void;
  agentKey?: string | null;
  isServiceAccount?: boolean;
  onManageAgentToolsetCredentials?: (toolset: BuilderSidebarToolset) => void;
  /** Viewer without edit: lock models/KB/apps palette (no drag onto canvas). */
  paletteStructureLocked?: boolean;
  /** Message when a palette drag is blocked (depends on SA vs individual viewer). */
  paletteDragBlockedMessage?: string;
  /** SA viewer without edit: lock org toolset credential UI inside Tools. */
  toolsetsOrgCredentialLocked?: boolean;
  /** Web-search provider currently attached to the agent (null if none). */
  webSearchAttached: AgentWebSearchAttachment | null;
}) {
  const {
    open,
    width,
    loading,
    nodeTemplates,
    configuredConnectors,
    toolsets,
    activeToolsetTypeKeys,
    toolsetMergeCheckNodes,
    refreshToolsets,
    onNotify,
    agentKey = null,
    isServiceAccount = false,
    onManageAgentToolsetCredentials,
    paletteStructureLocked = false,
    paletteDragBlockedMessage = '',
    toolsetsOrgCredentialLocked = false,
    webSearchAttached,
  } = props;

  const { t } = useTranslation();
  const onPaletteDragBlocked = useCallback(() => {
    if (paletteDragBlockedMessage) onNotify(paletteDragBlockedMessage);
  }, [paletteDragBlockedMessage, onNotify]);
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    models: true,
    knowledge: true,
    'knowledge-apps': true,
    'knowledge-collections': true,
    tools: true,
  });

  const filtered = useMemo(() => filterTemplatesBySearch(nodeTemplates, search), [nodeTemplates, search]);
  const groupedConnectors = useMemo(() => groupConnectorInstances(configuredConnectors), [configuredConnectors]);
  const connectorTypeEntries = useMemo(() => Object.entries(groupedConnectors), [groupedConnectors]);

  const llmTemplates = filtered.filter((t) => t.category === 'llm');
  // kbGroup/appGroup are looked up from the unfiltered templates so that group section
  // headers remain visible when the user searches for individual items inside them.
  const kbGroup = nodeTemplates.find((t) => t.type === 'kb-group');
  const appGroup = nodeTemplates.find((t) => t.type === 'app-group');
  const kbIndividuals = filtered.filter(
    (t) => t.category === 'knowledge' && t.type.startsWith('kb-') && t.type !== 'kb-group'
  );

  const toggle = useCallback(
    (key: string, defaultWhenUnset: boolean = DEFAULT_KNOWLEDGE_NEST_EXPANDED) => {
      setExpanded((p) => toggleKeyedBoolean(p, key, defaultWhenUnset));
    },
    []
  );

  if (!open) return null;

  return (
    <Box
      style={{
        width,
        flexShrink: 0,
        borderRight: '1px solid var(--olive-3)',
        background: 'var(--olive-1)',
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      <Box
        style={{
          flexShrink: 0,
          borderBottom: '1px solid var(--olive-3)',
          background: 'var(--olive-1)',
        }}
      >
        <Flex align="start" gap="2" px="2" py="2" style={{ minHeight: HEADER_HEIGHT }}>
          <Box
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--radius-2)',
              background: 'var(--gray-3)',
              border: '1px solid var(--gray-5)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              marginTop: 2,
            }}
          >
            <MaterialIcon name="account_tree" size={ICON_SIZE_DEFAULT} color="var(--olive-11)" />
          </Box>
          <Box style={{ minWidth: 0, flex: 1, overflow: 'hidden' }}>
            <Text size="2" weight="medium" style={{ color: 'var(--olive-12)', lineHeight: 1.25 }}>
              {t('agentBuilder.palette')}
            </Text>
            <Text
              size="1"
              style={{
                display: 'block',
                color: 'var(--olive-11)',
                marginTop: 4,
                lineHeight: 1.4,
                whiteSpace: 'normal',
                overflowWrap: 'anywhere',
                wordBreak: 'break-word',
              }}
            >
              {paletteStructureLocked
                ? toolsetsOrgCredentialLocked
                  ? t('agentBuilder.paletteViewOnlyServiceAccount')
                  : t('agentBuilder.paletteViewerAuthenticateInTools')
                : t('agentBuilder.paletteDragHint')}
            </Text>
          </Box>
        </Flex>
        <Box px="2" pb="2" style={{ background: 'var(--olive-1)' }}>
          <TextField.Root
            placeholder={t('agentBuilder.searchNodes')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            disabled={paletteStructureLocked}
            size="2"
            style={{ width: '100%' }}
          >
            <TextField.Slot side="left">
              <MaterialIcon name="search" size={18} color="var(--olive-11)" />
            </TextField.Slot>
          </TextField.Root>
        </Box>
      </Box>

      <ScrollArea style={{ flex: 1, minHeight: 0, minWidth: 0 }} type="hover">
        <Box
          style={{
            padding: CONTENT_PADDING,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
            overflowX: 'hidden',
            minWidth: 0,
            maxWidth: '100%',
          }}
        >
          <SectionHeader
            title={t('agentBuilder.aiModels')}
            icon="auto_awesome"
            open={expanded.models}
            onToggle={() => toggle('models')}
          />
          {expanded.models ? (
            loading ? (
              <Box className="agent-builder-palette-nest">
                <AgentBuilderPaletteSkeletonList count={5} />
              </Box>
            ) : (
              <Box className="agent-builder-palette-nest">
                {llmTemplates.map((t) => {
                  const provider =
                    typeof t.defaultConfig?.provider === 'string' ? t.defaultConfig.provider : '';
                  const llmIconSrc = resolveLlmProviderIconPath(provider || undefined);
                  return (
                    <DraggableRow
                      key={t.type}
                      comfortable
                      data={prepareDragData(t)}
                      disabled={paletteStructureLocked}
                      onBlocked={paletteStructureLocked ? onPaletteDragBlocked : undefined}
                    >
                      <ThemeableAssetIcon
                        {...themeableAssetIconPresets.agentBuilderSidebar}
                        src={llmIconSrc}
                        size={PALETTE_ICON_SIZE}
                        fallbackSrc={AGENT_LLM_FALLBACK_ICON}
                      />
                      <span style={paletteRowLabelStyle}>{t.label}</span>
                    </DraggableRow>
                  );
                })}
              </Box>
            )
          ) : null}

          <SectionHeader
            title={t('agentBuilder.knowledge')}
            icon="menu_book"
            open={expanded.knowledge}
            onToggle={() => toggle('knowledge')}
          />
          {expanded.knowledge ? (
            loading ? (
              <Box className="agent-builder-palette-nest">
                <AgentBuilderPaletteSkeletonList count={7} />
              </Box>
            ) : (
            <Box className="agent-builder-palette-nest">
              {/* Apps group — drag the whole row to drop all connected apps as one node */}
              {appGroup ? (
                <SidebarCategoryRow
                  groupLabel={t('agentBuilder.groupApps')}
                  groupMaterialIcon="apps"
                  itemCount={configuredConnectors.length}
                  isExpanded={expanded['knowledge-apps'] ?? true}
                  onToggle={() => toggle('knowledge-apps')}
                  dragType={paletteStructureLocked ? undefined : 'app-group'}
                >
                  {configuredConnectors.length === 0 ? (
                    <Text size="1" style={{ color: 'var(--olive-11)', padding: '4px 8px', fontStyle: 'italic' }}>
                      {t('agentBuilder.noConnectors')}
                    </Text>
                  ) : (
                    connectorTypeEntries.map(([connectorTypeLabel, { instances, icon }]) => {
                      const expandKey = `knowledge-connector-${connectorTypeLabel}`;
                      return (
                        <SidebarCategoryRow
                          key={connectorTypeLabel}
                          groupLabel={connectorTypeLabel}
                          groupIcon={icon || undefined}
                          itemCount={instances.length}
                          isExpanded={expanded[expandKey] ?? true}
                          onToggle={() => toggle(expandKey)}
                        >
                          {instances.map((inst, instIdx) => {
                            const tmpl = nodeTemplates.find(
                              (n) => n.type === `app-${inst.name.toLowerCase().replace(/\s+/g, '-')}`
                            );
                            if (!tmpl) return null;
                            const dragData = prepareDragData(tmpl, 'connectors', {
                              connectorId: inst._key || '',
                              connectorType: inst.type || '',
                              scope: inst.scope || 'personal',
                            });
                            return (
                              <DraggableRow
                                key={`${connectorTypeLabel}-${instIdx}-${inst._key ?? inst.name ?? 'connector'}`}
                                comfortable
                                data={dragData}
                                disabled={paletteStructureLocked}
                                onBlocked={paletteStructureLocked ? onPaletteDragBlocked : undefined}
                              >
                                {icon ? (
                                  <Box style={{ flexShrink: 0, lineHeight: 0 }}>
                                    <ThemeableAssetIcon
                                      {...themeableAssetIconPresets.agentBuilderSidebar}
                                      src={icon}
                                      size={PALETTE_ICON_SIZE}
                                      fallbackSrc={AGENT_TOOLSET_FALLBACK_ICON}
                                    />
                                  </Box>
                                ) : (
                                  <MaterialIcon
                                    name="cloud"
                                    size={PALETTE_ICON_SIZE}
                                    color="var(--olive-11)"
                                    style={{ flexShrink: 0 }}
                                  />
                                )}
                                <span style={paletteRowLabelStyle}>
                                  {inst.name?.trim() || connectorTypeLabel}
                                </span>
                              </DraggableRow>
                            );
                          })}
                        </SidebarCategoryRow>
                      );
                    })
                  )}
                </SidebarCategoryRow>
              ) : null}

              {/* Collections group — drag the whole row to drop all KBs as one node */}
              {kbGroup ? (
                <SidebarCategoryRow
                  groupLabel={t('agentBuilder.groupCollections')}
                  groupMaterialIcon="folder"
                  itemCount={kbIndividuals.length}
                  isExpanded={expanded['knowledge-collections'] ?? true}
                  onToggle={() => toggle('knowledge-collections')}
                  dragType={paletteStructureLocked ? undefined : 'kb-group'}
                >
                  {kbIndividuals.length === 0 ? (
                    <Text size="1" style={{ color: 'var(--olive-11)', padding: '4px 8px', fontStyle: 'italic' }}>
                      {t('agentBuilder.noCollections')}
                    </Text>
                  ) : (
                    kbIndividuals.map((t) => (
                      <DraggableRow
                        key={t.type}
                        comfortable
                        data={prepareDragData(t)}
                        disabled={paletteStructureLocked}
                        onBlocked={paletteStructureLocked ? onPaletteDragBlocked : undefined}
                      >
                        <MaterialIcon
                          name="folder_open"
                          size={PALETTE_ICON_SIZE}
                          color="var(--olive-11)"
                          style={{ flexShrink: 0 }}
                        />
                        <span style={paletteRowLabelStyle}>{t.label}</span>
                      </DraggableRow>
                    ))
                  )}
                </SidebarCategoryRow>
              ) : null}
            </Box>
            )
          ) : null}

          <SectionHeader
            title={t('agentBuilder.tools')}
            icon="handyman"
            open={expanded.tools}
            onToggle={() => toggle('tools')}
          />
          {expanded.tools ? (
            <Box className="agent-builder-palette-nest" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <AgentBuilderToolsetsSection
                toolsets={toolsets}
                loading={loading}
                refreshToolsets={refreshToolsets}
                activeToolsetTypeKeys={activeToolsetTypeKeys}
                toolsetMergeCheckNodes={toolsetMergeCheckNodes}
                isServiceAccount={isServiceAccount}
                agentKey={agentKey}
                onManageAgentToolsetCredentials={onManageAgentToolsetCredentials}
                onNotify={onNotify}
                structureLocked={paletteStructureLocked}
                orgCredentialUiLocked={toolsetsOrgCredentialLocked}
                onPaletteStructureDragBlocked={onPaletteDragBlocked}
                webSearchAttached={webSearchAttached}
              />
            </Box>
          ) : null}
        </Box>
      </ScrollArea>
    </Box>
  );
}

function SectionHeader({
  title,
  icon,
  open,
  onToggle,
}: {
  title: string;
  icon?: string;
  open: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Flex
      align="start"
      justify="between"
      gap="2"
      mt="1"
      className="agent-builder-section-header"
      style={{ width: '100%', minWidth: 0 }}
      onClick={onToggle}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onToggle();
        }
      }}
    >
      <Flex align="start" gap="2" style={{ minWidth: 0, flex: 1 }}>
        {icon ? (
          <Box
            style={{
              width: 32,
              height: 32,
              borderRadius: 'var(--radius-2)',
              background: 'var(--olive-2)',
              border: '1px solid var(--olive-3)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <MaterialIcon name={icon} size={18} color="var(--olive-11)" />
          </Box>
        ) : null}
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            lineHeight: '20px',
            letterSpacing: '-0.01em',
            color: 'var(--olive-12)',
            minWidth: 0,
            whiteSpace: 'normal',
            overflowWrap: 'anywhere',
            wordBreak: 'break-word',
          }}
        >
          {title}
        </span>
      </Flex>
      <IconButton
        size="2"
        variant="ghost"
        color="gray"
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
        aria-label={open ? t('agentBuilder.collapse') : t('agentBuilder.expand')}
        style={{ flexShrink: 0 }}
      >
        <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={18} color="var(--olive-11)" />
      </IconButton>
    </Flex>
  );
}

'use client';

import React, { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, TextField, IconButton, ScrollArea } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ConnectorIcon } from '@/app/components/ui';
import { CONTENT_PADDING, HEADER_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { SkillForBuilder } from '../../types';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { NodeTemplate } from '../types';
import {
  filterMcpServerTools,
  filterPaletteGroup,
  filterToolsetTools,
  groupConnectorInstances,
  matchesPaletteSearch,
  prepareDragData,
} from '../sidebar-utils';
import type { ToolsetTypeKeyFlowNode } from '../sidebar-toolset-utils';
import { useSearchAwareExpanded } from '../sidebar-expand-utils';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { useWebSearchConfig } from '../hooks/use-web-search-config';
import { AGENT_LLM_FALLBACK_ICON, resolveLlmProviderIconPath } from '../display-utils';
import { ThemeableAssetIcon, themeableAssetIconPresets } from '@/app/components/ui/themeable-asset-icon';
import {
  useFeatureFlagsStore,
  selectMcpEnabled,
  selectActionsEnabled,
  selectSkillsEnabled,
} from '@/lib/store/feature-flags-store';
import { BetaBadge } from '@/app/components/ui/beta-badge';
import { visibleWebSearchProviders } from './sidebar-web-search-section';
import { AgentBuilderToolsetsSection } from './sidebar-toolsets-section';
import { AgentBuilderMcpSection } from './sidebar-mcp-section';
import { AgentBuilderSkillsSection } from './sidebar-skills-section';
import { SidebarCategoryRow } from './sidebar-category-row';
import { DraggableRow } from './sidebar-draggable-row';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';
import type { McpMyServerEntry } from '../../../workspace/mcp-servers/types';
import type { McpInstanceIdFlowNode } from '../sidebar-mcp-utils';
import type { AgentWebSearchAttachment } from '../types';

const PALETTE_ICON_SIZE = 20;

/** Default open state for accordion keys not seeded up front (e.g. `knowledge-connector-*`). */
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

export function AgentBuilderSidebar(props: {
  open: boolean;
  /** Inside the phone bottom sheet, which already shows the palette title. */
  inSheet?: boolean;
  width: number | string;
  loading: boolean;
  nodeTemplates: NodeTemplate[];
  availableSkills: SkillForBuilder[];
  configuredConnectors: Connector[];
  toolsets: BuilderSidebarToolset[];
  activeToolsetTypeKeys: Set<string>;
  toolsetMergeCheckNodes: ToolsetTypeKeyFlowNode[];
  refreshToolsets: (
    agentKey?: string | null,
    isServiceAccount?: boolean,
    search?: string
  ) => Promise<void>;
  mcpServers: McpMyServerEntry[];
  mcpMergeCheckNodes: McpInstanceIdFlowNode[];
  refreshMcpServers: () => Promise<void>;
  onNotify: (message: string) => void;
  agentKey?: string | null;
  isServiceAccount?: boolean;
  shareWithOrg?: boolean;
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
    availableSkills,
    configuredConnectors,
    toolsets,
    activeToolsetTypeKeys,
    toolsetMergeCheckNodes,
    refreshToolsets,
    mcpServers,
    mcpMergeCheckNodes,
    refreshMcpServers,
    onNotify,
    agentKey = null,
    isServiceAccount = false,
    shareWithOrg = false,
    onManageAgentToolsetCredentials,
    paletteStructureLocked = false,
    paletteDragBlockedMessage = '',
    toolsetsOrgCredentialLocked = false,
    webSearchAttached,
    inSheet = false,
  } = props;

  const { t } = useTranslation();
  const mcpEnabled = useFeatureFlagsStore(selectMcpEnabled);
  const actionsEnabled = useFeatureFlagsStore(selectActionsEnabled);
  const skillsEnabled = useFeatureFlagsStore(selectSkillsEnabled);
  const onPaletteDragBlocked = useCallback(() => {
    if (paletteDragBlockedMessage) onNotify(paletteDragBlockedMessage);
  }, [paletteDragBlockedMessage, onNotify]);
  const [search, setSearch] = useState('');
  const searching = search.trim() !== '';
  const { isOpen, toggle: toggleKey } = useSearchAwareExpanded(searching, {
    models: false,
    knowledge: true,
    'knowledge-apps': true,
    'knowledge-collections': true,
    tools: true,
    mcpServers: true,
    skills: true,
  });
  const toggle = (key: string) => toggleKey(key, DEFAULT_KNOWLEDGE_NEST_EXPANDED);
  const isSectionOpen = (key: string) => isOpen(key, DEFAULT_KNOWLEDGE_NEST_EXPANDED);

  // Every section filters by the palette query; a query that names a section or group shows all of it.
  const modelsTitle = t('agentBuilder.aiModels');
  const knowledgeTitle = t('agentBuilder.knowledge');
  const toolsTitle = t('agentBuilder.tools');
  const mcpTitle = t('agentBuilder.mcpServersSection');
  const skillsTitle = t('agentBuilder.skillsSection');
  const appsLabel = t('agentBuilder.groupApps');
  const collectionsLabel = t('agentBuilder.groupCollections');
  const webSearchLabel = t('agentBuilder.webSearch');
  const isAdmin = useUserStore(selectIsAdmin);
  // One load for the web search group and the match check below, so both see the same providers.
  const webSearchConfig = useWebSearchConfig(actionsEnabled);

  const llmTemplates = filterPaletteGroup(
    search,
    [modelsTitle],
    nodeTemplates.filter((tmpl) => tmpl.category === 'llm'),
    (tmpl) => [tmpl.label, tmpl.description, tmpl.defaultConfig?.provider as string, tmpl.defaultConfig?.modelName as string]
  );
  const visibleConnectors = filterPaletteGroup(
    search,
    [knowledgeTitle, appsLabel],
    configuredConnectors,
    (c) => [c.name, c.type, c.appGroup]
  );
  const connectorTypeEntries = Object.entries(groupConnectorInstances(visibleConnectors ?? []));

  const kbGroup = nodeTemplates.find((t) => t.type === 'kb-group');
  const appGroup = nodeTemplates.find((t) => t.type === 'app-group');
  const kbIndividuals = filterPaletteGroup(
    search,
    [knowledgeTitle, collectionsLabel],
    nodeTemplates.filter((t) => t.category === 'knowledge' && t.type.startsWith('kb-') && t.type !== 'kb-group'),
    (tmpl) => [tmpl.label, tmpl.description]
  );
  const skillTemplates = filterPaletteGroup(
    search,
    [skillsTitle],
    nodeTemplates.filter((t) => t.category === 'skills'),
    (tmpl) => [tmpl.label, tmpl.description, tmpl.defaultConfig?.skillCategory as string]
  );
  const toolsSearch = matchesPaletteSearch(search, toolsTitle) ? '' : search;
  const mcpSearch = matchesPaletteSearch(search, mcpTitle) ? '' : search;
  const toolsHasMatch =
    toolsets.some((ts) => filterToolsetTools(ts, toolsSearch) !== null) ||
    filterPaletteGroup(
      toolsSearch,
      [webSearchLabel],
      visibleWebSearchProviders(Boolean(isAdmin), webSearchConfig.configuredProviders),
      (meta) => [meta.label, meta.description]
    ) !== null;
  const mcpHasMatch = mcpServers.some((entry) => filterMcpServerTools(entry, mcpSearch) !== null);
  // While searching, sections with nothing to show are hidden (loading keeps them as skeletons).
  const showSection = (hasMatch: boolean) => !searching || loading || hasMatch;
  const showModels = showSection(llmTemplates !== null);
  const showKnowledge = showSection(
    (appGroup !== undefined && visibleConnectors !== null) || (kbGroup !== undefined && kbIndividuals !== null)
  );
  const showTools = actionsEnabled && showSection(toolsHasMatch);
  const showMcp = mcpEnabled && showSection(mcpHasMatch);
  const showSkills = skillsEnabled && showSection(skillTemplates !== null);
  const nothingMatches = !showModels && !showKnowledge && !showTools && !showMcp && !showSkills;

  const SHOW_MORE_LIMIT = 5;
  const [showAllKbCollectionsState, setShowAllKbCollections] = useState(false);
  const [showAllConnectorsState, setShowAllConnectors] = useState(false);
  // Matches never hide behind "Show more".
  const showAllKbCollections = showAllKbCollectionsState || searching;
  const showAllConnectors = showAllConnectorsState || searching;

  if (!open) return null;

  return (
    <Box
      style={{
        width,
        flexShrink: 0,
        borderRight: inSheet ? undefined : '1px solid var(--olive-3)',
        background: inSheet ? 'transparent' : 'var(--olive-1)',
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
          background: inSheet ? 'transparent' : 'var(--olive-1)',
        }}
      >
        <Flex align="start" gap="2" px="2" py="2" style={inSheet ? undefined : { minHeight: HEADER_HEIGHT }}>
          {inSheet ? null : <Box
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
          </Box>}
          <Box style={{ minWidth: 0, flex: 1, overflow: 'hidden' }}>
            {inSheet ? null : (
              <Text size="2" weight="medium" style={{ color: 'var(--olive-12)', lineHeight: 1.25 }}>
                {t('agentBuilder.palette')}
              </Text>
            )}
            <Text
              size="1"
              style={{
                display: 'block',
                color: 'var(--olive-11)',
                marginTop: inSheet ? 0 : 4,
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
                : inSheet
                  ? t('agentBuilder.paletteTapHint')
                  : t('agentBuilder.paletteDragHint')}
            </Text>
          </Box>
        </Flex>
        <Box px="2" pb="2" style={inSheet ? undefined : { background: 'var(--olive-1)' }}>
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
          {showModels ? (
          <SectionHeader
            title={modelsTitle}
            icon="auto_awesome"
            open={isSectionOpen('models')}
            onToggle={() => toggle('models')}
          />
          ) : null}
          {showModels && isSectionOpen('models') ? (
            loading ? (
              <Box className="agent-builder-palette-nest">
                <AgentBuilderPaletteSkeletonList count={5} />
              </Box>
            ) : (
              <Box className="agent-builder-palette-nest">
                {(llmTemplates ?? []).map((t) => {
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

          {showKnowledge ? (
          <SectionHeader
            title={knowledgeTitle}
            icon="menu_book"
            open={isSectionOpen('knowledge')}
            onToggle={() => toggle('knowledge')}
          />
          ) : null}
          {showKnowledge && isSectionOpen('knowledge') ? (
            loading ? (
              <Box className="agent-builder-palette-nest">
                <AgentBuilderPaletteSkeletonList count={7} />
              </Box>
            ) : (
            <Box className="agent-builder-palette-nest">
              {/* Apps group — drag the whole row to drop all connected apps as one node */}
              {appGroup && visibleConnectors !== null ? (
                <SidebarCategoryRow
                  groupLabel={appsLabel}
                  groupMaterialIcon="apps"
                  itemCount={visibleConnectors.length}
                  isExpanded={isSectionOpen('knowledge-apps')}
                  onToggle={() => toggle('knowledge-apps')}
                  dragType={paletteStructureLocked ? undefined : 'app-group'}
                >
                  {visibleConnectors.length === 0 ? (
                    <Text size="1" style={{ color: 'var(--olive-11)', padding: '4px 8px', fontStyle: 'italic' }}>
                      {t('agentBuilder.noConnectors')}
                    </Text>
                  ) : (
                    <>
                    {(showAllConnectors ? connectorTypeEntries : connectorTypeEntries.slice(0, SHOW_MORE_LIMIT)).map(([connectorTypeLabel, { instances, icon }]) => {
                      const expandKey = `knowledge-connector-${connectorTypeLabel}`;
                      const groupConnectorType = instances[0]?.type;

                      const renderInstance = (inst: typeof instances[0], instIdx: number) => {
                        const tmpl = nodeTemplates.find(
                          (n) => n.type === `app-${(inst.name || '').toLowerCase().replace(/\s+/g, '-')}`
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
                            <Box style={{ flexShrink: 0, lineHeight: 0 }}>
                              <ConnectorIcon
                                type={inst.type || 'generic'}
                                size={PALETTE_ICON_SIZE}
                              />
                            </Box>
                            <span style={paletteRowLabelStyle}>
                              {inst.name?.trim() || connectorTypeLabel}
                            </span>
                          </DraggableRow>
                        );
                      };

                      // Single instance: show the draggable row directly — no group wrapper or dropdown.
                      if (instances.length === 1) {
                        return (
                          <React.Fragment key={connectorTypeLabel}>
                            {renderInstance(instances[0], 0)}
                          </React.Fragment>
                        );
                      }

                      // Multiple instances: wrap in a collapsible group row.
                      return (
                        <SidebarCategoryRow
                          key={connectorTypeLabel}
                          groupLabel={connectorTypeLabel}
                          groupConnectorType={groupConnectorType}
                          groupIcon={icon || undefined}
                          itemCount={instances.length}
                          isExpanded={isSectionOpen(expandKey)}
                          onToggle={() => toggle(expandKey)}
                        >
                          {instances.map((inst, instIdx) => renderInstance(inst, instIdx))}
                        </SidebarCategoryRow>
                      );
                    })}
                    {!searching && connectorTypeEntries.length > SHOW_MORE_LIMIT && (
                      <button
                        type="button"
                        aria-expanded={showAllConnectors}
                        onClick={() => setShowAllConnectors((v) => !v)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          width: '100%',
                          gap: 4,
                          padding: '6px 8px',
                          cursor: 'pointer',
                          userSelect: 'none',
                          borderRadius: 'var(--radius-1)',
                          border: 'none',
                          background: 'none',
                          fontFamily: 'inherit',
                        }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--olive-a3)'; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                      >
                        <MaterialIcon
                          name={showAllConnectors ? 'expand_less' : 'expand_more'}
                          size={16}
                          color="var(--accent-9)"
                        />
                        <Text size="1" weight="medium" style={{ color: 'var(--accent-9)' }}>
                          {showAllConnectors ? t('agentBuilder.showLess') : `${t('agentBuilder.showMore')} (${connectorTypeEntries.length - SHOW_MORE_LIMIT})`}
                        </Text>
                      </button>
                    )}
                    </>
                  )}
                </SidebarCategoryRow>
              ) : null}

              {/* Collections group — drag the whole row to drop all KBs as one node */}
              {kbGroup && kbIndividuals !== null ? (
                <SidebarCategoryRow
                  groupLabel={collectionsLabel}
                  groupMaterialIcon="folder"
                  itemCount={kbIndividuals.length}
                  isExpanded={isSectionOpen('knowledge-collections')}
                  onToggle={() => toggle('knowledge-collections')}
                  dragType={paletteStructureLocked ? undefined : 'kb-group'}
                >
                  {kbIndividuals.length === 0 ? (
                    <Text size="1" style={{ color: 'var(--olive-11)', padding: '4px 8px', fontStyle: 'italic' }}>
                      {t('agentBuilder.noCollections')}
                    </Text>
                  ) : (
                    <>
                    {(showAllKbCollections ? kbIndividuals : kbIndividuals.slice(0, SHOW_MORE_LIMIT)).map((t) => (
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
                    ))}
                    {!searching && kbIndividuals.length > SHOW_MORE_LIMIT && (
                      <button
                        type="button"
                        aria-expanded={showAllKbCollections}
                        onClick={() => setShowAllKbCollections((v) => !v)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          width: '100%',
                          gap: 4,
                          padding: '6px 8px',
                          cursor: 'pointer',
                          userSelect: 'none',
                          borderRadius: 'var(--radius-1)',
                          border: 'none',
                          background: 'none',
                          fontFamily: 'inherit',
                        }}
                        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'var(--olive-a3)'; }}
                        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.backgroundColor = 'transparent'; }}
                      >
                        <MaterialIcon
                          name={showAllKbCollections ? 'expand_less' : 'expand_more'}
                          size={16}
                          color="var(--accent-9)"
                        />
                        <Text size="1" weight="medium" style={{ color: 'var(--accent-9)' }}>
                          {showAllKbCollections ? t('agentBuilder.showLess') : `${t('agentBuilder.showMore')} (${kbIndividuals.length - SHOW_MORE_LIMIT})`}
                        </Text>
                      </button>
                    )}
                    </>
                  )}
                </SidebarCategoryRow>
              ) : null}
            </Box>
            )
          ) : null}

          {showTools ? (
            <>
              <SectionHeader
                title={toolsTitle}
                icon="handyman"
                open={isSectionOpen('tools')}
                onToggle={() => toggle('tools')}
              />
              {isSectionOpen('tools') ? (
                <Box className="agent-builder-palette-nest" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <AgentBuilderToolsetsSection
                    toolsets={toolsets}
                    search={toolsSearch}
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
                    webSearchConfig={webSearchConfig}
                    agentShared={isServiceAccount || shareWithOrg}
                  />
                </Box>
              ) : null}
            </>
          ) : null}

          {showMcp ? (
            <>
              <SectionHeader
                title={mcpTitle}
                icon="hub"
                open={isSectionOpen('mcpServers')}
                onToggle={() => toggle('mcpServers')}
              />
              {isSectionOpen('mcpServers') ? (
                <Box className="agent-builder-palette-nest" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <AgentBuilderMcpSection
                    mcpServers={mcpServers}
                    search={mcpSearch}
                    loading={loading}
                    refreshMcpServers={refreshMcpServers}
                    mcpMergeCheckNodes={mcpMergeCheckNodes}
                    isServiceAccount={isServiceAccount}
                    agentShared={isServiceAccount || shareWithOrg}
                    agentKey={agentKey}
                    onNotify={onNotify}
                    structureLocked={paletteStructureLocked}
                    onPaletteStructureDragBlocked={onPaletteDragBlocked}
                  />
                </Box>
              ) : null}
            </>
          ) : null}

          {showSkills ? (
            <>
              <SectionHeader
                title={skillsTitle}
                icon="psychology"
                open={isSectionOpen('skills')}
                onToggle={() => toggle('skills')}
                beta
              />
              {isSectionOpen('skills') ? (
                <AgentBuilderSkillsSection
                  availableSkills={availableSkills}
                  skillTemplates={skillTemplates ?? []}
                  loading={loading}
                  structureLocked={paletteStructureLocked}
                  onPaletteStructureDragBlocked={onPaletteDragBlocked}
                />
              ) : null}
            </>
          ) : null}

          {nothingMatches ? (
            <Text size="1" role="status" style={{ color: 'var(--olive-11)', fontStyle: 'italic', padding: '4px 8px' }}>
              {t('message.noResults')}
            </Text>
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
  beta = false,
}: {
  title: string;
  icon?: string;
  open: boolean;
  onToggle: () => void;
  beta?: boolean;
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
        {beta ? <BetaBadge /> : null}
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

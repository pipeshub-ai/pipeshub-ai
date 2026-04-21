'use client';

import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, TextField, IconButton, ScrollArea } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { CONTENT_PADDING, HEADER_HEIGHT, ICON_SIZE_DEFAULT } from '@/app/components/sidebar';
import type { Connector } from '@/app/(main)/workspace/connectors/types';
import type { BuilderSidebarToolset } from '../../toolsets-api';
import type { NodeTemplate } from '../types';
import { filterTemplatesBySearch, groupConnectorInstances, prepareDragData } from '../sidebar-utils';
import { AgentBuilderToolsetsSection } from './sidebar-toolsets-section';
import { SidebarCategoryRow } from './sidebar-category-row';
import { AgentBuilderPaletteSkeletonList } from './agent-builder-palette-skeleton';

const PALETTE_ROW_MIN_HEIGHT = 44;
const PALETTE_ICON_SIZE = 20;

const paletteRowLabelStyle: React.CSSProperties = {
  flex: 1,
  fontSize: 15,
  fontWeight: 500,
  lineHeight: '22px',
  color: 'var(--olive-12)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
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
  activeToolsetTypes: string[];
  toolsetsHasMore: boolean;
  toolsetsLoadingMore: boolean;
  onLoadMoreToolsets: () => void;
  refreshToolsets: (
    agentKey?: string | null,
    isServiceAccount?: boolean,
    search?: string
  ) => Promise<void>;
  onNotify: (message: string) => void;
  agentKey?: string | null;
  isServiceAccount?: boolean;
  onManageAgentToolsetCredentials?: (toolset: BuilderSidebarToolset) => void;
}) {
  const {
    open,
    width,
    loading,
    nodeTemplates,
    configuredConnectors,
    toolsets,
    activeToolsetTypes,
    toolsetsHasMore,
    toolsetsLoadingMore,
    onLoadMoreToolsets,
    refreshToolsets,
    onNotify,
    agentKey = null,
    isServiceAccount = false,
    onManageAgentToolsetCredentials,
  } = props;

  const { t } = useTranslation();
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

  const agentTemplates = filtered.filter((t) => t.category === 'agent');
  const llmTemplates = filtered.filter((t) => t.category === 'llm');
  // kbGroup/appGroup are looked up from the unfiltered templates so that group section
  // headers remain visible when the user searches for individual items inside them.
  const kbGroup = nodeTemplates.find((t) => t.type === 'kb-group');
  const appGroup = nodeTemplates.find((t) => t.type === 'app-group');
  const kbIndividuals = filtered.filter(
    (t) => t.category === 'knowledge' && t.type.startsWith('kb-') && t.type !== 'kb-group'
  );

  const toggle = (k: string) => setExpanded((p) => ({ ...p, [k]: !p[k] }));

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
        <Flex
          align="center"
          gap="2"
          px="2"
          style={{ height: HEADER_HEIGHT, minHeight: HEADER_HEIGHT }}
        >
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
            }}
          >
            <MaterialIcon name="account_tree" size={ICON_SIZE_DEFAULT} color="var(--olive-11)" />
          </Box>
          <Box style={{ minWidth: 0 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--olive-12)', lineHeight: 1.2 }}>
              {t('agentBuilder.palette')}
            </Text>
            <Text size="1" style={{ display: 'block', color: 'var(--olive-11)', marginTop: 1, lineHeight: 1.2 }}>
              {t('agentBuilder.paletteDragHint')}
            </Text>
          </Box>
        </Flex>
        <Box px="2" pb="2" style={{ background: 'var(--olive-1)' }}>
          <TextField.Root
            placeholder={t('agentBuilder.searchNodes')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            size="2"
            style={{ width: '100%' }}
          >
            <TextField.Slot side="left">
              <MaterialIcon name="search" size={18} color="var(--olive-11)" />
            </TextField.Slot>
          </TextField.Root>
        </Box>
      </Box>

      <ScrollArea style={{ flex: 1, minHeight: 0 }} type="hover">
        <Box
          style={{
            padding: CONTENT_PADDING,
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <SectionHeader
            title={t('agentBuilder.coreNodeTitle')}
            icon="account_tree"
            open={expanded.agent ?? true}
            onToggle={() => toggle('agent')}
          />
          {expanded.agent ?? true ? (
            loading ? (
              <Box className="agent-builder-palette-nest">
                <AgentBuilderPaletteSkeletonList count={1} />
              </Box>
            ) : (
              <Box className="agent-builder-palette-nest">
                {agentTemplates.map((template) => (
                  <DraggableRow key={template.type} comfortable data={prepareDragData(template)}>
                    <MaterialIcon name="auto_awesome" size={PALETTE_ICON_SIZE} color="var(--olive-11)" />
                    <span style={paletteRowLabelStyle}>{template.label}</span>
                  </DraggableRow>
                ))}
              </Box>
            )
          ) : null}

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
                {llmTemplates.map((t) => (
                  <DraggableRow key={t.type} comfortable data={prepareDragData(t)}>
                    <MaterialIcon name="psychology" size={PALETTE_ICON_SIZE} color="var(--olive-11)" />
                    <span style={paletteRowLabelStyle}>{t.label}</span>
                  </DraggableRow>
                ))}
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
                  dragType="app-group"
                >
                  {configuredConnectors.length === 0 ? (
                    <Text size="1" style={{ color: 'var(--olive-11)', padding: '4px 8px', fontStyle: 'italic' }}>
                      {t('agentBuilder.noConnectors')}
                    </Text>
                  ) : (
                    Object.entries(groupedConnectors).map(([typeName, { instances, icon }]) => {
                      const expandKey = `knowledge-connector-${typeName}`;
                      if (instances.length === 1) {
                        const inst = instances[0];
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
                          <DraggableRow key={typeName} comfortable data={dragData}>
                            {icon ? (
                              <img
                                src={icon}
                                width={PALETTE_ICON_SIZE}
                                height={PALETTE_ICON_SIZE}
                                alt=""
                                style={{ objectFit: 'contain', flexShrink: 0 }}
                              />
                            ) : (
                              <MaterialIcon name="cloud" size={PALETTE_ICON_SIZE} color="var(--olive-11)" />
                            )}
                            <span style={paletteRowLabelStyle}>{typeName}</span>
                          </DraggableRow>
                        );
                      }
                      return (
                        <SidebarCategoryRow
                          key={typeName}
                          groupLabel={typeName}
                          groupIcon={icon || undefined}
                          itemCount={instances.length}
                          isExpanded={expanded[expandKey] ?? false}
                          onToggle={() => toggle(expandKey)}
                        >
                          {instances.map((inst) => {
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
                              <DraggableRow key={inst._key} comfortable data={dragData}>
                                {icon ? (
                                  <img
                                    src={icon}
                                    width={PALETTE_ICON_SIZE}
                                    height={PALETTE_ICON_SIZE}
                                    alt=""
                                    style={{ objectFit: 'contain', flexShrink: 0 }}
                                  />
                                ) : (
                                  <MaterialIcon name="cloud" size={PALETTE_ICON_SIZE} color="var(--olive-11)" />
                                )}
                                <span style={paletteRowLabelStyle}>{inst.name}</span>
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
                  dragType="kb-group"
                >
                  {kbIndividuals.length === 0 ? (
                    <Text size="1" style={{ color: 'var(--olive-11)', padding: '4px 8px', fontStyle: 'italic' }}>
                      {t('agentBuilder.noCollections')}
                    </Text>
                  ) : (
                    kbIndividuals.map((t) => (
                      <DraggableRow key={t.type} comfortable data={prepareDragData(t)}>
                        <MaterialIcon name="folder_open" size={PALETTE_ICON_SIZE} color="var(--olive-11)" />
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
            <Box className="agent-builder-palette-nest">
              <AgentBuilderToolsetsSection
                toolsets={toolsets}
                loading={loading}
                refreshToolsets={refreshToolsets}
                loadMoreToolsets={onLoadMoreToolsets}
                toolsetsHasMore={toolsetsHasMore}
                toolsetsLoadingMore={toolsetsLoadingMore}
                activeToolsetTypes={activeToolsetTypes}
                isServiceAccount={isServiceAccount}
                agentKey={agentKey}
                onManageAgentToolsetCredentials={onManageAgentToolsetCredentials}
                onNotify={onNotify}
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
      align="center"
      justify="between"
      gap="2"
      mt="1"
      className="agent-builder-section-header"
      style={{ width: '100%' }}
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
      <Flex align="center" gap="2" style={{ minWidth: 0, flex: 1 }}>
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
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
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

'use client';

import { asArray, lower } from '@/lib/utils/safe';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Button, Flex, Grid, Heading, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { WorkspaceHeaderIconButton, HeaderOverflowMenu } from '../../../components';
import { McpAddServerButton } from '../../components';
import type { McpMyServerEntry, McpServerTemplate } from '../../types';
import { isOfferedForNewServers, replacementFor } from '../../catalog-replacement';
import { McpInstanceCard } from './mcp-instance-card';
import { McpServerCard } from './mcp-server-card';
import { EnterpriseFeatureNotice } from '@/config';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { pagePaddingX } from '@/app/components/ui/responsive';
import { MOBILE_PAGE_PADDING_TOP, PAGE_PADDING_TOP } from '@/app/(main)/workspace/components/settings-page-container';
import { SegmentedTabs } from '@/app/components/ui/segmented-tabs';

// ========================================
// Types
// ========================================

interface TemplateRow {
  template: McpServerTemplate;
  instances: McpMyServerEntry[];
}

interface McpCatalogLayoutProps {
  templates: McpServerTemplate[];
  instances: McpMyServerEntry[];
  isLoading: boolean;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  /** Header "Add Server" button — always creates a custom (non-catalog) server. */
  onAddCustom: () => void;
  /** Registry card "+ Setup" — creates the first instance of this template. */
  onSetupTemplate: (template: McpServerTemplate) => void;
  /** Active card "+" — creates another instance of an already-configured template. */
  onAddInstanceForTemplate: (template: McpServerTemplate) => void;
  /** Active card badge / body click — navigate to the type-detail page for this template. */
  onManageTemplate: (template: McpServerTemplate) => void;
  onEditInstance: (instance: McpMyServerEntry) => void;
  onDeleteInstance: (instance: McpMyServerEntry) => void;
  onRefresh: () => void;
  /** "Your MCP servers →" link to the personal page, as on the other team catalogs. */
  onOpenPersonal?: () => void;
}

const TABS = ['all', 'configured', 'notConfigured'] as const;
type Tab = (typeof TABS)[number];

// ========================================
// Component
// ========================================

export function McpCatalogLayout({
  templates,
  instances,
  isLoading,
  searchQuery,
  onSearchChange,
  onAddCustom,
  onSetupTemplate,
  onAddInstanceForTemplate,
  onManageTemplate,
  onEditInstance,
  onDeleteInstance,
  onRefresh,
  onOpenPersonal,
}: McpCatalogLayoutProps) {
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  const [tab, setTab] = useState<Tab>('all');

  // Group instances by typeId — custom instances (no typeId) never merge into a template card.
  const { templateRows, customInstances } = useMemo(() => {
    const instancesByType = new Map<string, McpMyServerEntry[]>();
    const custom: McpMyServerEntry[] = [];
    for (const instance of instances) {
      if (instance.typeId) {
        const list = instancesByType.get(instance.typeId) ?? [];
        list.push(instance);
        instancesByType.set(instance.typeId, list);
      } else {
        custom.push(instance);
      }
    }
    const rows: TemplateRow[] = templates.map((template) => ({
      template,
      instances: instancesByType.get(template.typeId) ?? [],
    }));
    return { templateRows: rows, customInstances: custom };
  }, [templates, instances]);

  // Configured first (like connectors), then alphabetical within each group.
  const { activeTemplateRows, inactiveTemplateRows, filteredCustomInstances } = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const byName = (a: string, b: string) =>
      a.localeCompare(b, undefined, { sensitivity: 'base' });

    const rows = !q
      ? templateRows
      : templateRows.filter(
          ({ template }) =>
            lower(template.displayName).includes(q) ||
            lower(template.description).includes(q) ||
            asArray(template.tags).some((tag) => lower(tag).includes(q))
        );

    const active = rows
      .filter((r) => r.instances.length > 0)
      .sort((a, b) => byName(a.template.displayName, b.template.displayName));
    // A replaced entry appears only where servers made from it still exist.
    const inactive = rows
      .filter((r) => r.instances.length === 0 && isOfferedForNewServers(r.template))
      .sort((a, b) => byName(a.template.displayName, b.template.displayName));

    const custom = (!q
      ? customInstances
      : customInstances.filter(
          (i) => i.name.toLowerCase().includes(q) || (i.description ?? '').toLowerCase().includes(q)
        )
    ).slice().sort((a, b) => byName(a.name, b.name));

    return {
      activeTemplateRows: active,
      inactiveTemplateRows: inactive,
      filteredCustomInstances: custom,
    };
  }, [templateRows, customInstances, searchQuery]);

  const hasAnyContent = templateRows.length > 0 || instances.length > 0;
  const configuredCount = activeTemplateRows.length + filteredCustomInstances.length;
  const counts: Record<Tab, number> = {
    all: configuredCount + inactiveTemplateRows.length,
    configured: configuredCount,
    notConfigured: inactiveTemplateRows.length,
  };
  const showConfigured = tab !== 'notConfigured';
  const showNotConfigured = tab !== 'configured';
  const hasResults = counts[tab] > 0;

  // Phones get a light text link; wider screens keep the soft button.
  const personalLink = onOpenPersonal && (
    <Button
      size="2"
      variant={isMobile ? 'ghost' : 'soft'}
      color="gray"
      className="touch-hit"
      onClick={onOpenPersonal}
      style={{ cursor: 'pointer' }}
    >
      {t('workspace.sidebar.nav.yourMcpServers')}
      <MaterialIcon name="arrow_forward" size={16} color="var(--gray-11)" />
    </Button>
  );

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: isMobile ? MOBILE_PAGE_PADDING_TOP : PAGE_PADDING_TOP,
        paddingBottom: 64,
        paddingInline: pagePaddingX(100),
        overflowY: 'auto',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >
      <Flex justify="between" align="start" wrap="wrap" gap="3" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: '1 1 240px', minWidth: 0 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.mcpServers.team.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.mcpServers.team.subtitle')}
          </Text>
        </Flex>

        {/* Phones: compact actions under the subtitle, link on the right */}
        {isMobile && (
          <Flex align="center" justify="between" wrap="wrap" gap="3" style={{ width: '100%' }}>
            <Flex align="center" gap="2">
              <McpAddServerButton onClick={onAddCustom} />
              <HeaderOverflowMenu items={[{ label: t('action.refresh'), icon: 'refresh', onSelect: onRefresh }]} />
            </Flex>
            {personalLink}
          </Flex>
        )}

        {hasAnyContent && (
          <Box width={{ initial: '100%', sm: '224px' }} flexShrink="0">
            <TextField.Root
              size="2"
              placeholder={t('workspace.mcpServers.searchPlaceholder')}
              value={searchQuery}
              onChange={(e) => onSearchChange(e.target.value)}
              style={{ width: '100%' }}
            >
              <TextField.Slot>
                <MaterialIcon name="search" size={16} color="var(--gray-9)" />
              </TextField.Slot>
            </TextField.Root>
          </Box>
        )}
      </Flex>

      <Flex align="center" justify="between" gap="3" wrap="wrap" style={{ width: '100%' }}>
        {/* Phones: scroll the tab row sideways instead of pushing it off-screen */}
        <Box className="no-scrollbar tab-scroll-fade" style={{ maxWidth: '100%', overflowX: 'auto' }}>
          <SegmentedTabs
            value={tab}
            onValueChange={(value) => setTab(value)}
            items={TABS.map((id) => ({ value: id, label: `${t(`workspace.actions.tabs.${id}`)} (${counts[id]})` }))}
            style={{ minWidth: 'max-content' }}
          />
        </Box>
        {!isMobile && (
          <Flex align="center" gap="2" wrap="wrap" style={{ flexShrink: 0 }}>
            {personalLink}
            <McpAddServerButton onClick={onAddCustom} />
            <WorkspaceHeaderIconButton icon="refresh" onClick={onRefresh} aria-label={t('action.refresh')} />
          </Flex>
        )}
      </Flex>

      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.mcpServers.loading')} />
        </Flex>
      ) : !hasAnyContent ? (
        <EmptyState onAdd={onAddCustom} />
      ) : !hasResults ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <Text size="2" style={{ color: 'var(--gray-10)' }}>
            {searchQuery.trim() ? t('workspace.mcpServers.noResults') : t('workspace.mcpServers.personal.noneInTab')}
          </Text>
        </Flex>
      ) : (
        <Grid columns={{ initial: '1', sm: '2', md: '3', lg: '4' }} gap="4" style={{ width: '100%' }}>
          {showConfigured && activeTemplateRows.map(({ template, instances: templateInstances }) => (
            <McpServerCard
              key={template.typeId}
              title={template.displayName}
              description={template.description}
              iconUrl={template.icon}
              variant="active"
              instanceCount={templateInstances.length}
              onSetup={() => onSetupTemplate(template)}
              onAddInstance={() => onAddInstanceForTemplate(template)}
              onManage={() => onManageTemplate(template)}
              canAddInstance={isOfferedForNewServers(template)}
              notice={
                isOfferedForNewServers(template)
                  ? null
                  : t('workspace.mcpServers.replacedNotice', {
                      name: replacementFor(template, templates)?.displayName ?? template.replacedBy,
                    })
              }
            />
          ))}
          {showConfigured && filteredCustomInstances.map((instance) => (
            <McpInstanceCard
              key={instance._id}
              instance={instance}
              onEdit={() => onEditInstance(instance)}
              onDelete={() => onDeleteInstance(instance)}
            />
          ))}
          {showNotConfigured && inactiveTemplateRows.map(({ template }) => (
            <McpServerCard
              key={template.typeId}
              title={template.displayName}
              description={template.description}
              iconUrl={template.icon}
              variant="registry"
              instanceCount={0}
              onSetup={() => onSetupTemplate(template)}
              onAddInstance={() => onAddInstanceForTemplate(template)}
              onManage={() => onManageTemplate(template)}
            />
          ))}
        </Grid>
      )}

      {!isLoading && (
        <EnterpriseFeatureNotice
          title={t('workspace.mcpServers.enterprise.userServersTitle')}
          description={t('workspace.mcpServers.enterprise.userServersDescription')}
        />
      )}
    </Flex>
  );
}

// ========================================
// Sub-components
// ========================================

function EmptyState({ onAdd }: { onAdd: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" align="center" justify="center" gap="3" style={{ width: '100%', flex: 1, paddingTop: 80 }}>
      <MaterialIcon name="hub" size={48} color="var(--gray-9)" />
      <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
        {t('workspace.mcpServers.team.emptyTitle')}
      </Text>
      <Text size="2" style={{ color: 'var(--gray-11)' }}>
        {t('workspace.mcpServers.team.emptyDescription')}
      </Text>
      <McpAddServerButton onClick={onAdd} />
    </Flex>
  );
}

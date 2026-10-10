'use client';

import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Grid, Heading, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { WorkspaceHeaderIconButton } from '../../../components';
import { McpAddServerButton } from '../../components';
import type { McpMyServerEntry, McpPersonalInstanceSummary, McpServerTemplate } from '../../types';
import { isOfferedForNewServers, replacementFor } from '../../catalog-replacement';
import { McpInstanceCard } from './mcp-instance-card';
import { McpServerCard } from './mcp-server-card';
import { McpUserCreatedSection } from './mcp-user-created-section';

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
  /** Every user's personal servers, for review. */
  userCreatedInstances: McpPersonalInstanceSummary[];
  ownerNames: Record<string, string>;
  onDeleteUserCreated: (instance: McpPersonalInstanceSummary) => void;
}

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
  userCreatedInstances,
  ownerNames,
  onDeleteUserCreated,
}: McpCatalogLayoutProps) {
  const { t } = useTranslation();

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
            template.displayName.toLowerCase().includes(q) ||
            template.description.toLowerCase().includes(q) ||
            template.tags.some((tag) => tag.toLowerCase().includes(q))
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

  const filteredUserCreated = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return userCreatedInstances
      .filter((i) => !q || i.name.toLowerCase().includes(q))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  }, [userCreatedInstances, searchQuery]);

  const hasAnyContent = templateRows.length > 0 || instances.length > 0;
  const hasResults =
    activeTemplateRows.length > 0 ||
    inactiveTemplateRows.length > 0 ||
    filteredCustomInstances.length > 0;

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: 64,
        paddingBottom: 64,
        paddingLeft: 100,
        paddingRight: 100,
        overflowY: 'auto',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >
      <Flex justify="between" align="start" gap="2" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: 1 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {t('workspace.mcpServers.team.title')}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.mcpServers.team.subtitle')}
          </Text>
        </Flex>

        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <McpAddServerButton onClick={onAddCustom} />
          <WorkspaceHeaderIconButton icon="refresh" onClick={onRefresh} />
        </Flex>
      </Flex>

      {hasAnyContent && (
        <TextField.Root
          size="2"
          placeholder={t('workspace.mcpServers.searchPlaceholder')}
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ maxWidth: 360 }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--gray-9)" />
          </TextField.Slot>
        </TextField.Root>
      )}

      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <LottieLoader variant="loader" size={48} showLabel label={t('workspace.mcpServers.loading')} />
        </Flex>
      ) : !hasAnyContent ? (
        <EmptyState onAdd={onAddCustom} />
      ) : !hasResults ? (
        <Flex align="center" justify="center" style={{ width: '100%', flex: 1 }}>
          <Text size="2" style={{ color: 'var(--gray-10)' }}>{t('workspace.mcpServers.noResults')}</Text>
        </Flex>
      ) : (
        <Grid columns={{ initial: '2', md: '3', lg: '4' }} gap="4" style={{ width: '100%' }}>
          {activeTemplateRows.map(({ template, instances: templateInstances }) => (
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
          {filteredCustomInstances.map((instance) => (
            <McpInstanceCard
              key={instance._id}
              instance={instance}
              onEdit={() => onEditInstance(instance)}
              onDelete={() => onDeleteInstance(instance)}
            />
          ))}
          {inactiveTemplateRows.map(({ template }) => (
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
        <McpUserCreatedSection
          instances={filteredUserCreated}
          ownerNames={ownerNames}
          onDelete={onDeleteUserCreated}
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

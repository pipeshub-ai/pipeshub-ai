'use client';

import { useTranslation } from 'react-i18next';
import { Box, Button, Callout, Flex, Heading, SegmentedControl, Spinner, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { BuilderSidebarToolset } from '@/app/(main)/toolsets/api';
import type { RegistryToolsetRow } from '@/app/(main)/toolsets/api';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import { ToolsetInstanceRowCard } from './toolset-instance-row-card';

export type ActionInstanceAuthTab = 'all' | 'authenticated' | 'not_authenticated';

export interface ActionTypeInstanceFilter {
  tab: ActionInstanceAuthTab;
  onTabChange: (tab: ActionInstanceAuthTab) => void;
  counts: { all: number; authenticated: number; notAuthenticated: number };
  search: string;
  onSearchChange: (value: string) => void;
  onRefresh?: () => void;
  showInfoBanner?: boolean;
}

export interface ActionTypeDetailsPagination {
  page: number;
  totalPages: number;
  hasNext: boolean;
  hasPrev: boolean;
  onPageChange: (page: number) => void;
}

export interface ActionTypeDetailsLayoutProps {
  /** Breadcrumb parent label: team vs personal actions. */
  scope: 'team' | 'personal';
  registryRow: RegistryToolsetRow | null;
  instances: BuilderSidebarToolset[];
  isLoading: boolean;
  /** In-place update (e.g. after OAuth): keep list visible with a light busy state instead of a full loading swap. */
  listRefreshing?: boolean;
  onBack: () => void;
  /** When omitted (e.g. personal browse), the add button is hidden. */
  onAddInstance?: () => void;
  onOpenDocs?: () => void;
  onAuthenticateInstance: (instance: BuilderSidebarToolset) => void;
  /** Opens user credential / sign-in panel (personal). */
  onConfigureInstance: (instance: BuilderSidebarToolset) => void;
  /** Admin: OAuth app / instance management (gear). */
  onManageInstance?: (instance: BuilderSidebarToolset) => void;
  /** When set, shows instance tabs, search, refresh, and optional banner (admin team detail). */
  instanceFilter?: ActionTypeInstanceFilter | null;
  /** Server-paginated instance list (type detail). */
  pagination?: ActionTypeDetailsPagination | null;
}

export function ActionTypeDetailsLayout({
  scope,
  registryRow,
  instances,
  isLoading,
  listRefreshing = false,
  onBack,
  onAddInstance,
  onOpenDocs,
  onAuthenticateInstance,
  onConfigureInstance,
  onManageInstance,
  instanceFilter,
  pagination = null,
}: ActionTypeDetailsLayoutProps) {
  const { t } = useTranslation();
  const scopeBackLabel =
    scope === 'team' ? t('workspace.actions.typeDetail.scopeTeam') : t('workspace.actions.typeDetail.scopePersonal');
  const title = registryRow?.displayName || registryRow?.name || '';
  const description = registryRow?.description || '';

  const tabItems = instanceFilter
    ? [
        {
          value: 'all' as const,
          label: t('workspace.actions.typeDetail.instanceTabs.all', {
            count: instanceFilter.counts.all,
          }),
        },
        {
          value: 'authenticated' as const,
          label: t('workspace.actions.typeDetail.instanceTabs.authenticated', {
            count: instanceFilter.counts.authenticated,
          }),
        },
        {
          value: 'not_authenticated' as const,
          label: t('workspace.actions.typeDetail.instanceTabs.notAuthenticated', {
            count: instanceFilter.counts.notAuthenticated,
          }),
        },
      ]
    : [];

  return (
    <Flex
      direction="column"
      gap="5"
      style={{
        width: '100%',
        height: '100%',
        paddingTop: 32,
        paddingBottom: 64,
        paddingLeft: 100,
        paddingRight: 100,
        overflowY: 'auto',
      }}
    >
      <Flex align="center" gap="2">
        <button
          type="button"
          onClick={onBack}
          style={{
            appearance: 'none',
            margin: 0,
            padding: 0,
            border: 'none',
            outline: 'none',
            background: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          <MaterialIcon name="arrow_back" size={16} color="var(--gray-11)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {scopeBackLabel}
          </Text>
        </button>
        <MaterialIcon name="chevron_right" size={14} color="var(--gray-9)" />
        <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
          {title}
        </Text>
      </Flex>

      <Flex justify="between" align="start" gap="4" style={{ width: '100%' }} wrap="wrap">
        <Flex align="center" gap="4" style={{ minWidth: 0 }}>
          <Flex
            align="center"
            justify="center"
            style={{
              width: 48,
              height: 48,
              borderRadius: 'var(--radius-3)',
              border: '1px solid var(--gray-a4)',
              backgroundColor: 'var(--gray-2)',
              flexShrink: 0,
              overflow: 'hidden',
            }}
          >
            {registryRow?.iconPath ? (
              <img src={registryRow.iconPath} alt="" width={32} height={32} style={{ objectFit: 'contain' }} />
            ) : (
              <MaterialIcon name="bolt" size={28} color="var(--gray-11)" />
            )}
          </Flex>
          <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
            <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {title}
            </Heading>
            {description ? (
              <Text size="2" style={{ color: 'var(--gray-11)', maxWidth: 720 }}>
                {description}
              </Text>
            ) : null}
          </Flex>
        </Flex>

        <Flex align="center" gap="2" wrap="wrap">
          {instanceFilter?.onRefresh ? (
            <button
              type="button"
              onClick={() => instanceFilter.onRefresh?.()}
              disabled={listRefreshing}
              aria-busy={listRefreshing}
              aria-label={
                listRefreshing
                  ? t('workspace.actions.typeDetail.refreshingList')
                  : t('workspace.actions.typeDetail.refresh')
              }
              style={{
                appearance: 'none',
                margin: 0,
                padding: 8,
                border: '1px solid var(--gray-a4)',
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'transparent',
                cursor: listRefreshing ? 'default' : 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                minWidth: 36,
                minHeight: 36,
                opacity: listRefreshing ? 0.85 : 1,
                transition: 'opacity 160ms ease',
              }}
            >
              {listRefreshing ? (
                <Spinner size="2" style={{ color: 'var(--gray-11)' }} />
              ) : (
                <MaterialIcon name="refresh" size={18} color="var(--gray-11)" />
              )}
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => {
              if (onOpenDocs) onOpenDocs();
              else window.open(EXTERNAL_LINKS.documentation, '_blank', 'noopener,noreferrer');
            }}
            style={{
              appearance: 'none',
              margin: 0,
              padding: '6px 12px',
              border: '1px solid var(--gray-a4)',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
            <Text size="2" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.actions.documentation')}
            </Text>
          </button>
          {onAddInstance ? (
            <Button variant="solid" size="2" onClick={onAddInstance} style={{ cursor: 'pointer' }}>
              <MaterialIcon name="add" size={16} color="white" />
              {t('workspace.actions.typeDetail.addInstance')}
            </Button>
          ) : null}
        </Flex>
      </Flex>

      {instanceFilter ? (
        <Flex align="center" justify="between" gap="4" wrap="wrap" style={{ width: '100%' }}>
          <SegmentedControl.Root
            size="2"
            value={instanceFilter.tab}
            disabled={listRefreshing}
            onValueChange={(v) => instanceFilter.onTabChange(v as ActionInstanceAuthTab)}
          >
            {tabItems.map((item) => (
              <SegmentedControl.Item key={item.value} value={item.value}>
                {item.label}
              </SegmentedControl.Item>
            ))}
          </SegmentedControl.Root>
          <TextField.Root
            placeholder={t('workspace.actions.typeDetail.searchInstances')}
            value={instanceFilter.search}
            disabled={listRefreshing}
            onChange={(e) => instanceFilter.onSearchChange(e.target.value)}
            style={{ minWidth: 220, maxWidth: 360, flex: '0 1 360px' }}
          >
            <TextField.Slot side="right">
              <MaterialIcon name="search" size={16} color="var(--gray-9)" />
            </TextField.Slot>
          </TextField.Root>
        </Flex>
      ) : null}

      {isLoading && instances.length === 0 ? (
        <Flex align="center" justify="center" gap="3" style={{ paddingTop: 80 }}>
          <Spinner size="2" style={{ color: 'var(--gray-11)' }} />
          <Text size="2" style={{ color: 'var(--gray-9)' }}>
            {t('workspace.actions.loading')}
          </Text>
        </Flex>
      ) : !isLoading && instances.length === 0 ? (
        <Flex direction="column" align="center" justify="center" gap="2" style={{ paddingTop: 80 }}>
          <MaterialIcon name="extension" size={48} color="var(--gray-9)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {t('workspace.actions.typeDetail.empty')}
          </Text>
        </Flex>
      ) : (
        <Box style={{ width: '100%' }}>
          {listRefreshing ? (
            <Flex align="center" gap="2" mb="3" role="status" aria-live="polite">
              <Spinner size="1" style={{ color: 'var(--gray-10)' }} />
              <Text size="1" style={{ color: 'var(--gray-10)', letterSpacing: '0.02em' }}>
                {t('workspace.actions.typeDetail.refreshingList')}
              </Text>
            </Flex>
          ) : null}
          <Flex
            direction="column"
            gap="4"
            style={{
              opacity: listRefreshing ? 0.9 : 1,
              transition: 'opacity 200ms ease-out',
              pointerEvents: listRefreshing ? 'none' : 'auto',
            }}
          >
            {instanceFilter ? null : (
              <Text size="2" weight="medium" style={{ color: 'var(--gray-11)' }}>
                {t('workspace.actions.typeDetail.instancesHeading', { count: instances.length })}
              </Text>
            )}
            {instances.map((inst) => (
              <ToolsetInstanceRowCard
                key={inst.instanceId || inst.instanceName}
                instance={inst}
                onAuthenticate={() => onAuthenticateInstance(inst)}
                onConfigure={() => onConfigureInstance(inst)}
                onManage={onManageInstance ? () => onManageInstance(inst) : undefined}
              />
            ))}
            {pagination && pagination.totalPages > 1 ? (
              <Flex align="center" justify="center" gap="3" mt="4" wrap="wrap">
                <Button
                  type="button"
                  variant="outline"
                  color="gray"
                  size="2"
                  disabled={!pagination.hasPrev || listRefreshing}
                  onClick={() => pagination.onPageChange(pagination.page - 1)}
                >
                  {t('workspace.actions.typeDetail.paginationPrevious')}
                </Button>
                <Text size="2" color="gray">
                  {t('workspace.actions.typeDetail.paginationPage', {
                    current: pagination.page,
                    total: pagination.totalPages,
                  })}
                </Text>
                <Button
                  type="button"
                  variant="outline"
                  color="gray"
                  size="2"
                  disabled={!pagination.hasNext || listRefreshing}
                  onClick={() => pagination.onPageChange(pagination.page + 1)}
                >
                  {t('workspace.actions.typeDetail.paginationNext')}
                </Button>
              </Flex>
            ) : null}
          </Flex>
        </Box>
      )}

      {instanceFilter?.showInfoBanner ? (
        <Callout.Root color="green" style={{ marginTop: 8 }}>
          <Callout.Icon>
            <MaterialIcon name="info" size={16} />
          </Callout.Icon>
          <Callout.Text>
            {scope === 'team'
              ? t('workspace.actions.typeDetail.teamInstanceInfo')
              : t('workspace.actions.typeDetail.personalInstanceInfo')}
          </Callout.Text>
        </Callout.Root>
      ) : null}
    </Flex>
  );
}

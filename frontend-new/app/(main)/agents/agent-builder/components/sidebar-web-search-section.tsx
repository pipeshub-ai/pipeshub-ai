'use client';

import React, { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, IconButton, Text, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  ThemeableAssetIcon,
  themeableAssetIconPresets,
} from '@/app/components/ui/themeable-asset-icon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { ConfigurePanel } from '../../../workspace/web-search/components';
import {
  WEB_SEARCH_PROVIDER_META,
  type ConfigurableProvider,
  type ConfiguredWebSearchProvider,
  type WebSearchProviderMeta,
} from '../../../workspace/web-search/types';
import { SidebarCategoryRow } from './sidebar-category-row';
import { useWebSearchConfig } from '../hooks/use-web-search-config';
import type { AgentWebSearchAttachment } from '../types';

export interface AgentBuilderWebSearchSectionProps {
  attached: AgentWebSearchAttachment | null;
  onNotify: (message: string) => void;
  structureLocked?: boolean;
}

function buildWebSearchDragData(
  meta: WebSearchProviderMeta,
  configured: ConfiguredWebSearchProvider | null,
): Record<string, string> {
  return {
    'application/reactflow': 'web-search',
    provider: meta.type,
    providerKey: configured?.providerKey ?? '',
    providerLabel: meta.label,
    iconPath: meta.icon,
  };
}

export function AgentBuilderWebSearchSection({
  attached,
  onNotify,
  structureLocked = false,
}: AgentBuilderWebSearchSectionProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(true);
  const [configurePanel, setConfigurePanel] = useState<{
    provider: ConfigurableProvider;
    meta: WebSearchProviderMeta;
  } | null>(null);

  const isAdmin = useUserStore(selectIsAdmin);
  const { configuredProviders, loading, reload } = useWebSearchConfig(true);

  const providerMap = useMemo(() => {
    const map = new Map<string, ConfiguredWebSearchProvider>();
    for (const p of configuredProviders) map.set(p.provider, p);
    return map;
  }, [configuredProviders]);

  const visibleProviders = useMemo(() => {
    if (isAdmin) return WEB_SEARCH_PROVIDER_META;
    return WEB_SEARCH_PROVIDER_META.filter(
      (meta) => !meta.configurable || providerMap.has(meta.type),
    );
  }, [isAdmin, providerMap]);

  const handleToggle = useCallback(() => {
    setExpanded((prev) => !prev);
  }, []);

  const handleConfigureClick = useCallback(
    (meta: WebSearchProviderMeta) => {
      if (!meta.configurable) return;
      if (structureLocked) {
        onNotify(t('agentBuilder.webSearchLocked'));
        return;
      }
      setConfigurePanel({
        provider: meta.type as ConfigurableProvider,
        meta,
      });
    },
    [onNotify, structureLocked, t],
  );

  const handlePanelClose = useCallback(() => {
    setConfigurePanel(null);
  }, []);

  const handlePanelSaveSuccess = useCallback(async () => {
    setConfigurePanel(null);
    await reload();
  }, [reload]);

  const handlePanelDeleteSuccess = useCallback(async () => {
    setConfigurePanel(null);
    await reload();
  }, [reload]);

  const existingConfigureProvider = configurePanel
    ? (providerMap.get(configurePanel.provider) ?? null)
    : null;

  const parentStatus = attached ? ('authenticated' as const) : undefined;

  return (
    <>
      <SidebarCategoryRow
        groupLabel={t('Web Search')}
        groupMaterialIcon="public"
        groupMaterialIconColor="var(--blue-9)"
        itemCount={visibleProviders.length}
        isExpanded={expanded}
        onToggle={handleToggle}
        toolsetStatus={parentStatus}
      >
        {loading ? (
          <Text
            size="1"
            style={{
              color: 'var(--slate-11)',
              padding: '4px 8px',
              fontStyle: 'italic',
            }}
          >
            {t('agentBuilder.webSearchLoading')}
          </Text>
        ) : (
          visibleProviders.map((meta) => {
            const configured = providerMap.get(meta.type) ?? null;
            const isAttached = attached?.provider === meta.type;
            const isConfigured = !meta.configurable || Boolean(configured);
            const dragBlocked =
              structureLocked || !isConfigured || Boolean(attached);

            return (
              <ProviderRow
                key={meta.type}
                meta={meta}
                configured={configured}
                isConfigured={isConfigured}
                isAttached={isAttached}
                dragBlocked={dragBlocked}
                structureLocked={structureLocked}
                anotherAttached={Boolean(attached) && !isAttached}
                onDragBlocked={() => {
                  if (structureLocked) {
                    onNotify(t('agentBuilder.webSearchLocked'));
                  } else if (!isConfigured) {
                    onNotify(
                      t('agentBuilder.webSearchConfigureFirst', {
                        name: meta.label,
                      }),
                    );
                  } else if (attached) {
                    onNotify(t('Only One Web Search Provider Allowed'));
                  }
                }}
                isAdmin={Boolean(isAdmin)}
                onConfigure={() => handleConfigureClick(meta)}
              />
            );
          })
        )}
      </SidebarCategoryRow>

      {isAdmin && (
        <ConfigurePanel
          open={Boolean(configurePanel)}
          provider={configurePanel?.provider ?? null}
          providerMeta={configurePanel?.meta ?? null}
          existingProvider={existingConfigureProvider}
          onClose={handlePanelClose}
          onSaveSuccess={handlePanelSaveSuccess}
          onDeleteSuccess={handlePanelDeleteSuccess}
        />
      )}
    </>
  );
}

function ProviderRow({
  meta,
  configured,
  isConfigured,
  isAttached,
  dragBlocked,
  structureLocked,
  anotherAttached,
  isAdmin,
  onDragBlocked,
  onConfigure,
}: {
  meta: WebSearchProviderMeta;
  configured: ConfiguredWebSearchProvider | null;
  isConfigured: boolean;
  isAttached: boolean;
  dragBlocked: boolean;
  structureLocked: boolean;
  anotherAttached: boolean;
  isAdmin: boolean;
  onDragBlocked: () => void;
  onConfigure: () => void;
}) {
  const { t } = useTranslation();
  const dimmed = (dragBlocked && !isAttached) || (anotherAttached && !isAttached);

  return (
    <Box mb="1">
      <Flex
        align="center"
        gap="2"
        px="2"
        py="1"
        mx="1"
        draggable
        onDragStart={(e) => {
          if (dragBlocked) {
            e.preventDefault();
            onDragBlocked();
            return;
          }
          e.dataTransfer.effectAllowed = 'move';
          const data = buildWebSearchDragData(meta, configured);
          Object.entries(data).forEach(([k, v]) => {
            e.dataTransfer.setData(k, v);
          });
        }}
        style={{
          minHeight: 32,
          borderRadius: 'var(--radius-1)',
          userSelect: 'none',
          cursor: dragBlocked
            ? isAttached
              ? 'default'
              : 'not-allowed'
            : 'grab',
          opacity: dimmed ? 0.55 : 1,
        }}
        className={
          dragBlocked
            ? 'agent-builder-draggable-row agent-builder-draggable-row--disabled'
            : 'agent-builder-draggable-row'
        }
      >
        {meta.iconType === 'image' ? (
          <ThemeableAssetIcon
            {...themeableAssetIconPresets.agentBuilderCategoryRow}
            src={meta.icon}
            size={18}
          />
        ) : (
          <MaterialIcon
            name={meta.icon}
            size={18}
            color="var(--slate-11)"
            style={{ flexShrink: 0 }}
          />
        )}

        <Text
          size="2"
          style={{
            flex: 1,
            minWidth: 0,
            color: 'var(--slate-12)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {meta.label}
        </Text>

        {isAttached ? (
          <Tooltip content={t('Attached')}>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                flexShrink: 0,
              }}
            >
              <MaterialIcon
                name="check_circle"
                size={16}
                color="var(--accent-11)"
              />
            </span>
          </Tooltip>
        ) : isConfigured ? (
          <Tooltip content={t('Configured')}>
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                flexShrink: 0,
              }}
            >
              <MaterialIcon
                name="check_circle"
                size={16}
                color="var(--green-9)"
              />
            </span>
          </Tooltip>
        ) : null}

        {meta.configurable && isAdmin ? (
          <Box
            onClick={(e) => e.stopPropagation()}
            onPointerDown={(e) => e.stopPropagation()}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              flexShrink: 0,
            }}
          >
            <Tooltip
              content={
                isConfigured
                  ? t('Edit')
                  : t('Configure')
              }
            >
              <IconButton
                type="button"
                size="1"
                variant="ghost"
                color="gray"
                onClick={(e) => {
                  e.stopPropagation();
                  onConfigure();
                }}
                disabled={structureLocked}
              >
                <MaterialIcon
                  name="settings"
                  size={16}
                  color={
                    isConfigured ? 'var(--slate-11)' : 'var(--amber-11)'
                  }
                />
              </IconButton>
            </Tooltip>
          </Box>
        ) : null}
      </Flex>
    </Box>
  );
}

'use client';

import React, { useCallback, useMemo } from 'react';
import type { TFunction } from 'i18next';
import { Box, Button, Flex, Grid, Heading, IconButton, SegmentedControl, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import type { AIModelProvider, ConfiguredModel } from '../types';
import type { CapabilitySection } from '../types';
import { CAPABILITY_SECTION_ORDER, LLM_SECTION_MODEL_TYPES } from '../types';
import type { MainSection } from '../store';
import { aiModelsCapabilityBadge, aiModelsCapabilityLabel, aiModelsCapabilitySectionTab } from '../capability-i18n';
import { ProviderRow } from './provider-card';
import { ConfiguredModelsGrid } from './configured-models-grid';

export type ProviderGridLayout = 'page' | 'embedded';

interface ProviderGridProps {
  providers: AIModelProvider[];
  configuredModels: Record<string, ConfiguredModel[]>;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  mainSection: MainSection;
  onMainSectionChange: (section: MainSection) => void;
  capabilitySection: CapabilitySection;
  onCapabilitySectionChange: (section: CapabilitySection) => void;
  onAdd: (provider: AIModelProvider, capability: string) => void;
  onEdit: (provider: AIModelProvider, capability: string, model: ConfiguredModel) => void;
  onSetDefault: (modelType: string, modelKey: string) => void;
  onDelete: (modelType: string, modelKey: string, modelName: string) => void;
  isLoading?: boolean;
  onRefresh: () => void;
  /** Full workspace page vs compact onboarding-style panel. */
  layout?: ProviderGridLayout;
  /** When unset, true for `page` layout and false for `embedded`. */
  showPageHeader?: boolean;
  /** When unset, `all` for page layout and `hidden` for embedded. */
  capabilityTabs?: 'all' | 'hidden';
  /** When true, hide capability chips on each provider row. */
  hideCapabilityBadges?: boolean;
  /** Passed to configured-models list; false hides built-in embedding row (e.g. onboarding). */
  showEmbeddingBuiltinPlaceholder?: boolean;
}

function modelTypesForSection(section: CapabilitySection): readonly string[] {
  if (section === 'text_generation') return LLM_SECTION_MODEL_TYPES;
  if (section === 'embedding') return ['embedding'];
  if (section === 'tts') return ['tts'];
  if (section === 'stt') return ['stt'];
  return ['imageGeneration'];
}

function providerMatchesSearch(
  provider: AIModelProvider,
  q: string,
  t: TFunction
): boolean {
  if (!q.trim()) return true;
  const qq = q.toLowerCase();
  if (provider.name.toLowerCase().includes(qq)) return true;
  if (provider.providerId.toLowerCase().includes(qq)) return true;
  if (provider.description.toLowerCase().includes(qq)) return true;
  if ((provider.notice ?? '').toLowerCase().includes(qq)) return true;
  for (const cap of provider.capabilities) {
    const dn = aiModelsCapabilityLabel(t, cap).toLowerCase();
    const badge = aiModelsCapabilityBadge(t, cap).toLowerCase();
    if (dn.includes(qq) || badge.includes(qq)) return true;
  }
  return false;
}

export function ProviderGrid({
  providers,
  configuredModels,
  searchQuery,
  onSearchChange,
  mainSection,
  onMainSectionChange,
  capabilitySection,
  onCapabilitySectionChange,
  onAdd,
  onEdit,
  onSetDefault,
  onDelete,
  isLoading = false,
  onRefresh,
  layout = 'page',
  showPageHeader: showPageHeaderProp,
  capabilityTabs: capabilityTabsProp,
  hideCapabilityBadges = false,
  showEmbeddingBuiltinPlaceholder = true,
}: ProviderGridProps) {
  const { t } = useTranslation();
  const isEmbedded = layout === 'embedded';
  const showPageHeader = showPageHeaderProp ?? !isEmbedded;
  const capabilityTabs = capabilityTabsProp ?? (isEmbedded ? 'hidden' : 'all');

  const matchSearch = useCallback(
    (p: AIModelProvider, q: string) => providerMatchesSearch(p, q, t),
    [t]
  );

  const capabilityFilteredProviders = useMemo(
    () => providers.filter((p) => p.capabilities.includes(capabilitySection)),
    [providers, capabilitySection]
  );

  const searchFilteredProviders = useMemo(
    () => capabilityFilteredProviders.filter((p) => matchSearch(p, searchQuery)),
    [capabilityFilteredProviders, searchQuery, matchSearch]
  );

  const configuredCount = useMemo(() => {
    const types = modelTypesForSection(capabilitySection);
    let total = 0;
    for (const mt of types) {
      total += (configuredModels[mt] ?? []).length;
    }
    return total;
  }, [configuredModels, capabilitySection]);

  const pageSearchFieldStyle: React.CSSProperties = {
    width: '100%',
    minWidth: 0,
    maxWidth: 720,
    flex: '1 1 260px',
    boxSizing: 'border-box',
  };

  const searchField = (
    <TextField.Root
      size="2"
      placeholder={t('workspace.aiModels.searchPlaceholder')}
      value={searchQuery}
      onChange={(e) => onSearchChange(e.target.value)}
      style={
        isEmbedded
          ? { width: '100%', maxWidth: 320, minWidth: 160, flex: '1 1 200px', boxSizing: 'border-box' }
          : showPageHeader
            ? pageSearchFieldStyle
            : { width: 320, minWidth: 200, flexShrink: 0, boxSizing: 'border-box' }
      }
    >
      <TextField.Slot>
        <MaterialIcon name="search" size={16} color="var(--gray-9)" />
      </TextField.Slot>
    </TextField.Root>
  );

  const refreshButton = (
    <IconButton
      variant="outline"
      color="gray"
      size="2"
      title={t('workspace.aiModels.refreshTitle')}
      style={{ cursor: 'pointer' }}
      onClick={onRefresh}
    >
      <MaterialIcon name="refresh" size={18} color="var(--gray-11)" />
    </IconButton>
  );

  const pagePaddingX = 'clamp(var(--space-4), 4vw, 100px)';
  const pagePaddingY = 'clamp(var(--space-6), 3vw, 64px)';

  /** Page: one outer scroll (workspace shell). Embedded: fill panel and scroll inside. */
  const rootLayoutStyle: React.CSSProperties = isEmbedded
    ? { height: '100%', overflowY: 'auto' }
    : { minHeight: '100%', overflowY: 'visible' };

  return (
    <Flex
      direction="column"
      gap={isEmbedded ? '4' : '5'}
      style={{
        width: '100%',
        minWidth: 0,
        boxSizing: 'border-box',
        ...rootLayoutStyle,
        ...(isEmbedded
          ? {
            paddingTop: 0,
            paddingBottom: 0,
            paddingLeft: 0,
            paddingRight: 0,
          }
          : {
            paddingTop: pagePaddingY,
            paddingBottom: pagePaddingY,
            paddingLeft: pagePaddingX,
            paddingRight: pagePaddingX,
          }),
      }}
    >
      {showPageHeader ? (
        <Flex justify="between" align="start" gap="4" style={{ width: '100%', flexWrap: 'wrap', flexShrink: 0 }}>
          <Flex direction="column" gap="2" style={{ flex: 1, minWidth: 200 }}>
            <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
              {t('workspace.aiModels.title')}
            </Heading>
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {t('workspace.aiModels.subtitle')}
            </Text>
          </Flex>

          <Flex align="center" gap="3" wrap="wrap" justify="end">
            <Button
              variant="outline"
              color="gray"
              size="2"
              style={{ cursor: 'pointer', gap: 6 }}
              onClick={() =>
                window.open(`${EXTERNAL_LINKS.documentation}ai-models/overview`, '_blank', 'noopener,noreferrer')
              }
            >
              <MaterialIcon name="open_in_new" size={16} color="var(--gray-11)" />
              {t('workspace.aiModels.documentation')}
            </Button>
            {refreshButton}
          </Flex>
        </Flex>
      ) : null}

      {/* Primary sections: page = tabs + search; embedded = tabs + refresh + search */}
      <Flex
        direction={{ initial: 'column', sm: 'row' }}
        align={{ initial: 'stretch', sm: 'center' }}
        justify={{ initial: 'start', sm: 'between' }}
        gap="3"
        style={{ width: '100%', minWidth: 0, flexShrink: 0 }}
      >
        <Box width={{ initial: '100%', sm: 'auto' }} minWidth="0">
          <SegmentedControl.Root
            value={mainSection}
            onValueChange={(v) => onMainSectionChange(v as MainSection)}
            size="2"
            style={{ width: '100%' }}
          >
            <SegmentedControl.Item value="providers">
              {t('workspace.aiModels.mainSectionProviders')}
            </SegmentedControl.Item>
            <SegmentedControl.Item value="configured">
              {t('workspace.aiModels.mainSectionConfigured')}
              {configuredCount > 0 ? (
                <Text
                  as="span"
                  size="1"
                  weight="medium"
                  style={{
                    marginLeft: 6,
                    padding: '1px 6px',
                    borderRadius: 999,
                    backgroundColor: 'var(--gray-a4)',
                    color: 'var(--gray-12)',
                    lineHeight: '16px',
                  }}
                >
                  {configuredCount}
                </Text>
              ) : null}
            </SegmentedControl.Item>
          </SegmentedControl.Root>
        </Box>
        {showPageHeader ? (
          <Flex
            align="center"
            gap="3"
            wrap="wrap"
            justify={{ initial: 'start', sm: 'end' }}
            style={{ flex: 1, minWidth: 0, width: '100%', alignSelf: 'stretch' }}
          >
            {searchField}
          </Flex>
        ) : (
          <Flex
            align="center"
            gap="3"
            wrap="wrap"
            justify={{ initial: 'start', sm: 'end' }}
            style={{ flex: 1, minWidth: 0, width: '100%', alignSelf: 'stretch' }}
          >
            {refreshButton}
            {searchField}
          </Flex>
        )}
      </Flex>

      {capabilityTabs === 'all' ? (
        <Box
          style={{
            width: '100%',
            minWidth: 0,
            flexShrink: 0,
            overflowX: 'auto',
            overflowY: 'hidden',
            WebkitOverflowScrolling: 'touch',
            borderBottom: '1px solid var(--olive-3)',
          }}
        >
          <Flex gap="1" wrap="nowrap" style={{ width: 'max-content' }}>
            {CAPABILITY_SECTION_ORDER.map((tabId) => {
              const active = capabilitySection === tabId;
              return (
                <button
                  key={tabId}
                  type="button"
                  onClick={() => onCapabilitySectionChange(tabId)}
                  style={{
                    appearance: 'none',
                    margin: 0,
                    font: 'inherit',
                    cursor: 'pointer',
                    padding: '10px 16px',
                    border: 'none',
                    background: 'transparent',
                    color: active ? 'var(--gray-12)' : 'var(--gray-10)',
                    fontWeight: active ? 600 : 500,
                    fontSize: 14,
                    borderBottom: active ? '2px solid var(--accent-9)' : '2px solid transparent',
                    marginBottom: -1,
                    whiteSpace: 'nowrap',
                    flexShrink: 0,
                  }}
                >
                  {aiModelsCapabilitySectionTab(t, tabId)}
                </button>
              );
            })}
          </Flex>
        </Box>
      ) : null}

      {/* Body */}
      {mainSection === 'providers' ? (
        isLoading ? (
          <Flex align="center" justify="center" style={{ width: '100%', paddingTop: 80 }}>
            <Text size="2" style={{ color: 'var(--gray-9)' }}>
              {t('workspace.aiModels.loadingProviders')}
            </Text>
          </Flex>
        ) : searchFilteredProviders.length === 0 ? (
          <Flex
            direction="column"
            align="center"
            justify="center"
            gap="2"
            style={{ width: '100%', paddingTop: 80 }}
          >
            <MaterialIcon name="smart_toy" size={48} color="var(--gray-9)" />
            <Text size="2" style={{ color: 'var(--gray-11)' }}>
              {t('workspace.aiModels.emptyProviders')}
            </Text>
          </Flex>
        ) : (
          <Grid columns={{ initial: '1', md: '2' }} gap="4" style={{ width: '100%' }}>
            {searchFilteredProviders.map((provider) => (
              <ProviderRow
                key={provider.providerId}
                provider={provider}
                onConfigure={() => onAdd(provider, capabilitySection)}
                hideCapabilityBadges={hideCapabilityBadges}
              />
            ))}
          </Grid>
        )
      ) : (
        <ConfiguredModelsGrid
          providers={providers}
          configuredModels={configuredModels}
          capabilitySection={capabilitySection}
          searchQuery={searchQuery}
          onEdit={onEdit}
          onSetDefault={onSetDefault}
          onDelete={onDelete}
          isLoading={isLoading}
          showEmbeddingBuiltinPlaceholder={showEmbeddingBuiltinPlaceholder}
        />
      )}
    </Flex>
  );
}

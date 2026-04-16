'use client';

import React, { useMemo } from 'react';
import { Flex, Grid, Heading, SegmentedControl, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { AIModelProvider, ConfiguredModel } from '../types';
import { CAPABILITY_TO_MODEL_TYPE, CAPABILITY_DISPLAY_NAMES } from '../types';
import { ProviderCard } from './provider-card';

type FilterTab = 'all' | 'configured' | 'not_configured';

interface ProviderGridProps {
  providers: AIModelProvider[];
  configuredModels: Record<string, ConfiguredModel[]>;
  searchQuery: string;
  onSearchChange: (q: string) => void;
  activeTab: FilterTab;
  onTabChange: (tab: FilterTab) => void;
  onAdd: (provider: AIModelProvider, capability: string) => void;
  onEdit: (provider: AIModelProvider, capability: string, model: ConfiguredModel) => void;
  onSetDefault: (modelType: string, modelKey: string) => void;
  onDelete: (modelType: string, modelKey: string, modelName: string) => void;
  isLoading?: boolean;
}

const TABS: { value: FilterTab; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'configured', label: 'Configured' },
  { value: 'not_configured', label: 'Not Configured' },
];

export function ProviderGrid({
  providers,
  configuredModels,
  searchQuery,
  onSearchChange,
  activeTab,
  onTabChange,
  onAdd,
  onEdit,
  onSetDefault,
  onDelete,
  isLoading = false,
}: ProviderGridProps) {
  const allModels = useMemo(() => {
    const flat: ConfiguredModel[] = [];
    for (const [modelType, models] of Object.entries(configuredModels)) {
      for (const m of models) {
        flat.push({ ...m, modelType: m.modelType || modelType });
      }
    }
    return flat;
  }, [configuredModels]);

  const providerConfiguredCount = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const p of providers) {
      let count = 0;
      for (const cap of p.capabilities) {
        const mt = CAPABILITY_TO_MODEL_TYPE[cap];
        if (!mt) continue;
        count += (configuredModels[mt] ?? []).filter(
          (m) => m.provider === p.providerId
        ).length;
      }
      counts[p.providerId] = count;
    }
    return counts;
  }, [providers, configuredModels]);

  const tabFiltered = useMemo(() => {
    switch (activeTab) {
      case 'configured':
        return providers.filter((p) => (providerConfiguredCount[p.providerId] ?? 0) > 0);
      case 'not_configured':
        return providers.filter((p) => (providerConfiguredCount[p.providerId] ?? 0) === 0);
      default:
        return providers;
    }
  }, [providers, activeTab, providerConfiguredCount]);

  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return tabFiltered;
    const q = searchQuery.toLowerCase();
    return tabFiltered.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        p.providerId.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q)
    );
  }, [tabFiltered, searchQuery]);

  const tabCounts = useMemo(() => {
    const applySearch = (list: AIModelProvider[]) => {
      if (!searchQuery.trim()) return list;
      const q = searchQuery.toLowerCase();
      return list.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.providerId.toLowerCase().includes(q) ||
          p.description.toLowerCase().includes(q)
      );
    };
    const base = applySearch(providers);
    return {
      all: base.length,
      configured: base.filter((p) => (providerConfiguredCount[p.providerId] ?? 0) > 0).length,
      not_configured: base.filter((p) => (providerConfiguredCount[p.providerId] ?? 0) === 0).length,
    };
  }, [providers, searchQuery, providerConfiguredCount]);

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
      }}
    >
      {/* Header */}
      <Flex justify="between" align="start" gap="2" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: 1 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            AI Models
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            Configure AI model providers for text generation, embeddings, and more
          </Text>
        </Flex>

        <TextField.Root
          size="2"
          placeholder="Search..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ width: 224, flexShrink: 0 }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--gray-9)" />
          </TextField.Slot>
        </TextField.Root>
      </Flex>

      {/* Tabs */}
      <Flex align="center" justify="between" style={{ width: '100%' }}>
        <SegmentedControl.Root
          value={activeTab}
          onValueChange={(v) => onTabChange(v as FilterTab)}
          size="2"
        >
          {TABS.map((tab) => (
            <SegmentedControl.Item key={tab.value} value={tab.value}>
              {tab.label} ({tabCounts[tab.value] ?? 0})
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
      </Flex>

      {/* Grid */}
      {isLoading ? (
        <Flex align="center" justify="center" style={{ width: '100%', paddingTop: 80 }}>
          <Text size="2" style={{ color: 'var(--gray-9)' }}>Loading providers…</Text>
        </Flex>
      ) : filtered.length === 0 ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          gap="2"
          style={{ width: '100%', paddingTop: 80 }}
        >
          <MaterialIcon name="smart_toy" size={48} color="var(--gray-9)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            No providers found
          </Text>
        </Flex>
      ) : (
        <Grid columns={{ initial: '2', md: '3', lg: '3' }} gap="4" style={{ width: '100%' }}>
          {filtered.map((provider) => (
            <ProviderCard
              key={provider.providerId}
              provider={provider}
              configuredModels={allModels}
              onAdd={onAdd}
              onEdit={onEdit}
              onSetDefault={onSetDefault}
              onDelete={onDelete}
            />
          ))}
        </Grid>
      )}
    </Flex>
  );
}

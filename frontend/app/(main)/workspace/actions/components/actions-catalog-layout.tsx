'use client';

import React, { useMemo } from 'react';
import { Flex, Grid, Heading, SegmentedControl, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import type { ActionCatalogItem } from '../types';
import { ActionCard, type ActionCardCta } from './action-card';

interface SegmentedTab {
  value: string;
  label: string;
}

export interface ActionsCatalogLayoutProps {
  title: string;
  subtitle: string;
  searchPlaceholder?: string;
  searchQuery: string;
  onSearchChange: (value: string) => void;
  tabs: SegmentedTab[];
  activeTab: string;
  onTabChange: (value: string) => void;
  trailingAction?: React.ReactNode;
  items: ActionCatalogItem[];
  resolveCta: (item: ActionCatalogItem) => { cta: ActionCardCta; label: string };
  onCta?: (item: ActionCatalogItem) => void;
  onCardClick?: (item: ActionCatalogItem) => void;
  isLoading?: boolean;
  loadingLabel: string;
  emptyLabel: string;
  /** When set, tab chip counts use these values (e.g. server filterCounts for my-toolsets). */
  tabCountsOverride?: Record<string, number> | null;
  /**
   * Team / browse catalog: filter **configured** by org instances (`hasOrgInstance`).
   * Personal “my”: filter **authenticated** by user credential state (`isUserAuthenticated`).
   */
  tabFilterMode?: 'orgInstances' | 'userAuth';
  /** Merged catalog cards: hide trailing “+” (personal browse). */
  showQuickAddOnMergedCards?: boolean;
  /**
   * Parent already filtered `items` by tab (and optionally applied server-side search).
   * Skips tab + search filtering inside the layout so counts and the grid stay consistent.
   */
  preFilteredCatalog?: boolean;
}

export function ActionsCatalogLayout({
  title,
  subtitle,
  searchPlaceholder = 'Search...',
  searchQuery,
  onSearchChange,
  tabs,
  activeTab,
  onTabChange,
  trailingAction,
  items,
  resolveCta,
  onCta,
  onCardClick,
  isLoading = false,
  loadingLabel,
  emptyLabel,
  tabCountsOverride = null,
  tabFilterMode = 'userAuth',
  showQuickAddOnMergedCards = true,
  preFilteredCatalog = false,
}: ActionsCatalogLayoutProps) {
  const tabFiltered = useMemo(() => {
    if (preFilteredCatalog) return items;
    if (tabFilterMode === 'orgInstances') {
      switch (activeTab) {
        case 'configured':
          return items.filter((c) => c.hasOrgInstance);
        case 'not_configured':
          return items.filter((c) => !c.hasOrgInstance);
        default:
          return items;
      }
    }
    switch (activeTab) {
      case 'authenticated':
        return items.filter((c) => c.isUserAuthenticated);
      case 'not_authenticated':
        return items.filter((c) => !c.isUserAuthenticated);
      default:
        return items;
    }
  }, [items, activeTab, tabFilterMode, preFilteredCatalog]);

  const filtered = useMemo(() => {
    if (preFilteredCatalog) return tabFiltered;
    if (!searchQuery.trim()) return tabFiltered;
    const q = searchQuery.toLowerCase();
    return tabFiltered.filter(
      (c) =>
        c.title.toLowerCase().includes(q) ||
        c.toolsetType.toLowerCase().includes(q) ||
        c.description.toLowerCase().includes(q) ||
        c.category.toLowerCase().includes(q)
    );
  }, [tabFiltered, searchQuery, preFilteredCatalog]);

  const tabCounts = useMemo(() => {
    if (tabCountsOverride) return tabCountsOverride;
    const counts: Record<string, number> = {};
    const searchFiltered = (list: ActionCatalogItem[]) => {
      if (!searchQuery.trim()) return list;
      const q = searchQuery.toLowerCase();
      return list.filter(
        (c) =>
          c.title.toLowerCase().includes(q) ||
          c.toolsetType.toLowerCase().includes(q) ||
          c.description.toLowerCase().includes(q) ||
          c.category.toLowerCase().includes(q)
      );
    };
    const base = searchFiltered(items);
    counts['all'] = base.length;
    counts['configured'] = base.filter((c) => c.hasOrgInstance).length;
    counts['not_configured'] = base.filter((c) => !c.hasOrgInstance).length;
    counts['authenticated'] = base.filter((c) => c.isUserAuthenticated).length;
    counts['not_authenticated'] = base.filter((c) => !c.isUserAuthenticated).length;
    return counts;
  }, [items, searchQuery, tabCountsOverride]);

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
      <Flex justify="between" align="start" gap="2" style={{ width: '100%' }}>
        <Flex direction="column" gap="2" style={{ flex: 1 }}>
          <Heading size="5" weight="medium" style={{ color: 'var(--gray-12)' }}>
            {title}
          </Heading>
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {subtitle}
          </Text>
        </Flex>

        <TextField.Root
          size="2"
          placeholder={searchPlaceholder}
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          style={{ width: 224, flexShrink: 0 }}
        >
          <TextField.Slot>
            <MaterialIcon name="search" size={16} color="var(--gray-9)" />
          </TextField.Slot>
        </TextField.Root>
      </Flex>

      <Flex align="center" justify="between" style={{ width: '100%' }}>
        <SegmentedControl.Root
          value={activeTab}
          onValueChange={(value) => onTabChange(value)}
          size="2"
        >
          {tabs.map((tab) => (
            <SegmentedControl.Item key={tab.value} value={tab.value}>
              {tab.label} ({tabCounts[tab.value] ?? 0})
            </SegmentedControl.Item>
          ))}
        </SegmentedControl.Root>
        {trailingAction}
      </Flex>

      {isLoading ? (
        <Flex
          align="center"
          justify="center"
          style={{ width: '100%', flex: 1 }}
        >
          <LottieLoader variant="loader" size={48} showLabel label={loadingLabel} />
        </Flex>
      ) : filtered.length === 0 ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          gap="2"
          style={{ width: '100%', paddingTop: 80 }}
        >
          <MaterialIcon name="extension" size={48} color="var(--gray-9)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            {emptyLabel}
          </Text>
        </Flex>
      ) : (
        <Grid columns={{ initial: '2', md: '3', lg: '3' }} gap="4" style={{ width: '100%' }}>
          {filtered.map((item) => {
            const { cta, label } = resolveCta(item);
            return (
              <ActionCard
                key={item.key}
                item={item}
                cta={cta}
                ctaLabel={label}
                showQuickAdd={showQuickAddOnMergedCards}
                onCta={onCta}
                onCardClick={onCardClick}
              />
            );
          })}
        </Grid>
      )}
    </Flex>
  );
}

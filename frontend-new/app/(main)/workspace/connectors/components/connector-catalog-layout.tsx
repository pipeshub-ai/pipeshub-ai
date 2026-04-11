'use client';

import React, { useMemo } from 'react';
import { Flex, Grid, Heading, SegmentedControl, Text, TextField } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { Connector } from '../types';
import { ConnectorCard } from './connector-card';

// ========================================
// Types
// ========================================

interface SegmentedTab {
  value: string;
  label: string;
}

interface ConnectorCatalogLayoutProps {
  /** Page heading. */
  title: string;
  /** Subtitle. */
  subtitle: string;
  /** Search placeholder text. */
  searchPlaceholder?: string;
  /** Controlled search value. */
  searchQuery: string;
  /** Search change handler. */
  onSearchChange: (value: string) => void;
  /** Tabs for the segmented control. */
  tabs: SegmentedTab[];
  /** Currently selected tab value. */
  activeTab: string;
  /** Tab change handler. */
  onTabChange: (value: string) => void;
  /** Optional right-side element next to segmented control (e.g. "Your Connectors →" link). */
  trailingAction?: React.ReactNode;
  /** Registry connectors to display as cards. */
  registryConnectors: Connector[];
  /** Active connectors to display as cards. */
  activeConnectors: Connector[];
  /** Called when a connector Setup / Add button is clicked. */
  onSetup?: (connector: Connector) => void;
  /** Called when a connector card body is clicked (navigate to type page). */
  onCardClick?: (connector: Connector) => void;
  /** Whether data is loading. */
  isLoading?: boolean;
}

// ========================================
// Component
// ========================================

export function ConnectorCatalogLayout({
  title,
  subtitle,
  searchPlaceholder = 'Search...',
  searchQuery,
  onSearchChange,
  tabs,
  activeTab,
  onTabChange,
  trailingAction,
  registryConnectors,
  activeConnectors,
  onSetup,
  onCardClick,
  isLoading = false,
}: ConnectorCatalogLayoutProps) {
  // Count active (isActive=true) and inactive (isActive=false) instances per connector type
  const { activeCountByType, inactiveCountByType } = useMemo(() => {
    const active: Record<string, number> = {};
    const inactive: Record<string, number> = {};
    for (const c of activeConnectors) {
      if (c.isActive) {
        active[c.type] = (active[c.type] || 0) + 1;
      } else {
        inactive[c.type] = (inactive[c.type] || 0) + 1;
      }
    }
    return { activeCountByType: active, inactiveCountByType: inactive };
  }, [activeConnectors]);

  // Merge registry + active connectors for display, deduplicating by type.
  // Active connectors take priority (isConfigured=true); registry fills in types with no instances.
  // Always use the registry connector's name/description so cards show the type name,
  // not the instance name of the first configured instance.
  const allConnectors = useMemo(() => {
    const registryByType = new Map(registryConnectors.map((c) => [c.type, c]));
    const seenTypes = new Set<string>();
    const result: Connector[] = [];
    for (const c of activeConnectors) {
      if (!seenTypes.has(c.type)) {
        seenTypes.add(c.type);
        const reg = registryByType.get(c.type);
        result.push(reg ? { ...c, name: reg.name, appDescription: reg.appDescription } : c);
      }
    }
    for (const c of registryConnectors) {
      if (!seenTypes.has(c.type)) {
        seenTypes.add(c.type);
        result.push(c);
      }
    }
    return result;
  }, [activeConnectors, registryConnectors]);

  // Apply tab filter
  const tabFiltered = useMemo(() => {
    switch (activeTab) {
      case 'configured':
        return allConnectors.filter((c) => c.isConfigured);
      case 'not_configured':
        return allConnectors.filter((c) => !c.isConfigured);
      case 'active':
        return allConnectors.filter((c) => c.isActive);
      case 'inactive':
        return allConnectors.filter((c) => !c.isActive);
      default:
        return allConnectors;
    }
  }, [allConnectors, activeTab]);

  // Apply search filter
  const filtered = useMemo(() => {
    if (!searchQuery.trim()) return tabFiltered;
    const q = searchQuery.toLowerCase();
    return tabFiltered.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.type.toLowerCase().includes(q) ||
        c.appDescription.toLowerCase().includes(q) ||
        c.appGroup.toLowerCase().includes(q)
    );
  }, [tabFiltered, searchQuery]);

  // Tab counts
  const tabCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    const searchFiltered = (list: Connector[]) => {
      if (!searchQuery.trim()) return list;
      const q = searchQuery.toLowerCase();
      return list.filter(
        (c) =>
          c.name.toLowerCase().includes(q) ||
          c.type.toLowerCase().includes(q) ||
          c.appDescription.toLowerCase().includes(q) ||
          c.appGroup.toLowerCase().includes(q)
      );
    };
    const base = searchFiltered(allConnectors);
    counts['all'] = base.length;
    counts['configured'] = base.filter((c) => c.isConfigured).length;
    counts['not_configured'] = base.filter((c) => !c.isConfigured).length;
    counts['active'] = base.filter((c) => c.isActive).length;
    counts['inactive'] = base.filter((c) => !c.isActive).length;
    return counts;
  }, [allConnectors, searchQuery]);

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
      {/* ── Header: title + search ── */}
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

      {/* ── Tabs + trailing action ── */}
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

      {/* ── Connector grid ── */}
      {isLoading ? (
        <Flex
          align="center"
          justify="center"
          style={{ width: '100%', paddingTop: 80 }}
        >
          <Text size="2" style={{ color: 'var(--gray-9)' }}>
            Loading connectors…
          </Text>
        </Flex>
      ) : filtered.length === 0 ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          gap="2"
          style={{ width: '100%', paddingTop: 80 }}
        >
          <MaterialIcon name="hub" size={48} color="var(--gray-9)" />
          <Text size="2" style={{ color: 'var(--gray-11)' }}>
            No connectors found
          </Text>
        </Flex>
      ) : (
        <Grid
          columns={{ initial: '2', md: '3', lg: '3' }}
          gap="4"
          style={{ width: '100%' }}
        >
          {filtered.map((connector, idx) => (
            <ConnectorCard
              key={`${connector.type}-${connector._key ?? idx}`}
              connector={connector}
              variant={connector.isConfigured ? 'active' : 'registry'}
              activeInstanceCount={activeCountByType[connector.type] ?? 0}
              inactiveInstanceCount={inactiveCountByType[connector.type] ?? 0}
              onSetup={onSetup}
              onCardClick={onCardClick}
            />
          ))}
        </Grid>
      )}
    </Flex>
  );
}


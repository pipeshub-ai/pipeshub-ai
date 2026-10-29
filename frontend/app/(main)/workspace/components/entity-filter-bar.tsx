'use client';

import React from 'react';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';

export interface FilterChipConfig {
  /** Unique key for the filter */
  key: string;
  /** Display label */
  label: string;
  /** Material icon name */
  icon: string;
}

export interface EntityFilterBarProps {
  /** Array of filter chip configurations */
  filters: FilterChipConfig[];
  /** Render function for each filter — allows parent to render FilterDropdown / DateRangePicker */
  renderFilter?: (filter: FilterChipConfig) => React.ReactNode;
}

/**
 * EntityFilterBar — filter bar with filter icon + configurable filter chips.
 *
 * Matches the Figma design: filter_list icon on the left, followed by chips.
 * Each chip shows an icon + label. The parent can render custom dropdowns per filter
 * via the `renderFilter` prop. If not provided, a plain chip is rendered.
 */
export function EntityFilterBar({ filters, renderFilter }: EntityFilterBarProps) {
  const compact = useIsBelowDesktop();
  return (
    <Flex
      align="center"
      gap="2"
      className={compact ? 'entity-filter-bar-mobile no-scrollbar' : undefined}
      style={{
        // Below desktop: one row that scrolls sideways instead of clipping chips.
        flexWrap: compact ? 'nowrap' : undefined,
        overflowX: compact ? 'auto' : undefined,
        // 12px around the 28px chips
        minHeight: 52,
        flexShrink: 0,
        padding: '0 var(--space-3)',
        borderBottom: '1px solid var(--olive-3)',
      }}
    >
      <MaterialIcon name="filter_list" size={16} color="var(--olive-9)" />

      {filters.map((filter) =>
        renderFilter ? (
          <React.Fragment key={filter.key}>{renderFilter(filter)}</React.Fragment>
        ) : (
          <DefaultFilterChip key={filter.key} filter={filter} />
        )
      )}
    </Flex>
  );
}

/**
 * Default filter chip — static badge with icon + label.
 * Used when no custom renderFilter is provided.
 */
function DefaultFilterChip({ filter }: { filter: FilterChipConfig }) {
  return (
    <Badge
      variant="outline"
      color="gray"
      size="1"
      style={{
        cursor: 'pointer',
        padding: '4px 10px',
        display: 'flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
      }}
    >
      <MaterialIcon name={filter.icon} size={12} color="var(--slate-11)" />
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {filter.label}
      </Text>
    </Badge>
  );
}

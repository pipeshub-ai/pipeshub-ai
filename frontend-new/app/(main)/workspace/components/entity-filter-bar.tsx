'use client';

import React from 'react';
import { Flex, Text, Badge } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

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
  return (
    <Flex
      align="center"
      gap="2"
      style={{
        height: '40px',
        padding: '0 16px',
        borderBottom: '1px solid var(--slate-6)',
        backgroundColor: 'var(--slate-2)',
      }}
    >
      <MaterialIcon name="filter_list" size={16} color="var(--slate-9)" />

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
        gap: '4px',
      }}
    >
      <MaterialIcon name={filter.icon} size={12} color="var(--slate-11)" />
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {filter.label}
      </Text>
    </Badge>
  );
}

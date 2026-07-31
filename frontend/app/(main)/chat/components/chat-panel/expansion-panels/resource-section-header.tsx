'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';

export interface ResourceSectionHeaderProps {
  label: string;
  /** Item count shown next to the label — omitted when undefined. */
  count?: number;
}

/**
 * Small title row for a grouped section (Connectors / Collections / Actions)
 * inside the resources panel. Shared across `CollectionsTab`'s sectioned
 * layout and both resource panels so the three sections look identical.
 */
export function ResourceSectionHeader({ label, count }: ResourceSectionHeaderProps) {
  return (
    <Flex align="center" gap="2" style={{ width: '100%', flexShrink: 0 }}>
      <Text size="1" weight="medium" style={{ color: 'var(--gray-10)', whiteSpace: 'nowrap' }}>
        {label}
      </Text>
      {typeof count === 'number' && (
        <Text size="1" style={{ color: 'var(--gray-8)' }}>
          {count}
        </Text>
      )}
    </Flex>
  );
}

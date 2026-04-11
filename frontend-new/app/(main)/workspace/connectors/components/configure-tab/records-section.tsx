'use client';

import React from 'react';
import { Flex, Text, Button, Badge, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// RecordsSection
// ========================================

export function RecordsSection({
  connectorName,
  selectedRecords,
  availableRecords,
  onSelectRecords,
}: {
  connectorName: string;
  selectedRecords: string[];
  availableRecords: { id: string; name: string }[];
  onSelectRecords: () => void;
}) {
  const hasRecords = selectedRecords.length > 0;

  // Map IDs to names using availableRecords; fall back to the ID if not found
  const recordNames = selectedRecords.map(
    (id) => availableRecords.find((r) => r.id === id)?.name ?? id
  );

  return (
    <Flex
      direction="column"
      gap="3"
      style={{
        padding: 16,
        backgroundColor: 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Flex direction="column" gap="1">
        <Flex align="center" justify="between">
          <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
            Records to index
          </Text>
          {hasRecords && (
            <Flex align="center" gap="2">
              <Badge
                size="1"
                style={{
                  backgroundColor: 'var(--green-a3)',
                  color: 'var(--green-a11)',
                  padding: '2px 6px',
                }}
              >
                {selectedRecords.length} Selected
              </Badge>
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={onSelectRecords}
                style={{ cursor: 'pointer' }}
              >
                <MaterialIcon name="edit" size={14} color="var(--gray-11)" />
              </IconButton>
            </Flex>
          )}
        </Flex>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          Choose which {connectorName} records should be imported
        </Text>
      </Flex>

      {hasRecords ? (
        <Flex direction="column" gap="1">
          {recordNames.map((name, index) => (
            <Text
              key={selectedRecords[index]}
              size="2"
              style={{ color: 'var(--gray-12)', lineHeight: '26px' }}
            >
              {name}
            </Text>
          ))}
        </Flex>
      ) : (
        <Button
          variant="solid"
          size="2"
          onClick={onSelectRecords}
          style={{ width: '100%', cursor: 'pointer' }}
        >
          <MaterialIcon name="add" size={16} color="white" />
          Select
        </Button>
      )}
    </Flex>
  );
}

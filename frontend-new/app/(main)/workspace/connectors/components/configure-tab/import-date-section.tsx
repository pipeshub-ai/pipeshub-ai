'use client';

import React, { useState } from 'react';
import { Flex, Text, Box, Checkbox } from '@radix-ui/themes';

// ========================================
// ImportDateSection
// ========================================

export function ImportDateSection() {
  const [importDate, setImportDate] = useState<string>('');
  const [importAllTime, setImportAllTime] = useState(false);
  const [isFocused, setIsFocused] = useState(false);

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
        <Text size="3" weight="medium" style={{ color: 'var(--gray-12)' }}>
          Import start date
        </Text>
        <Text size="1" style={{ color: 'var(--gray-10)' }}>
          Choose the date from which messages are imported
        </Text>
      </Flex>

      <Box style={{ position: 'relative', width: '100%' }}>
        <input
          type="date"
          value={importAllTime ? '' : importDate}
          disabled={importAllTime}
          onChange={(e) => setImportDate(e.target.value)}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          style={{
            height: 32,
            width: '100%',
            padding: '6px 8px',
            backgroundColor: 'var(--color-surface)',
            border: isFocused
              ? '2px solid var(--accent-8)'
              : '1px solid var(--gray-a5)',
            borderRadius: 'var(--radius-2)',
            fontSize: 14,
            fontFamily: 'var(--default-font-family)',
            color: importAllTime ? 'var(--gray-8)' : 'var(--gray-12)',
            boxSizing: 'border-box',
            outline: 'none',
            opacity: importAllTime ? 0.6 : 1,
          }}
        />
      </Box>

      <Flex align="center" gap="2" style={{ minHeight: 32 }}>
        <Checkbox
          checked={importAllTime}
          onCheckedChange={(checked) => setImportAllTime(Boolean(checked))}
        />
        <Text size="2" style={{ color: 'var(--gray-12)' }}>
          Import All Time Data
        </Text>
      </Flex>
    </Flex>
  );
}

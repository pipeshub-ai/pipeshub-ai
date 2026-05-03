'use client';

import React, { useEffect, useState } from 'react';
import { Flex, Box, Text, Switch, TextField, Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

// ========================================
// Types
// ========================================

interface SendImagesRowProps {
  enabled: boolean;
  maxImages: number;
  onToggle: (enabled: boolean) => void;
  onMaxImagesCommit: (value: number) => void;
  disabled?: boolean;
}

// ========================================
// Component
// ========================================

export function SendImagesRow({
  enabled,
  maxImages,
  onToggle,
  onMaxImagesCommit,
  disabled = false,
}: SendImagesRowProps) {
  const [inputValue, setInputValue] = useState(String(maxImages));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setInputValue(String(maxImages));
    setError(null);
  }, [maxImages]);

  const validate = (text: string): number | null => {
    const trimmed = text.trim();
    if (trimmed === '') return null;
    const parsed = Number(trimmed);
    if (!Number.isInteger(parsed) || parsed < 1 || parsed > 500) return null;
    return parsed;
  };

  const handleCommit = () => {
    const parsed = validate(inputValue);
    if (parsed === null) {
      setError('Enter a whole number between 1 and 500');
      setInputValue(String(maxImages));
      return;
    }
    setError(null);
    if (parsed !== maxImages) {
      onMaxImagesCommit(parsed);
    }
  };

  return (
    <Flex
      direction="column"
      gap="3"
      style={{
        padding: '12px 14px',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-1)',
        background: 'var(--olive-2)',
      }}
    >
      {/* Toggle row */}
      <Flex align="center" gap="3">
        <Flex
          align="center"
          justify="center"
          style={{
            width: 36,
            height: 36,
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-3)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="image" size={18} color="var(--slate-11)" />
        </Flex>

        <Box style={{ flex: 1, minWidth: 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', display: 'block' }}>
            Send images to LLM
          </Text>
          <Text
            size="1"
            style={{
              color: 'var(--slate-10)',
              display: 'block',
              marginTop: 2,
              fontWeight: 300,
            }}
          >
            Control whether images are sent to the LLM during web search
          </Text>
        </Box>

        <Flex align="center" style={{ flexShrink: 0 }}>
          <Switch
            color="jade"
            size="2"
            checked={enabled}
            disabled={disabled}
            onCheckedChange={onToggle}
          />
        </Flex>
      </Flex>

      {/* Max images input — shown when enabled */}
      {enabled && (
        <Flex
          direction="column"
          gap="2"
          style={{
            padding: '10px 12px 12px',
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--slate-2)',
            border: '1px solid var(--slate-4)',
          }}
        >
          <Text size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
            Maximum input images per LLM call
          </Text>
          <TextField.Root
            type="number"
            min={1}
            max={500}
            step={1}
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onBlur={handleCommit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                (e.target as HTMLInputElement).blur();
              }
            }}
            disabled={disabled}
            style={{ maxWidth: 220 }}
          />
          <Text size="1" style={{ color: error ? 'var(--red-10)' : 'var(--slate-10)', fontWeight: 300 }}>
            {error ?? 'Enter a whole number between 1 and 500'}
          </Text>
          <Callout.Root color="amber" size="1" variant="soft">
            <Callout.Icon>
              <MaterialIcon name="warning" size={14} color="var(--amber-11)" />
            </Callout.Icon>
            <Callout.Text>
              Including images may increase cost and add latency to web search queries.
            </Callout.Text>
          </Callout.Root>
        </Flex>
      )}
    </Flex>
  );
}

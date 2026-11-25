'use client';

import React, { useEffect, useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Switch, TextField, Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { MAX_IMAGES_PER_PAGE } from '../types';

export const MIN_IMAGES = 1;
export const MAX_IMAGES = MAX_IMAGES_PER_PAGE;

interface SendImagesRowProps {
  enabled: boolean;
  maxImages: number;
  onToggle: (enabled: boolean) => void;
  onMaxImagesCommit: (value: number) => void;
  disabled?: boolean;
}

/** The whole number the server accepts for `maxImages`, or null. */
export function parseMaxImages(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === '') return null;
  const parsed = Number(trimmed);
  return Number.isInteger(parsed) && parsed >= MIN_IMAGES && parsed <= MAX_IMAGES ? parsed : null;
}

export function SendImagesRow({
  enabled,
  maxImages,
  onToggle,
  onMaxImagesCommit,
  disabled = false,
}: SendImagesRowProps) {
  const { t } = useTranslation();
  const [inputValue, setInputValue] = useState(String(maxImages));
  const [invalid, setInvalid] = useState(false);
  const helpId = useId();
  const switchId = useId();

  useEffect(() => {
    setInputValue(String(maxImages));
    setInvalid(false);
  }, [maxImages]);

  const handleCommit = () => {
    const parsed = parseMaxImages(inputValue);
    if (parsed === null) {
      setInvalid(true);
      setInputValue(String(maxImages));
      return;
    }
    setInvalid(false);
    if (parsed !== maxImages) onMaxImagesCommit(parsed);
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
          <Text
            as="label"
            htmlFor={switchId}
            size="2"
            weight="medium"
            style={{ color: 'var(--slate-12)', display: 'block' }}
          >
            {t('workspace.webSearch.sendImages')}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-11)', display: 'block', marginTop: 2, fontWeight: 300 }}>
            {t('workspace.webSearch.sendImagesDescription')}
          </Text>
        </Box>

        <Flex align="center" style={{ flexShrink: 0, minHeight: 44 }}>
          <Switch
            id={switchId}
            color="jade"
            size="2"
            checked={enabled}
            disabled={disabled}
            onCheckedChange={onToggle}
          />
        </Flex>
      </Flex>

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
          <Text as="label" size="1" weight="medium" style={{ color: 'var(--slate-12)' }}>
            {t('workspace.webSearch.maxImages')}
            <TextField.Root
              type="number"
              inputMode="numeric"
              min={MIN_IMAGES}
              max={MAX_IMAGES}
              step={1}
              value={inputValue}
              aria-invalid={invalid}
              aria-describedby={helpId}
              onChange={(e) => setInputValue(e.target.value)}
              onBlur={handleCommit}
              onKeyDown={(e) => {
                if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
              }}
              disabled={disabled}
              style={{ maxWidth: 220, marginTop: 'var(--space-2)' }}
            />
          </Text>
          <Text
            id={helpId}
            size="1"
            role={invalid ? 'alert' : undefined}
            style={{ color: invalid ? 'var(--red-11)' : 'var(--slate-11)', fontWeight: 300 }}
          >
            {invalid ? t('workspace.webSearch.maxImagesInvalid') : t('workspace.webSearch.maxImagesHelp')}
          </Text>
          <Callout.Root color="amber" size="1" variant="soft">
            <Callout.Icon>
              <MaterialIcon name="warning" size={14} color="var(--amber-11)" />
            </Callout.Icon>
            <Callout.Text>{t('workspace.webSearch.imagesCostWarning')}</Callout.Text>
          </Callout.Root>
        </Flex>
      )}
    </Flex>
  );
}

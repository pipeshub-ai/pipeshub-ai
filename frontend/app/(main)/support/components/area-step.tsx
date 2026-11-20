'use client';

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { RadioGroup } from 'radix-ui';
import { Box, Dialog, Flex, Grid, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SUPPORT_AREA_META } from '../areas';
import { SUPPORT_AREA_KEYS, type SupportAreaKey } from '../types';

export function AreaStep({
  selected,
  suggested,
  suggestionTime,
  onSelect,
}: {
  selected: SupportAreaKey | null;
  suggested: SupportAreaKey | null;
  suggestionTime: string | null;
  onSelect: (area: SupportAreaKey) => void;
}) {
  const { t } = useTranslation();
  const [hovered, setHovered] = useState<SupportAreaKey | null>(null);

  return (
    <Flex direction="column" gap="4" px="5">
      <Flex direction="column" gap="1">
        <Text as="div" size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('support.areaStep.title')}
        </Text>
        <Dialog.Description size="2" style={{ color: 'var(--slate-11)', lineHeight: '20px' }}>
          {t('support.areaStep.subtitle')}
        </Dialog.Description>
      </Flex>
      {suggested && suggestionTime && (
        <Flex
          gap="2"
          align="start"
          style={{
            padding: 'var(--space-2) var(--space-3)',
            borderRadius: 'var(--radius-3)',
            background: 'var(--blue-a3)',
            color: 'var(--blue-11)',
          }}
        >
          <MaterialIcon name="info" size={16} />
          <Text size="1">
            {t('support.areaStep.suggestion', {
              area: t(`support.areas.${suggested}.label`),
              time: suggestionTime,
            })}
          </Text>
        </Flex>
      )}
      <RadioGroup.Root
        value={selected ?? ''}
        onValueChange={(value) => onSelect(value as SupportAreaKey)}
        aria-label={t('support.areaStep.title')}
        aria-required
      >
        {/* In two columns, equal rows keep every card the same height whether its hint takes one line or two. */}
        <Grid
          columns={{ initial: '1', sm: '2' }}
          rows={{ sm: `repeat(${Math.ceil(SUPPORT_AREA_KEYS.length / 2)}, 1fr)` }}
          gap="3"
        >
          {SUPPORT_AREA_KEYS.map((key) => {
            const isSelected = selected === key;
            const isHovered = hovered === key;
            return (
              <RadioGroup.Item
                key={key}
                value={key}
                onMouseEnter={() => setHovered(key)}
                onMouseLeave={() => setHovered(null)}
                style={{
                  position: 'relative',
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 'var(--space-3)',
                  width: '100%',
                  boxSizing: 'border-box',
                  padding: 'var(--space-3)',
                  font: 'inherit',
                  textAlign: 'left',
                  cursor: 'pointer',
                  borderRadius: 'var(--radius-4)',
                  border: `1px solid ${
                    isSelected ? 'var(--accent-8)' : isHovered ? 'var(--gray-a7)' : 'var(--gray-a5)'
                  }`,
                  background: isSelected
                    ? 'var(--accent-a2)'
                    : isHovered
                      ? 'var(--gray-a2)'
                      : 'var(--color-panel-solid)',
                  boxShadow: isSelected ? '0 0 0 1px var(--accent-8)' : 'none',
                  transition: 'border-color 0.15s, background 0.15s, box-shadow 0.15s',
                }}
              >
                <Flex
                  aria-hidden="true"
                  align="center"
                  justify="center"
                  flexShrink="0"
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 'var(--radius-3)',
                    background: 'var(--accent-a3)',
                    color: 'var(--accent-11)',
                  }}
                >
                  <MaterialIcon name={SUPPORT_AREA_META[key].icon} size={20} />
                </Flex>
                <Box style={{ flex: 1, minWidth: 0 }}>
                  <Text as="div" size="2" weight="medium" style={{ color: 'var(--slate-12)', lineHeight: '18px' }}>
                    {t(`support.areas.${key}.label`)}
                  </Text>
                  <Text as="div" size="1" style={{ color: 'var(--slate-10)', lineHeight: '16px', marginTop: 2 }}>
                    {t(`support.areas.${key}.hint`)}
                  </Text>
                </Box>
                {suggested === key && (
                  // Sits on the card's top edge so it never squeezes the label.
                  <Text
                    size="1"
                    weight="medium"
                    style={{
                      position: 'absolute',
                      top: -9,
                      right: 12,
                      padding: '0 6px',
                      lineHeight: '16px',
                      borderRadius: 'var(--radius-2)',
                      border: '1px solid var(--blue-6)',
                      background: 'var(--blue-3)',
                      color: 'var(--blue-11)',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {t('support.areaStep.suggested')}
                  </Text>
                )}
              </RadioGroup.Item>
            );
          })}
        </Grid>
      </RadioGroup.Root>
    </Flex>
  );
}

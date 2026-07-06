'use client';

import { Box, Flex, Text } from '@radix-ui/themes';
import { getIndexingProgressView, type IndexingProgressInput } from '../utils/indexing-progress';

const STEPS = [
  { label: 'Queued', percent: 5 },
  { label: 'Extract', percent: 35 },
  { label: 'Index', percent: 75 },
  { label: 'Done', percent: 100 },
] as const;

export function IndexingProgressIndicator({
  record,
  compact = false,
}: {
  record: IndexingProgressInput;
  compact?: boolean;
}) {
  const view = getIndexingProgressView(record);
  const activeColor = view.isStalled ? 'var(--orange-9)' : 'var(--blue-9)';
  const activeText = view.isStalled ? 'var(--orange-11)' : 'var(--blue-11)';

  return (
    <Flex direction="column" gap="1" style={{ minWidth: compact ? 180 : 240, maxWidth: compact ? 220 : 320 }}>
      <Flex align="center" justify="between" gap="2">
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {view.isStalled ? 'Stalled' : 'In progress'}
        </Text>
        <Text size="1" weight="medium" style={{ color: activeText, whiteSpace: 'nowrap' }}>
          {view.percent}%
        </Text>
      </Flex>
      <Flex align="center" gap="1" style={{ minWidth: 0 }}>
        {STEPS.map((step) => {
          const isReached = view.percent >= step.percent;
          const isCurrent = !view.isStalled && view.percent === step.percent;
          const color = isReached || isCurrent ? activeColor : 'var(--slate-6)';
          const textColor = isReached || isCurrent ? activeText : 'var(--slate-9)';
          return (
            <Flex key={step.label} align="center" gap="1" style={{ minWidth: 0 }}>
              <Box
                style={{
                  width: compact ? 6 : 7,
                  height: compact ? 6 : 7,
                  borderRadius: '9999px',
                  background: color,
                  flexShrink: 0,
                }}
              />
              <Text
                size="1"
                style={{
                  color: textColor,
                  lineHeight: 1,
                  whiteSpace: 'nowrap',
                }}
              >
                {step.label}
              </Text>
            </Flex>
          );
        })}
      </Flex>
      <Box style={{ height: 3, borderRadius: '9999px', background: 'var(--slate-4)', overflow: 'hidden' }}>
        <Box
          style={{
            width: `${view.percent}%`,
            height: '100%',
            borderRadius: '9999px',
            background: activeColor,
            transition: 'width 0.25s ease',
          }}
        />
      </Box>
      {view.detail ? (
        <Text size="1" style={{ color: activeText }}>
          {view.detail}
        </Text>
      ) : null}
    </Flex>
  );
}

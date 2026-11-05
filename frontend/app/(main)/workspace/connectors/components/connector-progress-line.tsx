'use client';

import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import {
  describeConnectorProgress,
  type ConnectorProgress,
  type ProgressTone,
} from '../utils/connector-progress';

const TONE: Record<ProgressTone, { icon: string; color: string }> = {
  ok: { icon: 'check_circle', color: 'var(--jade-11)' },
  busy: { icon: 'sync', color: 'var(--accent-11)' },
  warn: { icon: 'warning', color: 'var(--amber-11)' },
  error: { icon: 'error', color: 'var(--red-11)' },
  off: { icon: 'pause_circle', color: 'var(--gray-10)' },
};

/** A connector's state in one line, with a bar only when there is a real total. */
export function ConnectorProgressLine({ view, source }: { view: ConnectorProgress; source: string }) {
  const { t } = useTranslation();
  const line = describeConnectorProgress(t, view, source);
  const tone = TONE[line.tone];
  return (
    <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
      <Flex align="center" gap="2" style={{ minWidth: 0 }} role="status" aria-live="polite">
        <MaterialIcon name={tone.icon} size={16} color={tone.color} />
        <Text size="2" style={{ color: 'var(--gray-12)', minWidth: 0 }}>
          {line.text}
        </Text>
      </Flex>
      {line.percent != null || line.moving ? (
        <Box
          style={{
            width: '100%',
            height: 4,
            borderRadius: 'var(--radius-full)',
            backgroundColor: 'var(--gray-a3)',
            overflow: 'hidden',
          }}
          role="progressbar"
          aria-valuenow={line.percent ?? undefined}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={line.text}
        >
          <Box
            style={{
              width: line.percent == null ? '100%' : `${line.percent}%`,
              height: '100%',
              borderRadius: 'var(--radius-full)',
              backgroundColor: line.percent == null ? 'var(--accent-a6)' : 'var(--accent-9)',
              transition: 'width 300ms ease',
              animation: line.percent == null ? 'shimmer-pulse 1.6s ease-in-out infinite' : undefined,
            }}
          />
        </Box>
      ) : null}
    </Flex>
  );
}

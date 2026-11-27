'use client';

import React from 'react';
import Link from 'next/link';
import { Flex, Link as ThemeLink, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { ScheduleOrigin } from '../../schedule-origin';

export const SCHEDULES_HREF = '/workspace/schedules';

/** Top of a conversation a schedule started: which schedule, linking to where it is managed. */
export function ScheduleOriginHeader({ origin }: { origin: ScheduleOrigin }) {
  const { t } = useTranslation();
  return (
    <Flex
      align="center"
      gap="1"
      wrap="wrap"
      data-testid="schedule-origin-header"
      style={{ color: 'var(--slate-11)', minWidth: 0 }}
    >
      <MaterialIcon name="schedule" size={14} color="var(--slate-11)" />
      <Text size="2">
        {origin.scheduleName !== null ? t('chat.scheduleOrigin.startedBy') : `${t('chat.scheduleOrigin.startedByUnnamed')} ·`}
      </Text>
      <ThemeLink asChild size="2">
        <Link href={SCHEDULES_HREF} style={{ overflowWrap: 'anywhere' }}>
          {origin.scheduleName ?? t('chat.scheduleOrigin.manage')}
        </Link>
      </ThemeLink>
    </Flex>
  );
}

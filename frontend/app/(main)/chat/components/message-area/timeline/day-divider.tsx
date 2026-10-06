'use client';

import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/** "Today", "Yesterday", or the date in the viewer's language. */
export function dayLabel(iso: string, now: Date, language: string, today: string, yesterday: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(date)) / 86_400_000);
  if (days === 0) return today;
  if (days === 1) return yesterday;
  const sameYear = date.getFullYear() === now.getFullYear();
  return new Intl.DateTimeFormat(language, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    ...(sameYear ? {} : { year: 'numeric' }),
  }).format(date);
}

export function DayDivider({ iso }: { iso: string }) {
  const { t, i18n } = useTranslation();
  const label = dayLabel(iso, new Date(), i18n.language, t('chat.collab.timeline.today'), t('chat.collab.timeline.yesterday'));
  if (!label) return null;
  return (
    <Flex align="center" gap="3" role="separator" aria-label={label} data-testid="day-divider" style={{ margin: 'var(--space-4) 0 var(--space-2)' }}>
      <span style={{ flex: 1, height: 1, background: 'var(--slate-a5)' }} />
      <Text size="1" weight="medium" style={{ color: 'var(--slate-11)', whiteSpace: 'nowrap' }}>
        {label}
      </Text>
      <span style={{ flex: 1, height: 1, background: 'var(--slate-a5)' }} />
    </Flex>
  );
}

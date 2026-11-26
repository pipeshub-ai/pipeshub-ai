'use client';

import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { useJobAskFloat } from './job-asks';

/**
 * Where a job's ask was asked, while the card over the message box holds it (design Activity row: "Answer it in the
 * card over the chat"): one quiet line, so the transcript says something waits here without a second set of
 * buttons. "Show it" turns the floating card to this ask and focuses it. The stamp replaces it once answered.
 */
export function FloatedAskPointer({ askKey }: { askKey: string }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  return (
    <Flex align="center" gap="2" wrap="wrap" data-testid="job-ask-pointer" data-ask-key={askKey} style={{ marginTop: 'var(--space-1)' }}>
      <MaterialIcon name="pan_tool" size={14} color="var(--amber-11)" />
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {t('jobAsk.pointer')}
      </Text>
      <Button
        size="1"
        variant="ghost"
        color="gray"
        onClick={() => useJobAskFloat.getState().show(askKey)}
        style={isMobile ? { minHeight: 44, minWidth: 44 } : undefined}
      >
        {t('jobAsk.showIt')}
      </Button>
    </Flex>
  );
}

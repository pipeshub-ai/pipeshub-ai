'use client';

import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface AnswerFailedProps {
  message: string;
  /** Absent when the viewer may not retry (someone else's question). */
  onRetry?: () => void;
}

/** An answer that never arrived: the server's reason, set apart from a real answer. */
export function AnswerFailed({ message, onRetry }: AnswerFailedProps) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      gap="2"
      role="alert"
      data-testid="answer-failed"
      style={{
        border: '1px solid var(--red-a6)',
        background: 'var(--red-a2)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-3)',
      }}
    >
      <Flex align="center" gap="2">
        <MaterialIcon name="error" size={16} color="var(--red-11)" />
        <Text size="2" weight="medium" style={{ color: 'var(--red-11)' }}>
          {t('chat.collab.timeline.answerFailedTitle')}
        </Text>
      </Flex>
      <Text size="2" style={{ color: 'var(--slate-12)', overflowWrap: 'anywhere' }}>
        {message}
      </Text>
      {onRetry ? (
        <Flex>
          <Button size="1" variant="soft" color="red" data-testid="answer-failed-retry" onClick={onRetry}>
            <MaterialIcon name="refresh" size={14} color="currentColor" />
            {t('chat.collab.timeline.answerFailedRetry')}
          </Button>
        </Flex>
      ) : null}
    </Flex>
  );
}

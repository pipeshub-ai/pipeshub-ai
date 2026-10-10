'use client';

import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

interface AnswerFailedProps {
  message: string;
  /** Absent when the viewer may not retry (someone else's question). */
  onRetry?: () => void;
}

/** An answer that never arrived: the server's reason, drawn flat because it sits inside the reply's block. */
export function AnswerFailed({ message, onRetry }: AnswerFailedProps) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      gap="2"
      role="alert"
      data-testid="answer-failed"
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

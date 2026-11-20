'use client';

import { useTranslation } from 'react-i18next';
import { RunErrorCard } from './run-error-card';

interface AnswerFailedProps {
  message: string;
  /** Absent when retry is not offered. */
  onRetry?: () => void;
}

/** An answer that never arrived: the server's reason, in the run-error card's error tone. */
export function AnswerFailed({ message, onRetry }: AnswerFailedProps) {
  const { t } = useTranslation();
  return (
    <RunErrorCard
      tone="error"
      role="alert"
      icon="error"
      title={t('chat.answerFailedTitle')}
      body={message}
      retryLabel={t('chat.answerFailedRetry')}
      onRetry={onRetry}
      testId="answer-failed"
      retryTestId="answer-failed-retry"
    />
  );
}

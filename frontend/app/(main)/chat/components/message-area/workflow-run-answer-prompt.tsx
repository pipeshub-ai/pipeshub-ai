'use client';

import React, { useCallback, useState } from 'react';
import { Button, Callout, Flex, Text, TextField } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { WorkflowsApi } from '@/app/(main)/workflows/api';

export interface WorkflowRunAnswerPromptProps {
  workflowId: string;
  runId: string;
  question?: string | null;
  suspensionKind?: string | null;
  onAnswered: (status: string) => void;
}

/**
 * The answer affordance for a run parked on `ctx.request_approval` or an
 * agent HIL question. A suspended run used to appear in chat as a finished
 * message with no indication that it was blocked on the reader, so the only
 * way to unblock it was to find the run in the workflows dashboard.
 *
 * A run waiting on an external event is shown but not answerable — there is
 * nothing a person can click that would deliver the event.
 */
export function WorkflowRunAnswerPrompt({
  workflowId,
  runId,
  question,
  suspensionKind,
  onAnswered,
}: WorkflowRunAnswerPromptProps) {
  const { t } = useTranslation();
  const [answer, setAnswer] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    async (value: string) => {
      if (!value.trim() || submitting) return;
      setSubmitting(true);
      setError(null);
      try {
        const run = await WorkflowsApi.answerRun(workflowId, runId, value);
        onAnswered(run.status ?? 'pending');
      } catch (err) {
        // The run stays parked on any failure, so re-enabling the controls is
        // what lets the user retry rather than losing the only way to unblock it.
        setError(
          err instanceof Error
            ? err.message
            : t('workflowRun.answerFailed', 'Could not submit your answer.')
        );
        setSubmitting(false);
      }
    },
    [workflowId, runId, submitting, onAnswered, t]
  );

  const isApproval = suspensionKind === 'approval';
  const isWaitingForEvent = suspensionKind === 'wait_for_event';

  return (
    <Flex direction="column" gap="2" mt="2">
      {question && (
        <Text size="2" style={{ color: 'var(--slate-12)', whiteSpace: 'pre-wrap' }}>
          {question}
        </Text>
      )}

      {isWaitingForEvent ? (
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {t('workflowRun.waitingForEvent', 'This run resumes when the event arrives.')}
        </Text>
      ) : isApproval ? (
        <Flex gap="2" align="center">
          <Button size="1" color="green" disabled={submitting} onClick={() => submit('yes')}>
            {t('workflowRun.approve', 'Approve')}
          </Button>
          <Button
            size="1"
            variant="soft"
            color="gray"
            disabled={submitting}
            onClick={() => submit('no')}
          >
            {t('workflowRun.reject', 'Reject')}
          </Button>
        </Flex>
      ) : (
        <Flex gap="2" align="center" asChild>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void submit(answer);
            }}
          >
            <TextField.Root
              size="1"
              value={answer}
              disabled={submitting}
              placeholder={t('workflowRun.answerPlaceholder', 'Type your answer')}
              onChange={(e) => setAnswer(e.target.value)}
              style={{ flex: 1 }}
            />
            <Button size="1" type="submit" disabled={submitting || !answer.trim()}>
              {t('workflowRun.submitAnswer', 'Send')}
            </Button>
          </form>
        </Flex>
      )}

      {error && (
        <Callout.Root color="red" size="1" variant="surface">
          <Callout.Icon>
            <MaterialIcon name="error" size={14} />
          </Callout.Icon>
          <Callout.Text>{error}</Callout.Text>
        </Callout.Root>
      )}
    </Flex>
  );
}

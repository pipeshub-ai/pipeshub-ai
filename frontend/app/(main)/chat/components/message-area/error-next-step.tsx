'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useCommandStore } from '@/lib/store/command-store';
import { oneClickStep, type OneClickStep } from '../../harness-errors';
import { streamMessageForSlot } from '../../streaming';
import { buildStreamChatRequestForSlot } from '../../runtime';
import { useChatStore } from '../../store';
import type { ChatStreamErrorInfo } from '../../stream-error';

const LABELS: Record<OneClickStep, { key: string; icon: string }> = {
  retry: { key: 'chatStream.nextStep.retry', icon: 'refresh' },
  wait_then_retry: { key: 'chatStream.nextStep.retry', icon: 'refresh' },
  reload: { key: 'chatStream.nextStep.reload', icon: 'refresh' },
  new_conversation: { key: 'chatStream.nextStep.newChat', icon: 'add_comment' },
};

/** Sends the stopped question again in this conversation, as the MCP card's "Try again" does. */
export function sendAgain(question: string): boolean {
  const slotId = useChatStore.getState().activeSlotId;
  if (!slotId) return false;
  const request = buildStreamChatRequestForSlot(slotId, question);
  if (!request) return false;
  void streamMessageForSlot(slotId, question, request);
  return true;
}

/**
 * The one-click next step of a typed error the reply shows (UX-06, C10.02): try again, start a new chat or reload,
 * as the contract's `nextAction` names it. A step that is not one click (ask an admin, change the request) is said
 * in the error's own words, so nothing is shown for it.
 */
export function ErrorNextStep({ error, question }: { error: ChatStreamErrorInfo | undefined; question: string }) {
  const { t } = useTranslation();
  const [used, setUsed] = useState(false);
  if (!error?.source) return null;
  const step = oneClickStep(error.source, error.code);
  if (step === null) return null;
  const act = () => {
    if (step === 'new_conversation') {
      useCommandStore.getState().dispatch('newChat');
      return;
    }
    if (step === 'reload') {
      window.location.reload();
      return;
    }
    if (sendAgain(question)) setUsed(true);
  };
  return (
    <Flex align="center" gap="2" wrap="wrap" mt="2" data-testid="error-next-step" data-step={step}>
      <Button size="2" variant="soft" onClick={act} disabled={used} style={{ minHeight: 44 }}>
        <MaterialIcon name={LABELS[step].icon} size={16} />
        {t(LABELS[step].key)}
      </Button>
    </Flex>
  );
}

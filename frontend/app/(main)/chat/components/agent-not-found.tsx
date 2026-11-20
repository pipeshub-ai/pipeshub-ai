'use client';

import React from 'react';
import { Button, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { EmptyState } from '@/app/components/ui/empty-state';

/**
 * What the chat page shows in place of the agent space when the agent in the link does not exist or
 * is not the person's to open. Both look the same from here, and the server says no more than that.
 */
export function AgentNotFound({ onBack }: { onBack: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex direction="column" style={{ height: '100%', width: '100%' }} data-testid="agent-not-found">
      <EmptyState
        title={t('chat.agentNotFound.title')}
        description={t('chat.agentNotFound.description')}
        action={
          <Button variant="outline" color="gray" onClick={onBack}>
            {t('chat.agentNotFound.back')}
          </Button>
        }
      />
    </Flex>
  );
}

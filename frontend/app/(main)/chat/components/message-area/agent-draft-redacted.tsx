'use client';

import React from 'react';
import { Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

export const draftBoxStyle: React.CSSProperties = {
  background: 'var(--slate-2)',
  border: '1px solid var(--slate-a6)',
  borderRadius: 'var(--radius-3)',
  padding: 'var(--space-3)',
  marginBottom: 'var(--space-3)',
  maxWidth: '100%',
};

/** What everyone but the requester sees: that a draft exists, never what is in it. */
export function AgentDraftRedacted({ authorName }: { authorName?: string }) {
  const { t } = useTranslation();
  return (
    <Box data-testid="agent-draft-redacted" style={draftBoxStyle}>
      <Text size="2" style={{ color: 'var(--slate-11)' }}>
        {t('chat.agentDraft.draftedBy', { name: authorName || t('chat.agentDraft.someone') })}
      </Text>
    </Box>
  );
}

'use client';

import React from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Text } from '@radix-ui/themes';

/** Prompts that ask the assistant, so picking one sends it. */
const SENDABLE = ['emptyExample1', 'emptyExample2'] as const;

/**
 * Example prompts for a shared chat that has no messages yet. The note example names a placeholder teammate, so
 * sending it as is would reach the AI with no mention in it: its button types "@" in the composer instead.
 */
export function SharedChatEmptyState({ onPick, onStartNote }: { onPick: (text: string) => void; onStartNote: () => void }) {
  const { t } = useTranslation();
  return (
    <Flex
      direction="column"
      align="center"
      justify="center"
      gap="3"
      data-testid="shared-chat-empty-state"
      style={{ flex: 1, width: '100%', padding: 'var(--space-5)' }}
    >
      <Text size="3" weight="medium" style={{ color: 'var(--slate-12)' }}>
        {t('chat.mentions.tips.emptyTitle')}
      </Text>
      <Flex direction="column" gap="2" align="stretch" style={{ maxWidth: 480, width: '100%' }}>
        {SENDABLE.map((key) => {
          const text = t(`chat.mentions.tips.${key}`);
          return (
            <Button
              key={key}
              variant="soft"
              color="gray"
              onClick={() => onPick(text)}
              // Long prompts wrap on a phone instead of spilling out of a fixed-height button.
              style={{ justifyContent: 'flex-start', textAlign: 'start', height: 'auto', minHeight: 'var(--space-6)', whiteSpace: 'normal', paddingBlock: 'var(--space-2)' }}
            >
              {text}
            </Button>
          );
        })}
        <Button
          variant="soft"
          color="gray"
          onClick={onStartNote}
          data-testid="shared-chat-empty-mention"
          style={{ justifyContent: 'flex-start', textAlign: 'start', height: 'auto', minHeight: 'var(--space-6)', whiteSpace: 'normal', paddingBlock: 'var(--space-2)' }}
        >
          {t('chat.mentions.tips.emptyMentionAction')}
        </Button>
        <Text size="1" data-testid="shared-chat-empty-note-example" style={{ color: 'var(--slate-11)', padding: '0 var(--space-3)' }}>
          {t('chat.mentions.tips.emptyExample3')}
        </Text>
      </Flex>
      <Text size="1" style={{ color: 'var(--slate-11)', textAlign: 'center' }}>
        {t('chat.mentions.help.hint')}
      </Text>
    </Flex>
  );
}

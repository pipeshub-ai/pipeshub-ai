'use client';

import React, { useEffect, useRef } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { ResolverCandidate } from './typed-mention-resolver';

export interface MentionChooserProps {
  /** What the user typed after the `@`. */
  typed: string;
  candidates: ResolverCandidate[];
  onPick(candidate: ResolverCandidate): void;
  onCancel(): void;
}

/** Shown on send when a typed `@name` could mean several people or teams. Nothing is sent until one is picked. */
export function MentionChooser({ typed, candidates, onPick, onCancel }: MentionChooserProps) {
  const { t } = useTranslation();
  const first = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    first.current?.focus();
  }, []);

  return (
    <Flex
      direction="column"
      gap="2"
      role="dialog"
      aria-label={t('chat.mentions.resolve.title', { typed, defaultValue: 'Who is @{{typed}}?' })}
      data-testid="mention-chooser"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onCancel();
        }
      }}
      style={{
        border: '1px solid var(--slate-a6)',
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-2)',
        background: 'var(--color-panel-solid)',
      }}
    >
      <Text size="2" weight="medium">
        {t('chat.mentions.resolve.title', { typed, defaultValue: 'Who is @{{typed}}?' })}
      </Text>
      <Text size="1" color="gray">
        {t('chat.mentions.resolve.hint', { defaultValue: 'Pick one to send. Nothing has been sent yet.' })}
      </Text>
      <Flex direction="column" gap="1" role="list">
        {candidates.map((candidate, i) => (
          <Button
            key={`${candidate.ref.type}:${candidate.ref.id}`}
            ref={i === 0 ? first : undefined}
            type="button"
            variant="soft"
            color="gray"
            size="1"
            data-testid="mention-chooser-option"
            onClick={() => onPick(candidate)}
            style={{ justifyContent: 'flex-start' }}
          >
            {candidate.label}
            <Text size="1" color="gray">
              {'· '}
              {candidate.ref.type === 'team'
                ? t('chat.mentions.resolve.team', { defaultValue: 'team' })
                : t('chat.mentions.resolve.person', { defaultValue: 'person' })}
            </Text>
          </Button>
        ))}
      </Flex>
      <Flex justify="end">
        <Button type="button" variant="ghost" color="gray" size="1" onClick={onCancel}>
          {t('chat.mentions.resolve.cancel', { defaultValue: 'Keep editing' })}
        </Button>
      </Flex>
    </Flex>
  );
}

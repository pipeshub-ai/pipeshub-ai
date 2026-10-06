'use client';

import React from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { CollaboratorAccessLevel } from '../../collaboration-types';

export interface NonParticipant {
  userId: string;
  /** Empty when no name is known for the id. */
  name: string;
}

export interface NonParticipantPromptProps {
  people: NonParticipant[];
  /** The owner, or an editor the owner lets invite: they get the offer; anyone else is told to ask. */
  canInvite: boolean;
  busy?: boolean;
  onAdd(person: NonParticipant, level: CollaboratorAccessLevel): void;
  onDismiss(): void;
}

/** After a message mentions someone outside the chat: they were not notified; the owner may add them (never automatic). */
export function NonParticipantPrompt({ people, canInvite, busy, onAdd, onDismiss }: NonParticipantPromptProps) {
  const { t } = useTranslation();
  if (people.length === 0) return null;
  return (
    <Flex direction="column" gap="2" role="status" data-testid="non-participant-prompt" style={{ padding: 'var(--space-2) var(--space-3)' }}>
      {people.map((person) => {
        const name = person.name || t('chat.mentions.unknownUser', { defaultValue: 'Unknown user' });
        return (
          <Flex key={person.userId} align="center" gap="2" wrap="wrap" data-testid="non-participant-row">
            <Text size="2">
              {canInvite
                ? t('chat.mentions.resolve.addPrompt', { name, defaultValue: 'Add {{name}} to this chat?' })
                : t('chat.mentions.resolve.askOwner', {
                    name,
                    defaultValue: "{{name}} isn't in this chat. Ask the owner to add them.",
                  })}
            </Text>
            {canInvite ? (
              <>
                <Button size="1" variant="soft" disabled={busy} onClick={() => onAdd(person, 'read')}>
                  {t('chat.mentions.resolve.addView', { defaultValue: 'Can view' })}
                </Button>
                <Button size="1" variant="soft" disabled={busy} onClick={() => onAdd(person, 'write')}>
                  {t('chat.mentions.resolve.addContinue', { defaultValue: 'Can continue' })}
                </Button>
              </>
            ) : null}
          </Flex>
        );
      })}
      <Flex>
        <Button size="1" variant="ghost" color="gray" onClick={onDismiss}>
          {t('chat.mentions.resolve.dismiss', { defaultValue: 'Not now' })}
        </Button>
      </Flex>
    </Flex>
  );
}

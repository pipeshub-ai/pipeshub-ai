'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { placesText, type NotFound } from '../../subject-not-found';

/**
 * ADR-RUN-03 D4 (ANSWER-CONTRACT-FU-2): above an answer whose subject was not found in the person's knowledge, the
 * plain line saying so, in the reader's language: what the answer draws on instead, and, when the run could not ask
 * the person (a schedule's run), the closest records it found, the question it could not put.
 */
export function NotFoundNotice({ fact }: { fact: NotFound }) {
  const { t, i18n } = useTranslation();
  // Never "in your knowledge" alone when connected sources were searched too: every place is named.
  const title =
    fact.places.length > 0
      ? t('chat.notFound.titleIn', {
          subject: fact.subject,
          places: placesText(fact.places, t('chat.notFound.knowledge'), i18n.language),
        })
      : t('chat.notFound.title', { subject: fact.subject });
  const detail =
    fact.hits === 0 ? t('chat.notFound.nothing') : t('chat.notFound.related', { count: fact.hits });
  return (
    <Flex
      role="note"
      data-testid="not-found-notice"
      direction="column"
      gap="1"
      style={{
        marginBottom: 'var(--space-3)',
        padding: 'var(--space-2) var(--space-3)',
        borderRadius: 'var(--radius-3)',
        border: '1px solid var(--amber-a5)',
        backgroundColor: 'var(--amber-a2)',
        alignSelf: 'flex-start',
        maxWidth: '100%',
      }}
    >
      <Flex align="start" gap="1">
        <MaterialIcon name="search_off" size={14} color="var(--amber-11)" style={{ marginTop: 2, flexShrink: 0 }} />
        <Text size="2" weight="medium" style={{ color: 'var(--amber-12)' }}>
          {title}
        </Text>
      </Flex>
      <Text size="1" style={{ color: 'var(--slate-11)' }}>
        {detail}
      </Text>
      {fact.near.length > 0 ? (
        <Text size="1" data-testid="not-found-near" style={{ color: 'var(--slate-11)' }}>
          {t('chat.notFound.near', { names: fact.near.join(', ') })}
        </Text>
      ) : null}
    </Flex>
  );
}

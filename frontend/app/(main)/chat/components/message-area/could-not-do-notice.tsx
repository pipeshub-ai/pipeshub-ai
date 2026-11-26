'use client';

import React from 'react';
import { Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { COULD_NOT_DO_KEYS, isAttachmentItem, type CouldNotDo } from '../../could-not-do';

/**
 * ADR-RUN-03 D5 (R32REPO-FU-1): above an answer, what the run tried and could not do, one plain line per thing, in
 * the reader's language: "I couldn't read github.com/onyx-dot-app/onyx: the clone was declined." From the journal
 * only, so the person is told even when the answer's writer left it out.
 */
export function CouldNotDoNotice({ fact }: { fact: CouldNotDo }) {
  const { t } = useTranslation();
  return (
    <Flex
      role="note"
      data-testid="could-not-do-notice"
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
      {fact.items.map((item, i) => (
        <Flex key={`${item.kind}:${item.repository ?? item.file ?? i}`} align="start" gap="1">
          <MaterialIcon name="block" size={14} color="var(--amber-11)" style={{ marginTop: 2, flexShrink: 0 }} />
          <Text size="2" weight="medium" style={{ color: 'var(--amber-12)' }}>
            {isAttachmentItem(item)
              ? t(COULD_NOT_DO_KEYS[item.kind], {
                  count: item.count,
                  file: item.file
                    ? t('chat.couldNotDo.namedFile', { name: item.file })
                    : t('chat.couldNotDo.unnamedFile', { count: item.count }),
                })
              : t(COULD_NOT_DO_KEYS[item.kind], {
                  what: item.repository ?? t('chat.couldNotDo.repositories', { count: item.count }),
                })}
          </Text>
        </Flex>
      ))}
    </Flex>
  );
}

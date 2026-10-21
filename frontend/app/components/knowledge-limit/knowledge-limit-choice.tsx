'use client';

import React from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { CollectionsTab } from '@/chat/components/chat-panel/expansion-panels/connectors-collections';
import {
  EMPTY_SELECTION,
  MAX_KNOWLEDGE_LIMIT_IDS,
  limitFromSelection,
  limitSize,
  type KnowledgeLimit,
  type KnowledgeLimitValue,
} from './knowledge-limit';

export interface KnowledgeLimitChoiceProps {
  /** Shown in the question: "What can {appName} see?" */
  appName: string;
  value: KnowledgeLimitValue;
  onChange: (next: KnowledgeLimitValue) => void;
  /** Keeps the radio group's name unique when two choices are on one page. */
  idPrefix: string;
}

/**
 * Lets a user decide what a connected app may see: everything they can, or
 * only the sources, spaces, folders and files they pick.
 */
export function KnowledgeLimitChoice({ appName, value, onChange, idPrefix }: KnowledgeLimitChoiceProps) {
  const { t } = useTranslation();
  const size = value === null ? 0 : limitSize(limitFromSelection(value));
  const options = [
    { key: 'everything', checked: value === null, select: () => onChange(null), label: t('knowledgeLimit.everything'), hint: t('knowledgeLimit.everythingHint') },
    { key: 'only', checked: value !== null, select: () => value === null && onChange(EMPTY_SELECTION), label: t('knowledgeLimit.only'), hint: t('knowledgeLimit.onlyHint') },
  ];

  return (
    <Flex direction="column" gap="3">
      <Flex direction="column" gap="1">
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('knowledgeLimit.title', { appName })}
        </Text>
        <Text size="1" style={{ color: 'var(--slate-11)' }}>
          {t('knowledgeLimit.help')}
        </Text>
      </Flex>

      {options.map((option) => (
        <Flex key={option.key} align="start" gap="2">
          <input
            type="radio"
            name={`${idPrefix}-knowledge-limit`}
            id={`${idPrefix}-knowledge-limit-${option.key}`}
            checked={option.checked}
            onChange={option.select}
            style={{ marginTop: 3 }}
          />
          <label htmlFor={`${idPrefix}-knowledge-limit-${option.key}`}>
            <Flex direction="column">
              <Text size="2" style={{ color: 'var(--slate-12)' }}>{option.label}</Text>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>{option.hint}</Text>
            </Flex>
          </label>
        </Flex>
      ))}

      {value !== null && (
        <Flex direction="column" gap="2">
          <Box
            style={{
              height: 320,
              display: 'flex',
              border: '1px solid var(--olive-4)',
              borderRadius: 'var(--radius-2)',
              padding: 'var(--space-2)',
            }}
          >
            <CollectionsTab selection={value} onSelectionChange={onChange} />
          </Box>
          <Text
            size="1"
            style={{ color: size === 0 || size > MAX_KNOWLEDGE_LIMIT_IDS ? 'var(--amber-11)' : 'var(--slate-11)' }}
          >
            {size === 0
              ? t('knowledgeLimit.pickOne')
              : size > MAX_KNOWLEDGE_LIMIT_IDS
                ? t('knowledgeLimit.tooMany', { max: MAX_KNOWLEDGE_LIMIT_IDS })
                : t('knowledgeLimit.selectedCount', { count: size })}
          </Text>
        </Flex>
      )}
    </Flex>
  );
}

/** "Can see: …" for a connected app in a list. */
export function KnowledgeLimitSummary({ limit }: { limit?: KnowledgeLimit | null }) {
  const { t } = useTranslation();
  return (
    <Text size="1" style={{ color: 'var(--slate-11)' }}>
      {limit ? t('knowledgeLimit.canSeeSome', { count: limitSize(limit) }) : t('knowledgeLimit.canSeeEverything')}
    </Text>
  );
}

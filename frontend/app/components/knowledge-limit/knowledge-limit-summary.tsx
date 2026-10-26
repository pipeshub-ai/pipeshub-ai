'use client';

import React from 'react';
import { Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { limitSize, type KnowledgeLimit } from './knowledge-limit';

/** "Can see: …" for a connected app in a list. */
export function KnowledgeLimitSummary({ limit }: { limit?: KnowledgeLimit | null }) {
  const { t } = useTranslation();
  return (
    <Text size="1" style={{ color: 'var(--slate-11)' }}>
      {limit ? t('knowledgeLimit.canSeeSome', { count: limitSize(limit) }) : t('knowledgeLimit.canSeeEverything')}
    </Text>
  );
}

/** On the consent screens: what the app's creator let it see, or nothing when it may see everything. */
export function KnowledgeLimitNotice({ appName, limit }: { appName: string; limit?: KnowledgeLimit | null }) {
  const { t } = useTranslation();
  if (!limit) return null;
  return (
    <Text as="p" size="2" color="gray">
      {t('knowledgeLimit.appLimited', { appName, count: limitSize(limit) })}
    </Text>
  );
}

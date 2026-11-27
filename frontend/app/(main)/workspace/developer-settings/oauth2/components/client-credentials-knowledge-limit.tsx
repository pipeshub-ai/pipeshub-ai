'use client';

import React from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { EnterpriseFeatureNotice } from '@/config';
import {
  knowledgeLimitForApi,
  limitSize,
  sameKnowledgeLimit,
  type KnowledgeLimit,
  type KnowledgeLimitValue,
} from '@/app/components/knowledge-limit';

export interface ClientCredentialsKnowledgeLimitFieldProps {
  appName: string;
  value: KnowledgeLimitValue;
  onChange: (next: KnowledgeLimitValue) => void;
  idPrefix: string;
}

/** The app as last saved, for judging the limit on screen. */
export interface SavedClientCredentialsApp {
  limit?: KnowledgeLimit | null;
  hadClientCredentials: boolean;
}

/**
 * What an app that uses client_credentials may see: no limit can be set here.
 * A limit it already has can only be removed.
 */
export function ClientCredentialsKnowledgeLimitField({ value, onChange }: ClientCredentialsKnowledgeLimitFieldProps) {
  const { t } = useTranslation();
  const kept = knowledgeLimitForApi(value);
  return (
    <Flex direction="column" gap="3">
      <EnterpriseFeatureNotice
        title={t('knowledgeLimit.app.clientCredentials.title')}
        description={t('knowledgeLimit.app.clientCredentials.description')}
      />
      {kept && (
        <Flex direction="column" align="start" gap="2" data-testid="kept-knowledge-limit">
          <Text size="1" style={{ color: 'var(--slate-11)' }}>
            {t('knowledgeLimit.app.clientCredentials.kept', { count: limitSize(kept) })}
          </Text>
          <Button type="button" size="1" variant="soft" color="gray" onClick={() => onChange(null)}>
            {t('knowledgeLimit.app.clientCredentials.remove')}
          </Button>
        </Flex>
      )}
    </Flex>
  );
}

export const clientCredentialsKnowledgeLimit = {
  /** The limit a new client_credentials app is created with: none. */
  onCreate(_value: KnowledgeLimitValue): KnowledgeLimitValue {
    return null;
  },
  /** Why the limit on screen cannot be saved (a translation key), or null when it can. */
  problem(value: KnowledgeLimitValue, saved: SavedClientCredentialsApp): string | null {
    if (value === null) return null;
    return saved.hadClientCredentials && sameKnowledgeLimit(knowledgeLimitForApi(value), saved.limit)
      ? null
      : 'knowledgeLimit.app.clientCredentials.removeToSave';
  },
};

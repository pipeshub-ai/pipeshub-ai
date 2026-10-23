'use client';

import React, { useState } from 'react';
import { Box, Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { ChatApi } from '../../api';
import type { RequestAccessInfo } from '@/chat/types';
import type { CitationMaps } from './response-tabs/citations/types';

/** The server replaces text built from unreadable sources with this token. */
export const REDACTED_TOKEN = '{{redacted}}';

export function RedactedInline() {
  const { t } = useTranslation();
  return (
    <Text
      size="1"
      data-testid="redacted-inline"
      title={t('chat.redaction.inlineHidden')}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 'var(--space-1)',
        padding: '0 var(--space-2)',
        borderRadius: 'var(--radius-2)',
        background: 'var(--olive-3)',
        color: 'var(--olive-11)',
      }}
    >
      <MaterialIcon name="lock" size={12} color="var(--olive-11)" />
      {t('chat.redaction.inlineHidden')}
    </Text>
  );
}

type RequestState = 'idle' | 'sending' | 'sent' | 'already' | 'failed';

function RedactedSourceRow({ source }: { source: RequestAccessInfo }) {
  const { t } = useTranslation();
  const [state, setState] = useState<RequestState>('idle');
  // Sent back only once the request is made: a connector record's page, where the viewer finishes.
  const [sourceUrl, setSourceUrl] = useState<string | undefined>();
  const connector = source.kind === 'connector';

  const request = async () => {
    setState('sending');
    try {
      const out = await ChatApi.requestAccess(source.recordId);
      setSourceUrl(out.sourceUrl);
      setState(out.alreadyAccessible ? 'already' : 'sent');
    } catch {
      setState('failed');
    }
  };

  return (
    <Flex
      align="center"
      justify="between"
      gap="3"
      wrap="wrap"
      data-testid="redacted-source"
      style={{ padding: 'var(--space-2) 0' }}
    >
      <Flex align="center" gap="2">
        <MaterialIcon name="lock" size={16} color="var(--olive-11)" />
        <Text size="2" color="gray">
          {source.collection ? t('chat.redaction.collectionLabel') : t('chat.redaction.sourceLabel')}
        </Text>
      </Flex>
      <Flex align="center" gap="2" wrap="wrap">
        <Button
          size="2"
          variant="soft"
          disabled={state === 'sending' || state === 'sent'}
          onClick={() => void request()}
          style={{ minHeight: 44 }}
        >
          {state === 'sent' ? t('chat.redaction.requested') : t('chat.redaction.requestAccess')}
        </Button>
        {connector && sourceUrl && (
          <Button asChild size="2" variant="soft" style={{ minHeight: 44 }}>
            <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
              {t('chat.redaction.openInSource')}
            </a>
          </Button>
        )}
        {state === 'failed' && (
          <Text size="1" color="red" role="alert">
            {t('chat.redaction.requestFailed')}
          </Text>
        )}
        {state === 'already' && (
          <Text size="1" color="gray">
            {t('chat.redaction.alreadyAccessible')}
          </Text>
        )}
      </Flex>
    </Flex>
  );
}

/** The "partially based on sources you can't access" marker and one Request access row per hidden source. */
export function RedactedSourcesNotice({ redaction }: { redaction: NonNullable<CitationMaps['redaction']> }) {
  const { t } = useTranslation();
  return (
    <Box
      data-testid="redaction-notice"
      mt="3"
      p="3"
      style={{
        border: '1px solid var(--olive-5)',
        borderRadius: 'var(--radius-3)',
        background: 'var(--olive-2)',
      }}
    >
      <Text size="2" weight="medium">
        {redaction.redacted ? t('chat.redaction.hidden') : t('chat.redaction.partial')}
      </Text>
      {redaction.sources.map((source) => (
        <RedactedSourceRow key={source.recordId} source={source} />
      ))}
    </Box>
  );
}

/** Request access rows for attachments of a query that the viewer may not open. */
export function RedactedAttachments({ sources }: { sources: RequestAccessInfo[] }) {
  return (
    <Box data-testid="redacted-attachments" mb="2">
      {sources.map((source) => (
        <RedactedSourceRow key={source.recordId} source={source} />
      ))}
    </Box>
  );
}

/** Tool results and reasoning are never shown to someone the chat was shared with. */
export function DetailsHiddenNote() {
  const { t } = useTranslation();
  return (
    <Text size="1" color="gray" as="p" mt="2" data-testid="details-hidden-note">
      {t('chat.redaction.detailsHidden')}
    </Text>
  );
}

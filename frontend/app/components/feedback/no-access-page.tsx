'use client';

import { useState } from 'react';
import { Button, Flex, Heading, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/**
 * One page for revoked, never-had and deleted, so it confirms nothing about which.
 * Copies an id and a time only: no names, which could themselves be restricted.
 */
/** `onBack` is the way out: the page replaces a record view that had its own Back button. */
export function NoAccessPage({ recordId, onBack }: { recordId?: string; onBack?: () => void }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText([recordId && `Record: ${recordId}`, `Time: ${new Date().toISOString()}`].filter(Boolean).join('\n'));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <Flex direction="column" align="center" justify="center" gap="3" p="6" style={{ textAlign: 'center' }} data-testid="no-access-page">
      <MaterialIcon name="lock" size={48} color="var(--gray-9)" />
      <Heading as="h1" size="5">{t('accessNotice.noAccess.title')}</Heading>
      <Text size="2" color="gray" style={{ maxWidth: 480 }}>{t('accessNotice.noAccess.body')}</Text>
      <Text size="2" color="gray" style={{ maxWidth: 480 }}>{t('accessNotice.noAccess.justGotAccess')}</Text>
      <Flex gap="2" wrap="wrap" justify="center">
        {onBack ? (
          <Button size="2" style={{ minHeight: 44 }} onClick={onBack}>
            {t('common.back')}
          </Button>
        ) : null}
        <Button size="2" variant="soft" style={{ minHeight: 44 }} onClick={() => void copy()}>
          {t('accessNotice.noAccess.copyDetails')}
        </Button>
      </Flex>
      <Text size="1" color="gray" role="status">{copied ? t('accessNotice.noAccess.copied') : ''}</Text>
    </Flex>
  );
}

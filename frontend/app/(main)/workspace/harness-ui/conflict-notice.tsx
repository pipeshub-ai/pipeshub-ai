'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Flex, Text } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';

/**
 * A save refused because someone saved after this editor read (409): the
 * editor's values are kept. "Reload and reapply" reads the newer revision and
 * keeps the edits on top of it; "Discard" reads it and drops them.
 */
export function ConflictNotice({
  onReapply,
  onDiscard,
}: {
  onReapply: () => Promise<void>;
  onDiscard: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const run = (action: () => Promise<void>) => async () => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Flex
      direction="column"
      gap="2"
      role="alert"
      style={{
        padding: 'var(--space-3)',
        borderRadius: 'var(--radius-2)',
        background: 'var(--amber-a3)',
        border: '1px solid var(--amber-a6)',
      }}
    >
      <Flex align="start" gap="2">
        <MaterialIcon name="warning_amber" size={16} color="var(--amber-11)" />
        <Text size="2" style={{ color: 'var(--slate-12)' }}>
          {t('controlPlane.conflict.message')}
        </Text>
      </Flex>
      <Flex gap="2" justify="end" wrap="wrap">
        <Button type="button" size="1" variant="soft" disabled={busy} onClick={() => void run(onReapply)()}>
          {t('controlPlane.conflict.reapply')}
        </Button>
        <Button type="button" size="1" variant="soft" color="gray" disabled={busy} onClick={() => void run(onDiscard)()}>
          {t('controlPlane.conflict.discard')}
        </Button>
      </Flex>
    </Flex>
  );
}

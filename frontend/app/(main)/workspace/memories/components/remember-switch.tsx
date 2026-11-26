'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Switch, Text } from '@radix-ui/themes';
import { SettingsRow } from '../../components/settings-row';
import { SettingsSection } from '../../components/settings-section';
import { useMemoriesStore } from '../store';

/**
 * The person's own memory switch, at the top of their Memories page: on by
 * default; off, their chats neither use nor keep memories. Theirs alone,
 * whatever anyone else chose.
 */
export function RememberSwitch() {
  const { t } = useTranslation();
  const { remember, setRemember } = useMemoriesStore();
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);

  const change = async (next: boolean) => {
    setSaving(true);
    setFailed(false);
    try {
      await setRemember(next);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection title={t('memories.remember.sectionTitle')}>
      <SettingsRow label={t('memories.remember.title')} description={t('memories.remember.description')}>
        <Switch
          checked={remember.status === 'ready' ? remember.data : false}
          disabled={remember.status !== 'ready' || saving}
          onCheckedChange={(next) => void change(next)}
          aria-label={t('memories.remember.title')}
          style={{ cursor: 'pointer' }}
          size="1"
        />
      </SettingsRow>
      {remember.status === 'error' && (
        <Text size="1" as="p" role="alert" style={{ color: 'var(--red-11)', marginTop: 'var(--space-2)' }}>
          {t('memories.remember.unavailable')}
        </Text>
      )}
      {failed && (
        <Text size="1" as="p" role="alert" style={{ color: 'var(--red-11)', marginTop: 'var(--space-2)' }}>
          {t('memories.remember.failed')}
        </Text>
      )}
    </SettingsSection>
  );
}

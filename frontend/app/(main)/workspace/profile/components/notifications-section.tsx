'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, Switch, Callout } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SettingsSection } from './settings-section';
import {
  getBrowserNotificationPermission,
  isBrowserNotificationSupported,
  requestBrowserNotificationPermission,
} from '@/app/(main)/notifications/browser-notifications';
import { useBrowserNotificationsStore } from '@/app/(main)/notifications/browser-notifications-store';

export function NotificationsSection() {
  const { t } = useTranslation();
  const desktopEnabled = useBrowserNotificationsStore((s) => s.desktopEnabled);
  const setDesktopEnabled = useBrowserNotificationsStore((s) => s.setDesktopEnabled);

  const supported = isBrowserNotificationSupported();
  const [permission, setPermission] = useState(getBrowserNotificationPermission);
  const [isRequesting, setIsRequesting] = useState(false);

  const syncPermission = useCallback(() => {
    const next = getBrowserNotificationPermission();
    setPermission(next);
    if (next === 'denied' && useBrowserNotificationsStore.getState().desktopEnabled) {
      setDesktopEnabled(false);
    }
  }, [setDesktopEnabled]);

  useEffect(() => {
    syncPermission();
    window.addEventListener('focus', syncPermission);
    return () => window.removeEventListener('focus', syncPermission);
  }, [syncPermission]);

  const handleToggle = async (checked: boolean) => {
    if (!supported) return;

    if (!checked) {
      setDesktopEnabled(false);
      return;
    }

    if (permission === 'denied') return;

    if (permission === 'granted') {
      setDesktopEnabled(true);
      return;
    }

    setIsRequesting(true);
    try {
      const result = await requestBrowserNotificationPermission();
      setPermission(result);
      setDesktopEnabled(result === 'granted');
    } finally {
      setIsRequesting(false);
    }
  };

  const showBlockedHint = supported && permission === 'denied';
  const showUnsupportedHint = !supported;

  return (
    <SettingsSection title={t('workspace.profile.notifications.title')}>
      <Flex direction="column" gap="3">
        <Flex align="center" justify="between" style={{ width: '100%' }}>
          <Flex align="center" gap="3">
            <Flex
              align="center"
              justify="center"
              style={{
                width: 36,
                height: 36,
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--gray-4)',
                flexShrink: 0,
              }}
            >
              <MaterialIcon name="notifications" size={18} color="var(--gray-11)" />
            </Flex>
            <Box>
              <Text
                size="2"
                weight="medium"
                style={{ color: 'var(--gray-12)', display: 'block' }}
              >
                {t('workspace.profile.notifications.desktop')}
              </Text>
              <Text
                size="1"
                style={{
                  color: 'var(--gray-9)',
                  display: 'block',
                  marginTop: 2,
                  lineHeight: '16px',
                  fontWeight: 300,
                }}
              >
                {t('workspace.profile.notifications.desktopDescription')}
              </Text>
            </Box>
          </Flex>

          <Switch
            color="jade"
            size="2"
            checked={desktopEnabled}
            disabled={!supported || isRequesting}
            onCheckedChange={(checked) => {
              void handleToggle(checked);
            }}
            style={{ flexShrink: 0, cursor: supported ? 'pointer' : 'not-allowed' }}
          />
        </Flex>

        {showUnsupportedHint && (
          <Callout.Root color="gray" size="1" variant="soft">
            <Callout.Text>{t('workspace.profile.notifications.unsupported')}</Callout.Text>
          </Callout.Root>
        )}

        {showBlockedHint && (
          <Callout.Root color="amber" size="1" variant="soft">
            <Callout.Icon>
              <MaterialIcon name="info" size={14} color="var(--amber-11)" />
            </Callout.Icon>
            <Callout.Text>{t('workspace.profile.notifications.blockedHint')}</Callout.Text>
          </Callout.Root>
        )}
      </Flex>
    </SettingsSection>
  );
}

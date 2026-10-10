'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Box, Flex, Text, Switch, Separator } from '@radix-ui/themes';
import {
  useFeatureFlagsStore,
  selectChatMentionsEnabled,
  selectCollaborativeChatsEnabled,
} from '@/lib/store/feature-flags-store';
import {
  NotificationsApi,
  type NotificationPreferences,
  type NotificationPreferencesPatch,
} from '@/app/(main)/notifications/api';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { SettingsSection } from './settings-section';

type PreferenceKey =
  | 'email.chatShared'
  | 'email.ownershipTransferred'
  | 'inApp.chatActivity'
  | 'email.chatMentioned'
  | 'inApp.chatMentioned';

const ROWS: ReadonlyArray<{ key: PreferenceKey; labelKey: string }> = [
  { key: 'email.chatShared', labelKey: 'notifications.collab.preferences.emailChatShared' },
  { key: 'email.ownershipTransferred', labelKey: 'notifications.collab.preferences.emailOwnershipTransferred' },
  { key: 'inApp.chatActivity', labelKey: 'notifications.collab.preferences.inAppChatActivity' },
];

/** Shown only with `ENABLE_CHAT_MENTIONS`. */
const MENTION_ROWS: typeof ROWS = [
  { key: 'inApp.chatMentioned', labelKey: 'notifications.collab.preferences.inAppChatMentioned' },
  { key: 'email.chatMentioned', labelKey: 'notifications.collab.preferences.emailChatMentioned' },
];

function valueOf(prefs: NotificationPreferences, key: PreferenceKey): boolean {
  switch (key) {
    case 'email.chatShared':
      return prefs.email.chatShared;
    case 'email.ownershipTransferred':
      return prefs.email.ownershipTransferred;
    case 'inApp.chatActivity':
      return prefs.inApp.chatActivity;
    case 'email.chatMentioned':
      return prefs.email.chatMentioned ?? false;
    case 'inApp.chatMentioned':
      return prefs.inApp.chatMentioned ?? true;
  }
}

function patchOf(key: PreferenceKey, value: boolean): NotificationPreferencesPatch {
  switch (key) {
    case 'email.chatShared':
      return { email: { chatShared: value } };
    case 'email.ownershipTransferred':
      return { email: { ownershipTransferred: value } };
    case 'inApp.chatActivity':
      return { inApp: { chatActivity: value } };
    case 'email.chatMentioned':
      return { email: { chatMentioned: value } };
    case 'inApp.chatMentioned':
      return { inApp: { chatMentioned: value } };
  }
}

/** Email and in-app switches for collaborative chats. Hidden while the feature is off. */
export function ChatNotificationPreferencesSection() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const enabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const mentionsEnabled = useFeatureFlagsStore(selectChatMentionsEnabled);
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [pending, setPending] = useState<PreferenceKey | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    NotificationsApi.getPreferences()
      .then((p) => {
        if (!cancelled) setPrefs(p);
      })
      .catch(() => {
        if (!cancelled) setError(t('notifications.collab.preferences.loadFailed'));
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, t]);

  const toggle = useCallback(
    async (key: PreferenceKey, value: boolean) => {
      setPending(key);
      setError(null);
      try {
        setPrefs(await NotificationsApi.updatePreferences(patchOf(key, value)));
      } catch {
        setError(t('notifications.collab.preferences.updateFailed'));
      } finally {
        setPending(null);
      }
    },
    [t],
  );

  if (!enabled) return null;

  return (
    <Box style={{ marginBottom: 'var(--space-5)', minWidth: 0, maxWidth: '100%' }}>
      <SettingsSection
        title={t('notifications.collab.preferences.title')}
        description={t('notifications.collab.preferences.description')}
      >
        {(mentionsEnabled ? [...ROWS, ...MENTION_ROWS] : ROWS).map(({ key, labelKey }, i) => (
          <React.Fragment key={key}>
            {i > 0 && <Separator size="4" />}
            {/* Same narrow stacking as SettingsRow: label above, control below from the left edge. */}
            <Flex
              direction={isMobile ? 'column' : 'row'}
              align={isMobile ? 'stretch' : 'center'}
              justify="between"
              gap={isMobile ? '2' : '4'}
              style={{ width: '100%', minWidth: 0 }}
            >
              <Text
                size="2"
                weight="medium"
                style={{ color: 'var(--slate-12)', minWidth: 0, overflowWrap: 'anywhere' }}
              >
                {t(labelKey)}
              </Text>
              <Switch
                aria-label={t(labelKey)}
                checked={prefs ? valueOf(prefs, key) : false}
                disabled={prefs === null || pending !== null}
                onCheckedChange={(checked) => void toggle(key, checked)}
                style={isMobile ? { alignSelf: 'flex-start', flexShrink: 0 } : undefined}
              />
            </Flex>
          </React.Fragment>
        ))}
        {error && (
          <Text size="1" role="alert" style={{ color: 'var(--red-11)' }}>
            {error}
          </Text>
        )}
      </SettingsSection>
    </Box>
  );
}

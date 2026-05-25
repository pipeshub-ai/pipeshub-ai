'use client';

import { useCallback, useEffect, useState } from 'react';
import { Flex, Text, Heading, Button } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { NotificationsApi, type NotificationListItem } from './api';
import { useNotificationStore } from './store';
import { NotificationRow } from './notification-row';
import { useTranslation } from 'react-i18next';

export default function NotificationsPage() {
  const { t } = useTranslation();
  const notifications = useNotificationStore((s) => s.notifications);
  const setAll = useNotificationStore((s) => s.setAll);
  const markReadStore = useNotificationStore((s) => s.markRead);
  const removeStore = useNotificationStore((s) => s.remove);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await NotificationsApi.getAll();
      setAll(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('notifications.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [setAll, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const onMarkRead = async (n: NotificationListItem) => {
    if (n.status === 'Read' || !n._id) return;
    try {
      await NotificationsApi.markRead(n._id);
      markReadStore(n._id);
    } catch {
      setError(t('notifications.updateFailed'));
    }
  };

  const onDismiss = async (n: NotificationListItem) => {
    if (!n._id) return;
    try {
      await NotificationsApi.remove(n._id);
      removeStore(n._id);
    } catch {
      setError(t('notifications.removeFailed'));
    }
  };

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        backgroundColor: 'var(--slate-2)',
        padding: 'var(--space-4)',
        overflow: 'hidden',
      }}
    >
      <Flex align="center" justify="between" style={{ marginBottom: 'var(--space-4)' }}>
        <Heading size="6" style={{ color: 'var(--slate-12)' }}>
          {t('notifications.title')}
        </Heading>
        <Button variant="soft" size="2" color="gray" onClick={() => void load()}>
          {t('action.refresh')}
        </Button>
      </Flex>

      {error && (
        <Text size="2" style={{ color: 'var(--red-11)', marginBottom: 'var(--space-2)' }}>
          {error}
        </Text>
      )}

      {loading && notifications.length === 0 ? (
        <Text size="2" color="gray">
          {t('notifications.loading')}
        </Text>
      ) : notifications.length === 0 ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          style={{ flex: 1, color: 'var(--slate-11)' }}
        >
          <MaterialIcon name="notifications_none" size={48} color="var(--slate-8)" />
          <Text size="2" style={{ marginTop: 'var(--space-2)' }}>
            {t('notifications.empty')}
          </Text>
        </Flex>
      ) : (
        <Flex
          direction="column"
          gap="2"
          className="no-scrollbar"
          style={{ flex: 1, overflowY: 'auto' }}
        >
          {notifications.map((n) => (
            <NotificationRow
              key={n._id}
              notification={n}
              onMarkRead={(item) => void onMarkRead(item)}
              onDismiss={(item) => void onDismiss(item)}
              markReadLabel={t('notifications.markRead')}
              dismissLabel={t('notifications.dismiss')}
            />
          ))}
        </Flex>
      )}
    </Flex>
  );
}

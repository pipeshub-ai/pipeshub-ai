'use client';

import { useCallback, useEffect, useState } from 'react';
import { Flex, Text, Heading, Button, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { NotificationsApi, type NotificationListItem } from './api';
import { useNotificationStore } from './store';

function formatRelativeTime(iso?: string): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diff = Date.now() - t;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export default function NotificationsPage() {
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
      setError(e instanceof Error ? e.message : 'Failed to load notifications');
    } finally {
      setLoading(false);
    }
  }, [setAll]);

  useEffect(() => {
    void load();
  }, [load]);

  const onMarkRead = async (n: NotificationListItem) => {
    if (n.status === 'Read' || !n._id) return;
    try {
      await NotificationsApi.markRead(n._id);
      markReadStore(n._id);
    } catch {
      setError('Could not update notification');
    }
  };

  const onDismiss = async (n: NotificationListItem) => {
    if (!n._id) return;
    try {
      await NotificationsApi.remove(n._id);
      removeStore(n._id);
    } catch {
      setError('Could not remove notification');
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
          Notifications
        </Heading>
        <Button variant="soft" size="2" color="gray" onClick={() => void load()}>
          Refresh
        </Button>
      </Flex>

      {error && (
        <Text size="2" style={{ color: 'var(--red-11)', marginBottom: 'var(--space-2)' }}>
          {error}
        </Text>
      )}

      {loading && notifications.length === 0 ? (
        <Text size="2" color="gray">
          Loading…
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
            No notifications yet
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
            <Box
              key={n._id}
              style={{
                backgroundColor: 'var(--slate-1)',
                borderRadius: 'var(--radius-3)',
                border: '1px solid var(--slate-4)',
                padding: 'var(--space-3)',
                opacity: n.status === 'Read' ? 0.75 : 1,
              }}
            >
              <Flex align="start" justify="between" gap="3">
                <Flex align="start" gap="2" style={{ flex: 1, minWidth: 0 }}>
                  <MaterialIcon
                    name={n.type?.includes('WARNING') ? 'warning' : 'error_outline'}
                    size={22}
                    color={n.type?.includes('WARNING') ? 'var(--amber-9)' : 'var(--red-9)'}
                  />
                  <Flex direction="column" gap="1" style={{ minWidth: 0 }}>
                    <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                      {n.title}
                    </Text>
                    <Text size="1" color="gray">
                      {formatRelativeTime(n.createdAt)}
                      {n.appName ? ` · ${n.appName}` : ''}
                    </Text>
                  </Flex>
                </Flex>
                <Flex gap="2" align="center">
                  {n.status === 'Unread' && (
                    <Button size="1" variant="soft" onClick={() => void onMarkRead(n)}>
                      Mark read
                    </Button>
                  )}
                  <Button size="1" variant="ghost" color="gray" onClick={() => void onDismiss(n)}>
                    Dismiss
                  </Button>
                </Flex>
              </Flex>
            </Box>
          ))}
        </Flex>
      )}
    </Flex>
  );
}

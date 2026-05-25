'use client';

import { useState } from 'react';
import { Flex, Text, Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { NotificationListItem, NotificationSeverity } from './api';

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

function severityIcon(severity: NotificationSeverity): string {
  switch (severity) {
    case 'info':
      return 'info';
    case 'warning':
      return 'warning';
    case 'critical':
      return 'priority_high';
    default:
      return 'error_outline';
  }
}

function severityColor(severity: NotificationSeverity): string {
  switch (severity) {
    case 'info':
      return 'var(--blue-9)';
    case 'warning':
      return 'var(--amber-9)';
    case 'critical':
      return 'var(--red-11)';
    default:
      return 'var(--red-9)';
  }
}

export function NotificationRow({
  notification: n,
  onMarkRead,
  onDismiss,
  markReadLabel,
  dismissLabel,
}: {
  notification: NotificationListItem;
  onMarkRead: (n: NotificationListItem) => void;
  onDismiss: (n: NotificationListItem) => void;
  markReadLabel: string;
  dismissLabel: string;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const timeLabel = formatRelativeTime(n.createdAt);
  const severity = n.severity ?? 'error';
  const title = n.payload?.title ?? '';
  const message = n.payload?.message ?? '';

  const isRead = n.status === 'Read';

  return (
    <Box
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        boxSizing: 'border-box',
        backgroundColor: isHovered ? 'var(--olive-3)' : 'transparent',
        borderBottom: '1px solid var(--olive-4)',
        padding: 'var(--space-3) var(--space-4)',
        opacity: isRead ? 0.65 : 1,
      }}
    >
      <Flex align="start" gap="2">
        <Box
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 28,
            height: 28,
            borderRadius: 'var(--radius-2)',
            backgroundColor: 'var(--olive-3)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon
            name={severityIcon(severity)}
            size={16}
            color={severityColor(severity)}
          />
        </Box>

        <Flex align="start" justify="between" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }} truncate>
              {title}
            </Text>
            <Text
              size="1"
              color="gray"
              style={{
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {message}
            </Text>
          </Flex>

          <Box
            style={{
              flexShrink: 0,
              alignSelf: 'flex-start',
              display: 'flex',
              justifyContent: 'flex-end',
              alignItems: 'center',
              minHeight: 26,
              width: n.status === 'Unread' ? 84 : 40,
            }}
          >
            {isHovered ? (
              <Flex align="center" gap="2">
                {n.status === 'Unread' && (
                  <IconButton
                    variant="ghost"
                    color="gray"
                    size="1"
                    onClick={() => onMarkRead(n)}
                    aria-label={markReadLabel}
                    style={{ flexShrink: 0 }}
                  >
                    <MaterialIcon name="done" size={16} color="var(--slate-11)" />
                  </IconButton>
                )}
                <IconButton
                  variant="ghost"
                  color="gray"
                  size="1"
                  onClick={() => onDismiss(n)}
                  aria-label={dismissLabel}
                  style={{ flexShrink: 0 }}
                >
                  <MaterialIcon name="close" size={16} color="var(--slate-11)" />
                </IconButton>
              </Flex>
            ) : (
              <Text size="1" color="gray" style={{ whiteSpace: 'nowrap' }}>
                {timeLabel}
              </Text>
            )}
          </Box>
        </Flex>
      </Flex>
    </Box>
  );
}

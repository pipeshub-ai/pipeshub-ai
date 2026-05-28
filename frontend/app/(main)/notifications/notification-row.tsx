'use client';

import Link from 'next/link';
import { Flex, Text, Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import type { NotificationListItem, NotificationSeverity } from './api';

/** App-relative paths from the API may omit a leading slash; Next.js Link needs one. */
function notificationHref(redirectLink: string): string | null {
  const trimmed = redirectLink.trim();
  if (!trimmed) return null;
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
}

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
    case 'success':
      return 'check_circle';
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
    case 'success':
      return 'var(--green-9)';
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
  const timeLabel = formatRelativeTime(n.createdAt);
  const severity = n.severity ?? 'error';
  const title = n.payload?.title ?? '';
  const message = n.payload?.message ?? '';
  const href = notificationHref(n.payload?.redirectLink ?? '');

  const isRead = n.status === 'read';
  const titleStyle = { color: 'var(--slate-12)' };

  return (
    <Box
      data-ph-notification-row=""
      style={{
        width: '100%',
        boxSizing: 'border-box',
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
            {href ? (
              <Text size="2" weight="medium" style={titleStyle} truncate asChild>
                <Link
                  href={href}
                  data-ph-notification-row-title-link=""
                  style={{
                    minWidth: 0,
                    display: 'block',
                  }}
                  {...(/^https?:\/\//i.test(href)
                    ? { target: '_blank', rel: 'noopener noreferrer' }
                    : {})}
                >
                  {title}
                </Link>
              </Text>
            ) : (
              <Text size="2" weight="medium" style={titleStyle} truncate>
                {title}
              </Text>
            )}
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
              width: n.status === 'unread' ? 84 : 40,
            }}
          >
            <Flex
              data-ph-notification-row-actions=""
              align="center"
              gap="2"
            >
              {n.status === 'unread' && (
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
            <Text
              data-ph-notification-row-time=""
              size="1"
              color="gray"
              style={{ whiteSpace: 'nowrap' }}
            >
              {timeLabel}
            </Text>
          </Box>
        </Flex>
      </Flex>
    </Box>
  );
}

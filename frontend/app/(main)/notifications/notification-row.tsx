'use client';

import { useState, type CSSProperties } from 'react';
import { Link } from '@/lib/navigation';
import { Flex, Text, Box, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { useTranslation } from 'react-i18next';
import type { NotificationListItem, NotificationSeverity } from './api';
import { NOTIFICATIONS_PANEL_TOOLTIP_CLASS } from './notification-filter-menu';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';
import { collabChatTitle, collabSessionId, describeCollabNotification } from './collab-notifications';

export type NotificationRowAction =
  | 'markRead'
  | 'markUnread'
  | 'archive'
  | 'unarchive'
  | 'dismiss'
  | 'mute';

/** App-relative paths from the API may omit a leading slash; Next.js Link needs one. */
/**
 * An absolute http(s) URL, or a protocol-relative one (`//host`, and `/\host`, which browsers
 * read the same way): it leaves the app, so it opens in a new tab and is never handled in-app.
 */
export function isExternalNotificationHref(href: string): boolean {
  return /^(?:https?:)?[/\\]{2}/i.test(href);
}

function notificationHref(redirectLink: string): string | null {
  const trimmed = redirectLink.trim();
  if (!trimmed) return null;
  if (isExternalNotificationHref(trimmed)) return trimmed;
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
}

/** The conversation an in-app chat link such as `/chat/?conversationId=…` opens, or null. */
export function chatConversationIdFromHref(href: string): string | null {
  if (!href.startsWith('/') || isExternalNotificationHref(href)) return null;
  const url = new URL(href, 'http://app.invalid');
  if (url.origin !== 'http://app.invalid') return null;
  if (url.pathname.replace(/\/+$/, '') !== '/chat') return null;
  return url.searchParams.get('conversationId') || null;
}

function formatRelativeTime(
  iso: string | undefined,
  lang: string,
  compact = false,
): string {
  if (!iso) return '';
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return '';
  const diff = Date.now() - ts;
  const secs = Math.floor(diff / 1000);
  const mins = Math.floor(diff / 60000);
  const hrs = Math.floor(mins / 60);
  const days = Math.floor(hrs / 24);
  const rtf = new Intl.RelativeTimeFormat(lang, {
    numeric: 'auto',
    style: compact ? 'narrow' : 'long',
  });
  if (secs < 60) return rtf.format(0, 'second');
  if (mins < 60) return rtf.format(-mins, 'minute');
  if (hrs < 24) return rtf.format(-hrs, 'hour');
  return rtf.format(-days, 'day');
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

function typeIcon(type: string, severity: NotificationSeverity): string {
  switch (type) {
    case 'chat.shared':
      return 'share';
    case 'chat.mentioned':
      return 'alternate_email';
    case 'chat.activity':
      return 'forum';
    case 'chat.deleted':
      return 'delete';
    case 'chat.accessChanged':
      return 'lock_open';
    case 'chat.ownershipTransferred':
      return 'swap_horiz';
    default:
      return type.startsWith('agent') ? 'smart_toy' : severityIcon(severity);
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

const titleWrapStyle: CSSProperties = {
  minWidth: 0,
  display: 'block',
  width: '100%',
  whiteSpace: 'normal',
  overflowWrap: 'anywhere',
};

function NotificationTitle({
  title,
  href,
  style,
  onNavigate,
}: {
  title: string;
  href: string | null;
  style: CSSProperties;
  onNavigate?: () => void;
}) {
  if (!title) return null;

  if (href) {
    return (
      <Text size="2" asChild>
        <Link
          href={href}
          data-ph-notification-row-title-link=""
          style={{ ...titleWrapStyle, ...style }}
          onClick={onNavigate}
          {...(isExternalNotificationHref(href)
            ? { target: '_blank', rel: 'noopener noreferrer' }
            : {})}
        >
          {title}
        </Link>
      </Text>
    );
  }

  return (
    <Text size="2" style={{ ...style, ...titleWrapStyle }}>
      {title}
    </Text>
  );
}

function NotificationActionButton({
  label,
  icon,
  onClick,
  variant = 'default',
  disabled = false,
  loading = false,
}: {
  label: string;
  icon: string;
  onClick: () => void;
  variant?: 'default' | 'danger';
  disabled?: boolean;
  loading?: boolean;
}) {
  const [isHovered, setIsHovered] = useState(false);
  const isDangerHover = variant === 'danger' && isHovered && !disabled && !loading;

  return (
    <Box style={{ display: 'inline-flex', flexShrink: 0, position: 'relative' }}>
      <Tooltip
        className={NOTIFICATIONS_PANEL_TOOLTIP_CLASS}
        content={label}
        side="bottom"
      >
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onClick}
          aria-label={label}
          disabled={disabled || loading}
          onMouseEnter={() => setIsHovered(true)}
          onMouseLeave={() => setIsHovered(false)}
          style={{
            flexShrink: 0,
            cursor: disabled || loading ? 'not-allowed' : 'pointer',
            opacity: disabled || loading ? 0.5 : 1,
            pointerEvents: disabled || loading ? 'auto' : undefined,
          }}
        >
          {loading ? (
            <Spinner size={16} />
          ) : (
            <MaterialIcon
              name={icon}
              size={16}
              color={isDangerHover ? 'var(--red-11)' : 'var(--slate-11)'}
            />
          )}
        </IconButton>
      </Tooltip>
    </Box>
  );
}

export function NotificationRow({
  notification: n,
  onMarkRead,
  onMarkUnread,
  onArchive,
  onUnarchive,
  onDismiss,
  markReadLabel,
  markUnreadLabel,
  archiveLabel,
  unarchiveLabel,
  dismissLabel,
  compactTime = false,
  pendingAction = null,
  muted = false,
  onToggleMute,
  muteLabel = '',
  unmuteLabel = '',
  onOpenLink,
}: {
  notification: NotificationListItem;
  /** Called before an in-app link navigates, including to the page already open. */
  onOpenLink?: (href: string) => void;
  onMarkRead: (n: NotificationListItem) => void;
  onMarkUnread: (n: NotificationListItem) => void;
  onArchive: (n: NotificationListItem) => void;
  onUnarchive: (n: NotificationListItem) => void;
  onDismiss: (n: NotificationListItem) => void;
  markReadLabel: string;
  markUnreadLabel: string;
  archiveLabel: string;
  unarchiveLabel: string;
  dismissLabel: string;
  compactTime?: boolean;
  pendingAction?: NotificationRowAction | null;
  /** Collaboration rows only: the chat is muted for this user. */
  muted?: boolean;
  onToggleMute?: (n: NotificationListItem) => void;
  muteLabel?: string;
  unmuteLabel?: string;
}) {
  const { i18n, t } = useTranslation();
  const collabEnabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const timeLabel = formatRelativeTime(n.createdAt, i18n.language, compactTime);
  const severity = n.severity ?? 'error';
  const collabText = collabEnabled ? describeCollabNotification(n, t) : null;
  const title = collabText?.title ?? n.title ?? '';
  const message = collabText?.message ?? n.message ?? '';
  // A deleted chat has nothing to open.
  const href = n.type === 'chat.deleted' && collabEnabled ? null : notificationHref(n.redirectLink ?? '');
  const isCollabType = collabText != null;
  const canMute = collabEnabled && onToggleMute != null && collabSessionId(n) != null;

  const isRead = n.status === 'read' || n.status === 'archived';
  const isUnread = !isRead;
  const chatTitle = collabEnabled ? collabChatTitle(n) : undefined;
  const isDeleted = n.type === 'chat.deleted';
  const iconTint = isDeleted ? 'var(--red-11)' : isUnread ? 'var(--accent-11)' : 'var(--slate-10)';
  const titleStyle = {
    color: isUnread ? 'var(--slate-12)' : 'var(--slate-11)',
    fontWeight: isUnread ? 600 : 400,
  };
  const isBusy = pendingAction != null;

  return (
    <Box
      data-ph-notification-row=""
      data-read={isRead ? 'true' : 'false'}
      data-action-pending={isBusy ? 'true' : 'false'}
      style={{
        width: '100%',
        boxSizing: 'border-box',
        borderBottom: '1px solid var(--olive-4)',
        padding: 'var(--space-3) var(--space-4)',
        minHeight: 56,
        position: 'relative',
      }}
    >
      {isUnread ? (
        <Box
          data-ph-notification-unread-dot=""
          aria-hidden="true"
          style={{
            position: 'absolute',
            left: 6,
            top: 'calc(var(--space-3) + 11px)',
            width: 6,
            height: 6,
            borderRadius: '50%',
            backgroundColor: 'var(--accent-9)',
          }}
        />
      ) : null}
      <Flex align="start" gap="2">
        <Box
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 28,
            height: 28,
            borderRadius: 'var(--radius-2)',
            backgroundColor: isUnread ? 'var(--accent-a3)' : 'var(--olive-3)',
            flexShrink: 0,
          }}
        >
          <MaterialIcon
            name={typeIcon(n.type, severity)}
            size={16}
            color={collabEnabled && isCollabType ? iconTint : severityColor(severity)}
          />
        </Box>

        <Flex align="start" justify="between" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0 }}>
            <Box>
              <NotificationTitle
                title={title}
                href={href}
                style={titleStyle}
                onNavigate={() => {
                  if (!isRead) onMarkRead(n);
                  if (href && href.startsWith('/') && !isExternalNotificationHref(href)) onOpenLink?.(href);
                }}
              />
            </Box>
            {chatTitle ? (
              <Text
                data-ph-notification-chat-title=""
                size="1"
                title={chatTitle}
                style={{
                  color: 'var(--slate-12)',
                  fontWeight: 500,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {chatTitle}
              </Text>
            ) : null}
            {message ? (
              <div
                title={message}
                style={{
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  overflowWrap: 'anywhere',
                  fontSize: 'var(--font-size-1)',
                  lineHeight: 'var(--line-height-1)',
                  letterSpacing: 'var(--letter-spacing-1)',
                  color: 'var(--gray-11)',
                }}
              >
                {message}
              </div>
            ) : null}
          </Flex>

          <Box data-ph-notification-row-meta="">
            <Flex
              data-ph-notification-row-actions=""
              align="center"
              gap="2"
              style={{ justifyContent: 'flex-end' }}
            >
              {n.status === 'unread' ? (
                <NotificationActionButton
                  label={markReadLabel}
                  icon="done"
                  onClick={() => onMarkRead(n)}
                  disabled={isBusy}
                  loading={pendingAction === 'markRead'}
                />
              ): n.status != 'archived' ? (
                <NotificationActionButton
                  label={markUnreadLabel}
                  icon="mark_email_unread"
                  onClick={() => onMarkUnread(n)}
                  disabled={isBusy}
                  loading={pendingAction === 'markUnread'}
                />
              ) : null}
              {canMute ? (
                <NotificationActionButton
                  label={muted ? unmuteLabel : muteLabel}
                  icon={muted ? 'notifications_off' : 'notifications_active'}
                  onClick={() => onToggleMute(n)}
                  disabled={isBusy}
                  loading={pendingAction === 'mute'}
                />
              ) : null}
              {n.status !== 'archived' ? (
                <NotificationActionButton
                  label={archiveLabel}
                  icon="archive"
                  onClick={() => onArchive(n)}
                  disabled={isBusy}
                  loading={pendingAction === 'archive'}
                />
              ) : (
                <NotificationActionButton
                  label={unarchiveLabel}
                  icon="unarchive"
                  onClick={() => onUnarchive(n)}
                  disabled={isBusy}
                  loading={pendingAction === 'unarchive'}
                />
              )}
              <NotificationActionButton
                label={dismissLabel}
                icon="close"
                variant="danger"
                onClick={() => onDismiss(n)}
                disabled={isBusy}
                loading={pendingAction === 'dismiss'}
              />
            </Flex>
            <Text
              data-ph-notification-row-time=""
              size="1"
              color="gray"
            >
              {timeLabel}
            </Text>
          </Box>
        </Flex>
      </Flex>
    </Box>
  );
}

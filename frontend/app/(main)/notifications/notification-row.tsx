'use client';

import { useState, useRef, useLayoutEffect, useCallback, type CSSProperties } from 'react';
import Link from 'next/link';
import { Flex, Text, Box, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { Spinner } from '@/app/components/ui/spinner';
import { useTranslation } from 'react-i18next';
import type { NotificationListItem, NotificationSeverity } from './api';
import { NOTIFICATIONS_PANEL_TOOLTIP_CLASS } from './notification-filter-menu';

export type NotificationRowAction =
  | 'markRead'
  | 'markUnread'
  | 'archive'
  | 'unarchive'
  | 'dismiss';

/** App-relative paths from the API may omit a leading slash; Next.js Link needs one. */
function notificationHref(redirectLink: string): string | null {
  const trimmed = redirectLink.trim();
  if (!trimmed) return null;
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
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
      <Text size="2" weight="medium" asChild>
        <Link
          href={href}
          data-ph-notification-row-title-link=""
          style={{ ...titleWrapStyle, ...style }}
          onClick={onNavigate}
          {...(/^https?:\/\//i.test(href)
            ? { target: '_blank', rel: 'noopener noreferrer' }
            : {})}
        >
          {title}
        </Link>
      </Text>
    );
  }

  return (
    <Text size="2" weight="medium" style={{ ...style, ...titleWrapStyle }}>
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
}: {
  notification: NotificationListItem;
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
}) {
  const { i18n } = useTranslation();
  const [isExpanded, setIsExpanded] = useState(false);
  const [isTruncated, setIsTruncated] = useState(false);
  // Hidden unclamped clone used solely for measuring the natural text height.
  // scrollHeight on a -webkit-line-clamp element is unreliable in some browsers
  // (it can return the clamped height instead of the full content height), so we
  // measure on a separate, unconstrained div instead.
  const messageContainerRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLDivElement>(null);

  const timeLabel = formatRelativeTime(n.createdAt, i18n.language, compactTime);
  const severity = n.severity ?? 'error';
  const title = n.title ?? '';
  const message = n.message ?? '';
  const href = notificationHref(n.redirectLink ?? '');

  const isRead = n.status === 'read' || n.status === 'archived';
  const readOpacity = isRead ? 0.65 : 1;
  const titleStyle = { color: 'var(--slate-12)' };
  const isBusy = pendingAction != null;

  const measureMessageTruncation = useCallback(() => {
    const el = measureRef.current;
    if (!el) return;
    const lineHeight = parseFloat(getComputedStyle(el).lineHeight) || 16;
    const truncated = el.scrollHeight > lineHeight * 2 + 1;
    setIsTruncated(truncated);
    if (!truncated) {
      setIsExpanded(false);
    }
  }, []);

  useLayoutEffect(() => {
    setIsExpanded(false);
  }, [message]);

  useLayoutEffect(() => {
    measureMessageTruncation();
    const container = messageContainerRef.current;
    if (!container) return;
    const ro = new ResizeObserver(measureMessageTruncation);
    ro.observe(container);
    return () => ro.disconnect();
  }, [measureMessageTruncation, message]);

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
            opacity: readOpacity,
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
            <Box style={{ opacity: readOpacity }}>
              <NotificationTitle
                title={title}
                href={href}
                style={titleStyle}
                onNavigate={() => {
                  if (!isRead) onMarkRead(n);
                }}
              />
            </Box>
            <Box ref={messageContainerRef} style={{ position: 'relative', minWidth: 0, width: '100%' }}>
              {/* Invisible unclamped clone — used only to measure full text height */}
              <div
                ref={measureRef}
                aria-hidden="true"
                style={{
                  visibility: 'hidden',
                  pointerEvents: 'none',
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  fontSize: 'var(--font-size-1)',
                  lineHeight: 'var(--line-height-1)',
                  letterSpacing: 'var(--letter-spacing-1)',
                  whiteSpace: 'normal',
                  overflow: 'visible',
                }}
              >
                {message}
              </div>
              <div
                style={{
                  display: isExpanded ? 'block' : '-webkit-box',
                  WebkitLineClamp: isExpanded ? undefined : 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  paddingRight: (isTruncated && !isExpanded) ? '58px' : '0',
                  fontSize: 'var(--font-size-1)',
                  lineHeight: 'var(--line-height-1)',
                  letterSpacing: 'var(--letter-spacing-1)',
                  color: 'var(--gray-11)',
                }}
              >
                {message}
              </div>
              {isTruncated && !isExpanded && (
                <span
                  role="button"
                  tabIndex={0}
                  onClick={() => setIsExpanded(true)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') setIsExpanded(true);
                  }}
                  style={{
                    position: 'absolute',
                    bottom: 0,
                    right: 0,
                    cursor: 'pointer',
                    color: 'var(--accent-11)',
                    fontSize: 'var(--font-size-1)',
                    lineHeight: 'var(--line-height-1)',
                    userSelect: 'none',
                  }}
                >
                  show more
                </span>
              )}
            </Box>
            {isExpanded && isTruncated && (
              <span
                role="button"
                tabIndex={0}
                onClick={() => setIsExpanded(false)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') setIsExpanded(false);
                }}
                style={{
                  display: 'inline-block',
                  marginTop: '2px',
                  cursor: 'pointer',
                  color: 'var(--accent-11)',
                  fontSize: 'var(--font-size-1)',
                  lineHeight: 'var(--line-height-1)',
                  userSelect: 'none',
                }}
              >
                show less
              </span>
            )}
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

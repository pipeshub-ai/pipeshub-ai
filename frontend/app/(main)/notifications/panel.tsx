'use client';

import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { Theme, Flex, Text, Box, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { NotificationsApi, type NotificationListItem } from './api';
import { useNotificationStore } from './store';
import { useTranslation } from 'react-i18next';
import { usePathname, useSearchParams } from 'next/navigation';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import {
  PANEL_MAX_WIDTH,
  PANEL_MIN_WIDTH,
  useNotificationPanelWidthStore,
} from './panel-width-store';

const TRANSITION = '0.25s cubic-bezier(0.4, 0, 0.2, 1)';

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

function NotificationRow({
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

  return (
    <Box
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        position: 'relative',
        backgroundColor: n.status === 'Read' ? 'transparent' : 'var(--slate-1)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-4)',
        padding: 'var(--space-3)',
        opacity: n.status === 'Read' ? 0.65 : 1,
      }}
    >
      <Flex
        align="center"
        gap="1"
        style={{
          position: 'absolute',
          top: 'var(--space-2)',
          right: 'var(--space-2)',
          opacity: isHovered ? 1 : 0,
          pointerEvents: isHovered ? 'auto' : 'none',
          transition: 'opacity 0.15s ease',
        }}
      >
        {n.status === 'Unread' && (
          <IconButton
            variant="soft"
            color="gray"
            size="1"
            onClick={() => onMarkRead(n)}
            aria-label={markReadLabel}
          >
            <MaterialIcon name="done" size={14} color="var(--slate-11)" />
          </IconButton>
        )}
        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={() => onDismiss(n)}
          aria-label={dismissLabel}
        >
          <MaterialIcon name="close" size={14} color="var(--slate-11)" />
        </IconButton>
      </Flex>

      <Flex align="start" gap="2">
        <MaterialIcon
          name={n.type?.includes('WARNING') ? 'warning' : 'error_outline'}
          size={16}
          color={n.type?.includes('WARNING') ? 'var(--amber-9)' : 'var(--red-9)'}
        />
        <Flex direction="column" gap="1" style={{ flex: 1, minWidth: 0, paddingRight: isHovered ? 56 : 0 }}>
          <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }} truncate>
            {n.title}
          </Text>
          {n.appName && (
            <Text size="1" color="gray">
              {n.appName}
            </Text>
          )}
          <Text size="1" color="gray">
            {formatRelativeTime(n.createdAt)}
          </Text>
        </Flex>
      </Flex>
    </Box>
  );
}

/**
 * Floating notification panel.
 *
 * Portaled to document.body and positioned with `position: fixed` so it
 * overlays the main content area instead of pushing it. The left offset
 * tracks the sidebar width so the panel always appears just to the right
 * of the sidebar.
 */
export function NotificationsPanel() {
  const { t } = useTranslation();
  const isPanelOpen = useNotificationStore((s) => s.isPanelOpen);
  const closePanel = useNotificationStore((s) => s.closePanel);
  const notifications = useNotificationStore((s) => s.notifications);
  const setAll = useNotificationStore((s) => s.setAll);
  const markReadStore = useNotificationStore((s) => s.markRead);
  const removeStore = useNotificationStore((s) => s.remove);

  const pathname = usePathname();
  const searchParams = useSearchParams();
  const searchKey = searchParams.toString();

  // Close when route or query changes (e.g. Collections, recent chat ?conversationId=).
  const prevRouteRef = useRef({ pathname, searchKey });
  useEffect(() => {
    const prev = prevRouteRef.current;
    if (prev.pathname === pathname && prev.searchKey === searchKey) return;
    prevRouteRef.current = { pathname, searchKey };
    closePanel();
  }, [pathname, searchKey, closePanel]);

  const sidebarWidth = useSidebarWidthStore((s) => s.sidebarWidth);
  const isNavCollapsed = useSidebarWidthStore((s) => s.isNavCollapsed);
  const isMobile = useIsMobile();
  const panelWidth = useNotificationPanelWidthStore((s) => s.panelWidth);
  const setPanelWidth = useNotificationPanelWidthStore((s) => s.setPanelWidth);

  // On mobile the sidebar is a fixed overlay and doesn't consume layout space,
  // so the panel should start from the left edge.
  const leftOffset = isMobile || isNavCollapsed ? 0 : sidebarWidth;

  // ── Animation state ────────────────────────────────────────────────────────
  // `isVisible`  — controls whether the portal is in the DOM at all.
  // `isClosing`  — true during the exit animation; keeps portal mounted until
  //                onAnimationEnd fires, then sets isVisible = false.
  const [isVisible, setIsVisible] = useState(false);
  const [isClosing, setIsClosing] = useState(false);
  // Guards against triggering a close animation on the very first render
  // (when isPanelOpen is false but the panel has never been opened).
  const hasOpenedRef = useRef(false);

  useEffect(() => {
    if (isPanelOpen) {
      hasOpenedRef.current = true;
      setIsClosing(false);
      setIsVisible(true);
    } else if (hasOpenedRef.current) {
      // Panel was open before — play exit animation, then unmount.
      setIsClosing(true);
    }
  }, [isPanelOpen]);

  const handleAnimationEnd = () => {
    if (isClosing) {
      setIsVisible(false);
      setIsClosing(false);
    }
  };
  // ──────────────────────────────────────────────────────────────────────────

  // ── Live-track sidebar width during drag ───────────────────────────────────
  // The sidebar resize handler mutates DOM styles directly (no store update
  // until mouseup). A ResizeObserver on the sidebar slot element fires every
  // pixel during drag, so we patch style.left in-place instead of waiting for
  // a React re-render triggered by the store update.
  const panelRef = useRef<HTMLDivElement>(null);
  const widthRef = useRef(panelWidth);
  const [isResizingPanel, setIsResizingPanel] = useState(false);
  const [resizeHandleHovered, setResizeHandleHovered] = useState(false);

  useEffect(() => {
    widthRef.current = panelWidth;
  }, [panelWidth]);

  const handleResizeMouseDown = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      const el = panelRef.current;
      if (!el) return;

      setIsResizingPanel(true);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';

      const onMouseMove = (ev: MouseEvent) => {
        const rect = el.getBoundingClientRect();
        const next = Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, ev.clientX - rect.left));
        widthRef.current = next;
        el.style.width = `${next}px`;
      };

      const onMouseUp = () => {
        setIsResizingPanel(false);
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        setPanelWidth(widthRef.current);
      };

      document.addEventListener('mousemove', onMouseMove);
      document.addEventListener('mouseup', onMouseUp);
    },
    [setPanelWidth],
  );

  useEffect(() => {
    if (!isVisible) return;
    const slot = document.querySelector<HTMLElement>('[data-ph-sidebar-slot]');
    if (!slot) return;

    const observer = new ResizeObserver((entries) => {
      const el = panelRef.current;
      if (!el) return;
      const w = Math.round(entries[0].contentRect.width);
      el.style.left = `${w}px`;
    });

    observer.observe(slot);
    return () => observer.disconnect();
  }, [isVisible]);
  // ──────────────────────────────────────────────────────────────────────────

  const [loading, setLoading] = useState(false);
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
    if (isPanelOpen) void load();
  }, [isPanelOpen, load]);


  // Close on Escape
  useEffect(() => {
    if (!isPanelOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePanel();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isPanelOpen, closePanel]);

  // Close when clicking outside the panel (main content, sidebar items, etc.).
  // Exclude the notifications toggle so mousedown does not fight with its click handler.
  useEffect(() => {
    if (!isPanelOpen) return;

    const handlePointerDown = (e: MouseEvent) => {
      const target = e.target;
      if (!(target instanceof Node)) return;
      if (panelRef.current?.contains(target)) return;
      if (target instanceof Element && target.closest('[data-ph-notifications-trigger]')) return;
      closePanel();
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [isPanelOpen, closePanel]);

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

  if (!isVisible || typeof document === 'undefined') return null;

  return createPortal(
    <Theme appearance="inherit" hasBackground={false}>
      <style>{`
        @keyframes notif-panel-slide-in {
          from { opacity: 0; transform: translateX(-16px); }
          to   { opacity: 1; transform: translateX(0); }
        }
        @keyframes notif-panel-slide-out {
          from { opacity: 1; transform: translateX(0); }
          to   { opacity: 0; transform: translateX(-16px); }
        }
      `}</style>
      <Box
        ref={panelRef}
        data-ph-notifications-panel=""
        role="complementary"
        aria-label={t('notifications.title')}
        onAnimationEnd={handleAnimationEnd}
        style={{
          position: 'fixed',
          top: 0,
          left: `${leftOffset}px`,
          bottom: 0,
          width: `${panelWidth}px`,
          zIndex: 9100,
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--olive-1)',
          borderRight: '1px solid var(--olive-4)',
          boxShadow: '4px 0 20px rgba(0, 0, 0, 0.09)',
          animation: isClosing
            ? `notif-panel-slide-out ${TRANSITION} forwards`
            : `notif-panel-slide-in ${TRANSITION}`,
        }}
      >
        {/* Resize handle — right edge */}
        <Box
          onMouseDown={handleResizeMouseDown}
          onMouseEnter={() => setResizeHandleHovered(true)}
          onMouseLeave={() => setResizeHandleHovered(false)}
          aria-hidden
          style={{
            position: 'absolute',
            top: 0,
            right: -2,
            width: 4,
            height: '100%',
            cursor: 'col-resize',
            zIndex: 20,
          }}
        >
          <Box
            style={{
              position: 'absolute',
              top: 0,
              left: 1,
              width: 2,
              height: '100%',
              borderRadius: 1,
              transition: 'opacity 0.15s',
              opacity: resizeHandleHovered || isResizingPanel ? 1 : 0,
              backgroundColor: 'var(--olive-8)',
            }}
          />
        </Box>
        {/* ── Header ──────────────────────────────────────────── */}
        <Flex
          align="center"
          justify="between"
          style={{
            padding: 'var(--space-3) var(--space-2) var(--space-3) var(--space-4)',
            borderBottom: '1px solid var(--olive-4)',
            flexShrink: 0,
          }}
        >
          <Flex align="center" gap="2">
            <MaterialIcon name="notifications" size={16} color="var(--slate-11)" />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('notifications.title')}
            </Text>
          </Flex>
          <Flex align="center" gap="1">
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={() => void load()}
              disabled={loading}
              aria-label={t('action.refresh')}
            >
              <MaterialIcon name="refresh" size={14} color="var(--slate-11)" />
            </IconButton>
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={closePanel}
              aria-label="Close notifications"
            >
              <MaterialIcon name="close" size={14} color="var(--slate-11)" />
            </IconButton>
          </Flex>
        </Flex>

        {/* ── Body ────────────────────────────────────────────── */}
        <Box
          className="no-scrollbar"
          style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-3)' }}
        >
          {error && (
            <Text
              size="1"
              style={{ color: 'var(--red-11)', marginBottom: 'var(--space-2)', display: 'block' }}
            >
              {error}
            </Text>
          )}

          {loading && notifications.length === 0 ? (
            <Flex align="center" justify="center" style={{ paddingTop: 'var(--space-8)' }}>
              <Text size="2" color="gray">
                {t('notifications.loading')}
              </Text>
            </Flex>
          ) : notifications.length === 0 ? (
            <Flex
              direction="column"
              align="center"
              justify="center"
              gap="2"
              style={{ paddingTop: 'var(--space-8)' }}
            >
              <MaterialIcon name="notifications_none" size={40} color="var(--slate-8)" />
              <Text size="2" color="gray">
                {t('notifications.empty')}
              </Text>
            </Flex>
          ) : (
            <Flex direction="column" gap="2">
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
        </Box>
      </Box>
    </Theme>,
    document.body,
  );
}

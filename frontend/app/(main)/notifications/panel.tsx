'use client';

import { useCallback, useEffect, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import { SidebarLoadMoreButton } from '@/app/(main)/knowledge-base/sidebar/sidebar-load-more-button';
import { createPortal } from 'react-dom';
import { Theme, Flex, Text, Box, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { NotificationsApi, type NotificationListFilter, type NotificationListItem } from './api';
import { useNotificationStore } from './store';
import { NotificationRow } from './notification-row';
import {
  NotificationFilterMenu,
  NOTIFICATIONS_PANEL_TOOLTIP_CLASS,
} from './notification-filter-menu';
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
  const hasMore = useNotificationStore((s) => s.hasMore);
  const isLoadingMore = useNotificationStore((s) => s.isLoadingMore);
  const loadMore = useNotificationStore((s) => s.loadMore);
  const setInitialPage = useNotificationStore((s) => s.setInitialPage);
  const markReadStore = useNotificationStore((s) => s.markRead);
  const markAllReadStore = useNotificationStore((s) => s.markAllRead);
  const removeStore = useNotificationStore((s) => s.remove);
  const listFilter = useNotificationStore((s) => s.listFilter);
  const setListFilter = useNotificationStore((s) => s.setListFilter);
  const unreadCount = useNotificationStore((s) => s.unreadCount);

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
  const [markingAllRead, setMarkingAllRead] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await NotificationsApi.list(
        listFilter === 'unread' ? { status: 'unread' } : {},
      );
      setInitialPage(page);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('notifications.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [setInitialPage, listFilter, t]);

  useEffect(() => {
    if (isPanelOpen) void load();
  }, [isPanelOpen, load]);

  const handleFilterChange = (next: NotificationListFilter) => {
    setListFilter(next);
  };

  const displayNotifications =
    listFilter === 'unread'
      ? notifications.filter((n) => n.status === 'unread')
      : notifications;

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
      if (target instanceof Element) {
        if (target.closest('[data-ph-notifications-trigger]')) return;
        // Filter menu is portaled outside the panel; keep panel open while using it.
        if (
          target.closest('[data-ph-notifications-filter-menu]') ||
          target.closest('.rt-DropdownMenuContent')
        ) {
          return;
        }
      }
      closePanel();
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, [isPanelOpen, closePanel]);

  const onMarkRead = async (n: NotificationListItem) => {
    if (n.status === 'read' || !n._id) return;
    try {
      await NotificationsApi.markRead(n._id);
      markReadStore(n._id);
    } catch {
      setError(t('notifications.updateFailed'));
    }
  };

  const onMarkAllRead = async () => {
    if (unreadCount === 0 || markingAllRead) return;
    setMarkingAllRead(true);
    setError(null);
    try {
      await NotificationsApi.markAllRead();
      markAllReadStore();
    } catch {
      setError(t('notifications.updateFailed'));
    } finally {
      setMarkingAllRead(false);
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
        [data-ph-notifications-filter-menu],
        [data-ph-notifications-filter-menu] .rt-PopperContent {
          z-index: 9200 !important;
        }
        .rt-TooltipContent.${NOTIFICATIONS_PANEL_TOOLTIP_CLASS} {
          z-index: 9200 !important;
        }
        [data-ph-notifications-header-actions] > * {
          position: relative;
          isolation: isolate;
        }
        [data-ph-notifications-header-actions] > *:hover {
          z-index: 1;
        }
      `}</style>
      <Box
        ref={panelRef}
        data-ph-notifications-panel=""
        role="complementary"
        aria-label={t('inbox.title', { defaultValue: 'Inbox' })}
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
          gap="2"
          style={{
            padding: 'var(--space-3) var(--space-4)',
            borderBottom: '1px solid var(--olive-4)',
            flexShrink: 0,
            overflow: 'visible',
          }}
        >
          <Flex align="center" gap="2" style={{ minWidth: 0 }}>
            <MaterialIcon name="inbox" size={16} color="var(--slate-11)" />
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('inbox.title', { defaultValue: 'Inbox' })}
            </Text>
          </Flex>
          <Flex
            align="center"
            gap="2"
            data-ph-notifications-header-actions=""
            style={{ flexShrink: 0 }}
          >
            <Box style={{ display: 'inline-flex', flexShrink: 0, position: 'relative' }}>
              <Tooltip
                className={NOTIFICATIONS_PANEL_TOOLTIP_CLASS}
                content={t('notifications.markAllRead', { defaultValue: 'Mark all as read' })}
                side="bottom"
              >
                <IconButton
                  variant="ghost"
                  size="1"
                  color="gray"
                  disabled={unreadCount === 0 || markingAllRead}
                  aria-label={t('notifications.markAllRead', { defaultValue: 'Mark all as read' })}
                  onClick={() => void onMarkAllRead()}
                  style={{ cursor: unreadCount === 0 ? 'not-allowed' : 'pointer' }}
                >
                  <MaterialIcon name="done_all" size={18} color="var(--slate-11)" />
                </IconButton>
              </Tooltip>
            </Box>
            <NotificationFilterMenu value={listFilter} onChange={handleFilterChange} />
          </Flex>
        </Flex>

        {/* ── Body ────────────────────────────────────────────── */}
        <Box
          className="no-scrollbar"
          style={{ flex: 1, overflowY: 'auto' }}
        >
          {error && (
            <Text
              size="1"
              style={{
                color: 'var(--red-11)',
                padding: 'var(--space-2) var(--space-4)',
                display: 'block',
              }}
            >
              {error}
            </Text>
          )}

          {loading && displayNotifications.length === 0 ? (
            <Flex align="center" justify="center" style={{ paddingTop: 'var(--space-8)' }}>
              <Text size="2" color="gray">
                {t('notifications.loading')}
              </Text>
            </Flex>
          ) : displayNotifications.length === 0 ? (
            <Flex
              direction="column"
              align="center"
              justify="center"
              gap="2"
              style={{ paddingTop: 'var(--space-8)' }}
            >
              <MaterialIcon name="inbox" size={40} color="var(--slate-8)" />
              <Text size="2" color="gray">
                {listFilter === 'unread'
                  ? t('notifications.emptyUnread', {
                      defaultValue: 'No unread notifications',
                    })
                  : t('notifications.empty')}
              </Text>
            </Flex>
          ) : (
            <Flex direction="column">
              {displayNotifications.map((n) => (
                <NotificationRow
                  key={n._id}
                  notification={n}
                  onMarkRead={(item) => void onMarkRead(item)}
                  onDismiss={(item) => void onDismiss(item)}
                  markReadLabel={t('notifications.markRead')}
                  dismissLabel={t('notifications.dismiss')}
                />
              ))}
              {hasMore && (
                <Flex justify="center" style={{ padding: 'var(--space-3) var(--space-4)' }}>
                  <Box
                    style={{
                      display: 'inline-flex',
                      border: '1px solid var(--olive-5)',
                      borderRadius: 'var(--radius-2)',
                      padding: 'var(--space-1) var(--space-3)',
                      backgroundColor: 'var(--olive-2)',
                    }}
                  >
                    <SidebarLoadMoreButton
                      onClick={() => void loadMore()}
                      loading={isLoadingMore}
                      disabled={isLoadingMore}
                      flexStyle={{ padding: 0, justifyContent: 'center', width: 'auto' }}
                    />
                  </Box>
                </Flex>
              )}
            </Flex>
          )}
        </Box>
      </Box>
    </Theme>,
    document.body,
  );
}

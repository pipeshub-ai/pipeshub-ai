'use client';

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from 'react';
import { SidebarLoadMoreButton } from '@/app/(main)/knowledge-base/sidebar/sidebar-load-more-button';
import { createPortal } from 'react-dom';
import { Theme, Flex, Text, Box, IconButton, Tooltip } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { NotificationsApi, type NotificationListFilter, type NotificationListItem } from './api';
import { useNotificationStore, getVisibleNotifications } from './store';
import { NotificationRow, type NotificationRowAction } from './notification-row';
import {
  NotificationFilterMenu,
  NOTIFICATIONS_PANEL_TOOLTIP_CLASS,
  useNotificationFilterLabels,
} from './notification-filter-menu';
import { useTranslation } from 'react-i18next';
import { usePathname, useSearchParams } from 'next/navigation';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import {
  PANEL_COMPACT_TIME_WIDTH,
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
  const filterLabels = useNotificationFilterLabels();
  const isPanelOpen = useNotificationStore((s) => s.isPanelOpen);
  const closePanel = useNotificationStore((s) => s.closePanel);
  const notifications = useNotificationStore((s) => s.notifications);
  const hasMore = useNotificationStore((s) => s.hasMore);
  const isLoadingMore = useNotificationStore((s) => s.isLoadingMore);
  const loadMore = useNotificationStore((s) => s.loadMore);
  const ensureBackfill = useNotificationStore((s) => s.ensureBackfill);
  const setInitialPage = useNotificationStore((s) => s.setInitialPage);
  const setStats = useNotificationStore((s) => s.setStats);
  const markReadStore = useNotificationStore((s) => s.markRead);
  const markUnreadStore = useNotificationStore((s) => s.markUnread);
  const markAllReadStore = useNotificationStore((s) => s.markAllRead);
  const removeStore = useNotificationStore((s) => s.remove);
  const archiveStore = useNotificationStore((s) => s.archive);
  const unarchiveStore = useNotificationStore((s) => s.unarchive);
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
  const storedPanelWidth = useNotificationPanelWidthStore((s) => s.panelWidth);
  const setPanelWidth = useNotificationPanelWidthStore((s) => s.setPanelWidth);
  const panelWidth = Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, storedPanelWidth));
  /** Live rendered width — updates during drag (store `panelWidth` only commits on mouseup). */
  const [layoutPanelWidth, setLayoutPanelWidth] = useState(panelWidth);

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

  useLayoutEffect(() => {
    setLayoutPanelWidth(panelWidth);
  }, [panelWidth]);

  useLayoutEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    const syncWidth = () => {
      const next = el.getBoundingClientRect().width;
      setLayoutPanelWidth((prev) => (Math.abs(prev - next) < 0.5 ? prev : next));
    };
    syncWidth();
    const ro = new ResizeObserver(syncWidth);
    ro.observe(el);
    return () => ro.disconnect();
  }, [isVisible]);

  const handleResizeMouseDown = useCallback(
    (e: ReactMouseEvent) => {
      e.preventDefault();
      const el = panelRef.current;
      if (!el) return;

      const rect = el.getBoundingClientRect();

      setIsResizingPanel(true);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';

      const onMouseMove = (ev: MouseEvent) => {
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
      const w = isMobile || isNavCollapsed ? 0 : Math.round(entries[0].contentRect.width);
      el.style.left = `${w}px`;
    });

    observer.observe(slot);
    return () => observer.disconnect();
  }, [isVisible, isMobile, isNavCollapsed]);
  // ──────────────────────────────────────────────────────────────────────────

  const [loading, setLoading] = useState(false);
  const [markingAllRead, setMarkingAllRead] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pendingActionsRef = useRef(new Map<string, NotificationRowAction>());
  const [pendingActions, setPendingActions] = useState<
    Map<string, NotificationRowAction>
  >(() => new Map());

  const syncPendingActions = useCallback(() => {
    setPendingActions(new Map(pendingActionsRef.current));
  }, []);

  const beginRowAction = useCallback(
    (id: string, action: NotificationRowAction): boolean => {
      if (pendingActionsRef.current.has(id)) return false;
      pendingActionsRef.current.set(id, action);
      syncPendingActions();
      return true;
    },
    [syncPendingActions],
  );

  const endRowAction = useCallback(
    (id: string) => {
      pendingActionsRef.current.delete(id);
      syncPendingActions();
    },
    [syncPendingActions],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [page, stats] = await Promise.all([
        NotificationsApi.list(
          listFilter === 'unread'
            ? { status: 'unread' }
            : listFilter === 'archived'
            ? { status: 'archived' }
            : {},
        ),
        NotificationsApi.getStats(),
      ]);
      setInitialPage(page);
      setStats(stats);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('notifications.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [setInitialPage, setStats, listFilter, t]);

  useEffect(() => {
    if (isPanelOpen) void load();
  }, [isPanelOpen, load]);

  const handleFilterChange = (next: NotificationListFilter) => {
    setListFilter(next);
  };

  const displayNotifications = getVisibleNotifications(notifications, listFilter);

  useEffect(() => {
    if (!isPanelOpen || loading) return;
    void ensureBackfill();
  }, [isPanelOpen, loading, displayNotifications.length, hasMore, listFilter, ensureBackfill]);

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
    if (n.status === 'read' || !n._id || !beginRowAction(n._id, 'markRead')) return;
    try {
      await NotificationsApi.markRead(n._id);
      markReadStore(n._id);
      const stats = await NotificationsApi.getStats();
      setStats(stats);
    } catch {
      setError(t('notifications.updateFailed'));
    } finally {
      endRowAction(n._id);
    }
  };

  const onMarkUnread = async (n: NotificationListItem) => {
    if (n.status === 'unread' || !n._id || !beginRowAction(n._id, 'markUnread')) return;
    try {
      await NotificationsApi.markUnread(n._id);
      markUnreadStore(n._id);
      const stats = await NotificationsApi.getStats();
      setStats(stats);
    } catch {
      setError(t('notifications.updateFailed'));
    } finally {
      endRowAction(n._id);
    }
  };

  const onMarkAllRead = async () => {
    if (unreadCount === 0 || markingAllRead) return;
    setMarkingAllRead(true);
    setError(null);
    try {
      await NotificationsApi.markAllRead();
      markAllReadStore();
      const stats = await NotificationsApi.getStats();
      setStats(stats);
    } catch {
      setError(t('notifications.updateFailed'));
    } finally {
      setMarkingAllRead(false);
    }
  };

  const onDismiss = async (n: NotificationListItem) => {
    if (!n._id || !beginRowAction(n._id, 'dismiss')) return;
    try {
      await NotificationsApi.remove(n._id);
      removeStore(n._id);
      const stats = await NotificationsApi.getStats();
      setStats(stats);
    } catch {
      setError(t('notifications.removeFailed'));
    } finally {
      endRowAction(n._id);
    }
  };

  const onArchive = async (n: NotificationListItem) => {
    if (!n._id || n.status === 'archived' || !beginRowAction(n._id, 'archive')) return;
    try {
      await NotificationsApi.archive(n._id);
      archiveStore(n._id);
      const stats = await NotificationsApi.getStats();
      setStats(stats);
    } catch {
      setError(t('notifications.updateFailed'));
    } finally {
      endRowAction(n._id);
    }
  };

  const onUnarchive = async (n: NotificationListItem) => {
    if (!n._id || n.status !== 'archived' || !beginRowAction(n._id, 'unarchive')) return;
    try {
      await NotificationsApi.unarchive(n._id);
      unarchiveStore(n._id);
    } catch {
      setError(t('notifications.updateFailed'));
    } finally {
      endRowAction(n._id);
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
          pointer-events: none;
        }
        [data-ph-notifications-header-actions] > * {
          position: relative;
          isolation: isolate;
        }
        [data-ph-notifications-header-actions] > *:hover {
          z-index: 1;
        }
        [data-ph-notification-row][data-read="true"] {
          background-color: var(--gray-a2);
        }
        html.dark [data-ph-notification-row][data-read="true"] {
          background-color: transparent;
        }
        html.dark [data-ph-notification-row][data-read="false"] {
          background-color: var(--gray-a2);
        }
        [data-ph-notification-row]:hover {
          background-color: var(--olive-3);
        }
        html.dark [data-ph-notification-row][data-read="true"]:hover {
          background-color: var(--gray-a3);
        }
        html.dark [data-ph-notification-row][data-read="false"]:hover {
          background-color: var(--gray-a6);
        }
        [data-ph-notification-row-meta] {
          position: relative;
          flex-shrink: 0;
          align-self: flex-start;
          min-width: 88px;
          min-height: var(--space-5);
          display: flex;
          align-items: center;
          justify-content: flex-end;
        }
        [data-ph-notification-row] [data-ph-notification-row-actions] {
          position: absolute;
          right: 0;
          top: 50%;
          transform: translateY(-50%);
          opacity: 0;
          visibility: hidden;
          pointer-events: none;
        }
        [data-ph-notification-row]:hover [data-ph-notification-row-actions] {
          opacity: 1;
          visibility: visible;
          pointer-events: auto;
        }
        [data-ph-notification-row][data-action-pending="true"] [data-ph-notification-row-actions] {
          opacity: 1;
          visibility: visible;
          pointer-events: auto;
        }
        [data-ph-notification-row] [data-ph-notification-row-time] {
          position: absolute;
          right: 0;
          top: 50%;
          transform: translateY(-50%);
          opacity: 1;
          visibility: visible;
          white-space: nowrap;
        }
        [data-ph-notification-row]:hover [data-ph-notification-row-time] {
          opacity: 0;
          visibility: hidden;
          pointer-events: none;
        }
        [data-ph-notification-row][data-action-pending="true"] [data-ph-notification-row-time] {
          opacity: 0;
          visibility: hidden;
          pointer-events: none;
        }
        [data-ph-notification-row] [data-ph-notification-row-title-link] {
          text-decoration: none;
        }
        [data-ph-notification-row] [data-ph-notification-row-title-link]:hover {
          text-decoration: underline;
        }
      `}</style>
      <Box
        ref={panelRef}
        data-ph-notifications-panel=""
        role="complementary"
        aria-label={t('nav.inbox')}
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
            <MaterialIcon name="inbox" size={20} color="var(--slate-11)" />
            <Flex direction="column" gap="0" style={{ minWidth: 0 }}>
              <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                {t('nav.inbox')}
              </Text>
              <Text
                size="1"
                truncate
                style={{
                  color: 'var(--gray-11)',
                  opacity: 0.5,
                }}
              >
                {filterLabels[listFilter]}
              </Text>
            </Flex>
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
                content={t('notifications.markAllRead')}
                side="bottom"
              >
                <IconButton
                  variant="ghost"
                  size="1"
                  color="gray"
                  disabled={unreadCount === 0 || markingAllRead}
                  aria-label={t('notifications.markAllRead')}
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
          ) : displayNotifications.length === 0 && (hasMore || isLoadingMore) ? (
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
                  ? t('notifications.emptyUnread')
                  : listFilter === 'archived'
                  ? t('notifications.emptyArchived')
                  : t('notifications.empty')}
              </Text>
            </Flex>
          ) : (
            <Flex direction="column">
              {displayNotifications.map((n) => (
                <NotificationRow
                  key={n._id}
                  notification={n}
                  compactTime={layoutPanelWidth < PANEL_COMPACT_TIME_WIDTH}
                  pendingAction={pendingActions.get(n._id) ?? null}
                  onMarkRead={(item) => void onMarkRead(item)}
                  onMarkUnread={(item) => void onMarkUnread(item)}
                  onArchive={(item) => void onArchive(item)}
                  onUnarchive={(item) => void onUnarchive(item)}
                  onDismiss={(item) => void onDismiss(item)}
                  markReadLabel={t('notifications.markRead')}
                  markUnreadLabel={t('notifications.markUnread')}
                  archiveLabel={t('notifications.archive')}
                  unarchiveLabel={t('notifications.unarchive')}
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

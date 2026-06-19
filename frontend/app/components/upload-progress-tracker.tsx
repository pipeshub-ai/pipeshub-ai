'use client';

import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { useUploadStore } from '@/lib/store/upload-store';
import type { UploadItem, UploadSession } from '@/lib/store/upload-store';

type TrackerTab = 'all' | 'completed' | 'failed';
const TRACKER_TABS: TrackerTab[] = ['all', 'completed', 'failed'];

// Format bytes to human readable
const formatSize = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

interface UploadItemRowProps {
  item: UploadItem;
}

function UploadItemRow({ item }: UploadItemRowProps) {
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  const isFailed = item.status === 'failed';
  // A file can fail for more than one reason (size, type, backend error). Show
  // them all; fall back to a generic hint when the backend gave none.
  const failureReasons =
    isFailed
      ? (item.errors?.map((e) => e?.trim()).filter((e): e is string => !!e) ?? [])
      : [];
  const resolvedReasons =
    isFailed && failureReasons.length === 0
      ? [t('uploadProgress.genericFailureHint')]
      : failureReasons;
  const failureDetail = resolvedReasons.join(' · ');

  const statusLabel = t(`uploadProgress.status.${item.status}`);
  const rowAriaLabel = isFailed
    ? failureDetail
      ? t('uploadProgress.ariaFailedWithDetail', { name: item.name, detail: failureDetail })
      : t('uploadProgress.ariaFailed', { name: item.name })
    : t('uploadProgress.ariaItemStatus', { name: item.name, status: statusLabel });

  const getStatusIcon = () => {
    switch (item.status) {
      case 'completed':
        return (
          <Box
            style={{
              borderRadius: 'var(--radius-2)',
              background: 'var(--accent-a2)',
              padding: 'var(--space-1)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <MaterialIcon name="check" size={16} color="var(--accent-11)" />
          </Box>
        );
      case 'uploading':
        return (
          <Box
            style={{
              width: '24px',
              height: '24px',
              borderRadius: '50%',
              border: '2px solid var(--slate-6)',
              borderTopColor: 'var(--slate-10)',
              animation: 'spin 1s linear infinite',
            }}
          />
        );
      case 'failed':
        return (
          <Box
            style={{
              width: '24px',
              height: '24px',
              borderRadius: 'var(--radius-2)',
              backgroundColor: 'var(--red-a3)',
              padding: 'var(--space-1)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
            aria-hidden
          >
            <MaterialIcon name="error" size={16} color="var(--red-9)" />
          </Box>
        );
      default:
        return (
          <Box
            style={{
              width: '24px',
              height: '24px',
              borderRadius: '50%',
              border: '2px solid var(--slate-6)',
            }}
          />
        );
    }
  };

  return (
    <Flex
      align="center"
      justify="between"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        width: '100%',
        padding: '12px',
        background: isHovered ? 'var(--olive-4)' : 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border:
          item.status === 'failed'
            ? '1px solid var(--red-a6)'
            : '1px solid var(--olive-3)',
        // Let the browser skip layout/paint for rows scrolled out of view, so a
        // multi-thousand-file list stays smooth. The intrinsic-size estimate
        // (taller for failed rows, which carry reason text) keeps the scrollbar
        // stable before a row is first rendered.
        contentVisibility: 'auto',
        containIntrinsicSize: isFailed ? '0 92px' : '0 58px',
      }}
      role="group"
      data-testid="upload-item-row"
      data-status={item.status}
      data-name={item.name}
      tabIndex={isFailed ? 0 : -1}
      aria-label={rowAriaLabel}
      // Native tooltip with the full failure detail (reasons can be long / wrap).
      title={isFailed ? failureDetail : undefined}
    >
      <Flex align="center" gap="3" style={{ minWidth: 0, flex: 1 }}>
        <Box
          style={{
            width: '32px',
            height: '32px',
            flexShrink: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: item.type === 'folder' ? 'var(--accent-a3)' : 'var(--slate-4)',
            borderRadius: 'var(--radius-1)',
          }}
        >
          {item.type === 'folder' ? (
            <MaterialIcon name="folder" size={16} color="var(--accent-9)" />
          ) : (
            <FileIcon filename={item.name} size={16} />
          )}
        </Box>
        <Flex direction="column" gap="1" style={{ minWidth: 0, flex: 1 }}>
          <Text
            size="2"
            title={item.name}
            style={{
              color: 'var(--slate-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.name}
          </Text>
          {isFailed ? (
            <Flex direction="column" gap="0">
              {resolvedReasons.map((reason, idx) => (
                <Text
                  key={idx}
                  size="1"
                  title={reason}
                  style={{
                    color: 'var(--orange-11)',
                    lineHeight: 1.35,
                    overflowWrap: 'anywhere',
                  }}
                >
                  {reason}
                </Text>
              ))}
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                {formatSize(item.size)}
              </Text>
            </Flex>
          ) : (
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {formatSize(item.size)}
            </Text>
          )}
        </Flex>
      </Flex>
      <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
        {getStatusIcon()}
      </Flex>
    </Flex>
  );
}

interface SessionGroup {
  uploadId: string;
  session?: UploadSession;
  items: UploadItem[];
}

// Order rows so failures and in-flight files sit above completed ones, but keep
// EVERY item — the user must be able to scroll the whole list in each tab. The
// row itself uses `content-visibility: auto` so off-screen rows are skipped by
// the browser's layout/paint, keeping even a multi-thousand-file list smooth
// without a windowing library.
function orderRows(items: UploadItem[]): UploadItem[] {
  const rank = (s: UploadItem['status']) =>
    s === 'failed' ? 0 : s === 'uploading' || s === 'pending' ? 1 : 2;
  // Stable sort by status priority; preserves arrival order within a group.
  return [...items].sort((a, b) => rank(a.status) - rank(b.status));
}

function SessionCard({ group, filter }: { group: SessionGroup; filter: TrackerTab }) {
  const { t } = useTranslation();
  const { items, session } = group;

  // Counts always reflect the whole session (so the header is stable across tabs).
  const total = items.length;
  const completed = items.filter((i) => i.status === 'completed').length;
  const failed = items.filter((i) => i.status === 'failed').length;
  const inFlight = total - completed - failed;

  // Rows are filtered by the active tab.
  const filteredItems =
    filter === 'completed'
      ? items.filter((i) => i.status === 'completed')
      : filter === 'failed'
        ? items.filter((i) => i.status === 'failed')
        : items;

  const rows = orderRows(filteredItems);

  const label = session?.label || t('uploadProgress.tab_all', { defaultValue: 'Files' });

  return (
    <Box
      style={{
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        background: 'var(--olive-1)',
        overflow: 'hidden',
      }}
    >
      {/* Session header */}
      <Flex
        align="center"
        justify="between"
        gap="2"
        style={{ padding: '8px 10px', background: 'var(--olive-2)' }}
      >
        <Flex direction="column" gap="0" style={{ minWidth: 0, flex: 1 }}>
          <Text
            size="2"
            weight="medium"
            title={label}
            style={{
              color: 'var(--slate-12)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {label}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {completed}/{total}
            {failed > 0 ? ` · ${t('uploadProgress.tab_failed', { defaultValue: 'Failed' })}: ${failed}` : ''}
            {inFlight > 0 ? ` · ${inFlight} ${t('uploadProgress.status.uploading', { defaultValue: 'uploading' })}` : ''}
          </Text>
        </Flex>
      </Flex>

      <Flex direction="column" gap="2" style={{ padding: '8px' }}>
        {rows.map((item) => (
          <UploadItemRow key={item.id} item={item} />
        ))}
      </Flex>
    </Box>
  );
}

export function UploadProgressTracker() {
  const { t } = useTranslation();
  const {
    items,
    sessions,
    isVisible,
    isCollapsed,
    totalSize,
    completedCount,
    totalCount,
    clearedCompletedCount,
    setCollapsed,
    clearAll,
  } = useUploadStore();

  const failedCount = items.reduce((n, i) => n + (i.status === 'failed' ? 1 : 0), 0);
  const completedIncludingCleared = completedCount + clearedCompletedCount;
  const [activeTab, setActiveTab] = useState<TrackerTab>('all');

  // Group items into stacked session cards (one per drag-drop), newest first, so
  // multiple concurrent uploads each show their own progress and never collide.
  const groups: SessionGroup[] = (() => {
    const byId = new Map<string, UploadItem[]>();
    for (const item of items) {
      const key = item.uploadId || '__legacy__';
      if (!byId.has(key)) byId.set(key, []);
      byId.get(key)!.push(item);
    }
    const out: SessionGroup[] = [];
    for (const [uploadId, groupItems] of byId) {
      out.push({ uploadId, session: sessions[uploadId], items: groupItems });
    }
    // Newest session first; legacy/ungrouped last.
    out.sort(
      (a, b) => (b.session?.createdAt ?? 0) - (a.session?.createdAt ?? 0),
    );
    return out;
  })();

  // When a tab filters by status, only show session cards that have a matching row.
  const visibleGroups = groups.filter((g) => {
    if (activeTab === 'completed') return g.items.some((i) => i.status === 'completed');
    if (activeTab === 'failed') return g.items.some((i) => i.status === 'failed');
    return true;
  });

  const tabCount = (tab: TrackerTab): number =>
    tab === 'completed' ? completedCount : tab === 'failed' ? failedCount : totalCount;

  const completedBytes = items.reduce(
    (s, i) => s + (i.status === 'completed' ? i.size : 0),
    0,
  );
  const failedBytes = items.reduce(
    (s, i) => s + (i.status === 'failed' ? i.size : 0),
    0,
  );

  const hasActiveUploads = items.some(
    (i) => i.status === 'uploading' || i.status === 'pending'
  );

  useEffect(() => {
    if (!hasActiveUploads) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [hasActiveUploads]);

  if (!isVisible || items.length === 0) {
    return null;
  }

  return (
    <>
      {/* Spinner animation keyframes */}
      <style jsx global>{`
        @keyframes spin {
          from {
            transform: rotate(0deg);
          }
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>

      <Box
        data-testid="upload-progress-tracker"
        style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          width: '360px',
          background: 'var(--olive-2)',
          border: '1px solid var(--olive-3)',
          borderRadius: 'var(--radius-2)',
          overflow: 'hidden',
          zIndex: 1000,
          padding: 'var(--space-4)',
        }}
      >
        {/* Header */}
        <Flex
          align="center"
          justify="between"
          style={{
            paddingBottom: 'var(--space-4)',
          }}
        >
          <Flex direction="column" gap="1">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {failedCount > 0
                ? completedIncludingCleared > 0
                  ? t('uploadProgress.headerCompletedAndFailed', {
                      completed: completedIncludingCleared,
                      failed: failedCount,
                    })
                  : t('uploadProgress.headerFailuresOnly', { count: failedCount })
                : t('uploadProgress.headerAllComplete', {
                    completed: completedCount,
                    total: totalCount,
                  })}
            </Text>
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {failedCount > 0
                ? t('uploadProgress.uploadedAndFailedSize', {
                    uploaded: formatSize(completedBytes),
                    failed: formatSize(failedBytes),
                  })
                : t('uploadProgress.totalSizeLabel', { size: formatSize(totalSize) })}
            </Text>
          </Flex>
          <Flex align="center" gap="4">
            <IconButton
              variant="ghost"
              color="gray"
              size="1"
              onClick={() => setCollapsed(!isCollapsed)}
            >
              <MaterialIcon
                name={isCollapsed ? 'expand_less' : 'expand_more'}
                size={16}
                color="var(--slate-10)"
              />
            </IconButton>
            <IconButton variant="ghost" color="gray" size="1" onClick={clearAll}>
              <MaterialIcon name="close" size={16} color="var(--slate-10)" />
            </IconButton>
          </Flex>
        </Flex>
        <Box style={{ height: '1px', background: 'var(--olive-3)' }} />

        {/* Filter tabs — view all, only completed, or only failed. */}
        {!isCollapsed && (
          <Flex gap="1" style={{ paddingTop: 'var(--space-3)' }}>
            {TRACKER_TABS.map((tab) => {
              const isActive = activeTab === tab;
              const isFailedTab = tab === 'failed';
              return (
                <Box
                  key={tab}
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setActiveTab(tab)}
                  style={{
                    cursor: 'pointer',
                    padding: '3px 10px',
                    borderRadius: '999px',
                    fontSize: '12px',
                    fontWeight: 500,
                    lineHeight: 1.5,
                    whiteSpace: 'nowrap',
                    transition: 'background-color 0.15s ease, color 0.15s ease',
                    color: isActive
                      ? 'var(--slate-1)'
                      : isFailedTab
                        ? 'var(--red-11)'
                        : 'var(--slate-11)',
                    backgroundColor: isActive
                      ? isFailedTab
                        ? 'var(--red-9)'
                        : 'var(--slate-12)'
                      : 'var(--olive-3)',
                  }}
                >
                  {t(`uploadProgress.tab_${tab}`, {
                    defaultValue: tab === 'all' ? 'All' : tab === 'completed' ? 'Completed' : 'Failed',
                  })}{' '}
                  ({tabCount(tab)})
                </Box>
              );
            })}
          </Flex>
        )}

        {/* Content - stacked session cards */}
        {!isCollapsed && (
          <Box
            className="no-scrollbar"
            style={{
              // Compact scroll area (close to the original height) so the tray
              // never dominates the viewport; the full list is still reachable
              // by scrolling within it.
              maxHeight: 'min(360px, 45vh)',
              overflowY: 'auto',
              paddingTop: 'var(--space-4)',
            }}
          >
            <Flex direction="column" gap="3">
              {visibleGroups.length === 0 ? (
                <Text
                  size="1"
                  style={{
                    color: 'var(--slate-9)',
                    textAlign: 'center',
                    padding: 'var(--space-4) 0',
                  }}
                >
                  {t('uploadProgress.emptyTab', { defaultValue: 'Nothing here yet.' })}
                </Text>
              ) : (
                visibleGroups.map((group) => (
                  <SessionCard key={group.uploadId} group={group} filter={activeTab} />
                ))
              )}
            </Flex>
          </Box>
        )}
      </Box>
    </>
  );
}

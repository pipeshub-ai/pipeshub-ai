'use client';

import React, { useMemo, useState, useRef, useEffect } from 'react';
import { Flex, Text, Button, IconButton, Box, DropdownMenu, SegmentedControl } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile, useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { MOBILE_HAMBURGER_GUTTER_PX } from '@/app/components/sidebar';
import { useKnowledgeBaseStore } from '../store';
import type { ViewMode, PageViewMode, Breadcrumb, NodeType } from '../types';
import { KbNodeNameIcon } from '../utils/kb-node-name-icon';
import { FolderIcon } from '@/app/components/ui';
import { useTranslation } from 'react-i18next';
import { ShareHeaderGroup } from '@/app/components/share';
import type { SharedAvatarMember } from '@/app/components/share';
import { PermissionLockIcon } from '@/config';
import { HeaderOverflowMenu } from '@/app/(main)/workspace/components/header-overflow-menu';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';


// Radix offsets ghost buttons by their own padding; these set their own, so the
// offset only pushes the hover box into the neighbouring control.
const HEADER_TEXT_BUTTON: React.CSSProperties = {
  cursor: 'pointer',
  height: 28,
  minHeight: 28,
  boxSizing: 'border-box',
  padding: '0 8px',
  gap: 6,
  margin: 0,
  fontSize: '13px',
  fontWeight: 400,
  color: 'var(--neutral-11)',
  borderRadius: 'var(--radius-2)',
};
const HEADER_ICON_BUTTON: React.CSSProperties = { cursor: 'pointer', margin: 0 };
interface KBHeaderProps {
  pageViewMode: PageViewMode;
  breadcrumbs?: Breadcrumb[];
  currentTitle?: string;
  onBreadcrumbClick?: (breadcrumb: Breadcrumb) => void;
  onInfoClick?: () => void;

  // Common actions (always shown)
  onFind: () => void;
  onRefresh: () => void;
  /** Shown when browsing inside a KB collection */
  showIndexingStatus?: boolean;
  onIndexingStatusClick?: () => void;
  /** Opens the collection's Recently deleted page; only passed while the trash is on */
  onRecentlyDeletedClick?: () => void;

  // Collections mode only actions (hidden in all-records mode)
  onCreateFolder?: () => void;
  onUpload?: () => void;
  onShare?: () => void;
  createPermissionDenied?: boolean;
  folderPermissionDenied?: boolean;
  uploadPermissionDenied?: boolean;
  sharePermissionDenied?: boolean;
  sharedMembers?: SharedAvatarMember[];
  onRename?: (nodeId: string, nodeType: string, newName: string) => Promise<void>;
  isSearchActive?: boolean;
}

/** Same mark the node's row carries; crumbs below an app inherit its connector. */
function CrumbIcon({ crumb, connector, size }: { crumb: Breadcrumb; connector?: string; size: number }) {
  if (crumb.nodeType === 'all-records') {
    return <FolderIcon variant="default" size={size} color="var(--emerald-11)" />;
  }
  return (
    <KbNodeNameIcon
      isKnowledgeHub
      nodeType={crumb.nodeType as NodeType}
      connector={crumb.nodeType === 'app' ? crumb.subType : connector}
      subType={crumb.subType}
      name={crumb.name}
      size={size}
    />
  );
}

function BreadcrumbItem({
  crumb,
  connector,
  isLast,
  onClick,
  onInfoClick,
  onRename,
}: {
  crumb: Breadcrumb;
  connector?: string;
  isLast: boolean;
  onClick?: () => void;
  onInfoClick?: () => void;
  onRename?: (nodeId: string, nodeType: string, newName: string) => Promise<void>;
}) {
  const isClickable = !isLast && !!onClick;
  const canRename = isLast && !!onRename && crumb.nodeType !== 'kb' && crumb.nodeType !== 'all-records';

  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditing]);

  const startEditing = () => {
    setEditValue(crumb.name);
    setIsEditing(true);
  };

  const handleSave = async () => {
    const trimmed = editValue.trim();
    if (!trimmed || trimmed === crumb.name) {
      setIsEditing(false);
      return;
    }
    try {
      await onRename!(crumb.id, crumb.nodeType, trimmed);
    } catch {
      // Error toast handled by parent; revert
    }
    setIsEditing(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSave();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      setIsEditing(false);
    }
  };

  return (
    <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
      <CrumbIcon crumb={crumb} connector={connector} size={18} />
      {isEditing ? (
        <input
          ref={inputRef}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={handleSave}
          onKeyDown={handleKeyDown}
          style={{
            font: 'inherit',
            fontSize: '14px',
            fontWeight: 500,
            color: 'var(--slate-12)',
            backgroundColor: 'var(--slate-1)',
            border: '1px solid var(--accent-8)',
            borderRadius: 'var(--radius-1)',
            padding: 'var(--space-1) var(--space-2)',
            outline: 'none',
            minWidth: '100px',
          }}
        />
      ) : (
        <Text
          size="2"
          weight="medium"
          onClick={canRename ? startEditing : (isClickable ? onClick : undefined)}
          style={{
            color: isLast ? 'var(--slate-12)' : 'var(--slate-11)',
            cursor: canRename ? 'text' : (isClickable ? 'pointer' : 'default'),
            whiteSpace: 'nowrap',
          }}
          onMouseEnter={(e) => {
            if (isClickable) {
              (e.target as HTMLElement).style.textDecoration = 'underline';
            }
          }}
          onMouseLeave={(e) => {
            if (isClickable) {
              (e.target as HTMLElement).style.textDecoration = 'none';
            }
          }}
        >
          {crumb.name}
        </Text>
      )}
      {isLast && !isEditing && crumb.nodeType !== 'all-records' && (
        <Box
          onClick={(e) => {
            e.stopPropagation();
            onInfoClick?.();
          }}
          style={{
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            flexShrink: 0,
          }}
        >
          <MaterialIcon name="info" size={16} color="var(--emerald-11)" />
        </Box>
      )}
    </Flex>
  );
}

function BreadcrumbSeparator() {
  return <MaterialIcon name="chevron_right" size={16} color="var(--slate-8)" style={{ flexShrink: 0 }} />;
}

export function Header({
  pageViewMode,
  breadcrumbs,
  currentTitle: _currentTitle,
  onBreadcrumbClick,
  onInfoClick,
  onFind,
  onRefresh,
  showIndexingStatus,
  onIndexingStatusClick,
  onRecentlyDeletedClick,
  onCreateFolder,
  onUpload,
  onShare,
  createPermissionDenied,
  folderPermissionDenied,
  uploadPermissionDenied,
  sharePermissionDenied,
  sharedMembers = [],
  onRename,
  isSearchActive,
}: KBHeaderProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  // Tablets fold the secondary actions too, so the collection name keeps room.
  const foldActions = useIsBelowDesktop();
  const { viewMode, setViewMode, tableData } = useKnowledgeBaseStore();
  const isCollectionsMode = pageViewMode === 'collections';

  // Compute visible and hidden breadcrumbs for truncation
  const { visibleBefore, hiddenCrumbs, visibleAfter } = useMemo(() => {
    if (!breadcrumbs || breadcrumbs.length === 0) {
      return { visibleBefore: [], hiddenCrumbs: [], visibleAfter: [] };
    }
    if (breadcrumbs.length <= 4) {
      return { visibleBefore: breadcrumbs, hiddenCrumbs: [], visibleAfter: [] };
    }
    // > 4: show first + ... + last 3
    return {
      visibleBefore: [breadcrumbs[0]],
      hiddenCrumbs: breadcrumbs.slice(1, breadcrumbs.length - 3),
      visibleAfter: breadcrumbs.slice(breadcrumbs.length - 3),
    };
  }, [breadcrumbs]);


  const trailConnector = breadcrumbs?.find((c) => c.nodeType === 'app')?.subType;

  const renderBreadcrumbs = () => {
    // if (!breadcrumbs || breadcrumbs.length === 0) {
    //   return (
    //     <Flex align="center" gap="2">
    //       <FolderIcon variant="default" size={18} color="var(--emerald-11)" />
    //       <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
    //         {currentTitle}
    //       </Text>
    //     </Flex>
    //   );
    // }

    // The root has no trail; name the page so the toolbar is not blank.
    if (!breadcrumbs || breadcrumbs.length === 0) {
      return (
        <Text size="2" weight="medium" style={{ color: 'var(--slate-12)', whiteSpace: 'nowrap' }}>
          {isCollectionsMode ? t('nav.collections') : t('nav.allRecords')}
        </Text>
      );
    }

    const items: React.ReactNode[] = [];

    // Render visible before (first item, or all items if <= 4)
    visibleBefore.forEach((crumb, index) => {
      const isLast = hiddenCrumbs.length === 0 && visibleAfter.length === 0 && index === visibleBefore.length - 1;
      items.push(
        <BreadcrumbItem
          key={crumb.id}
          crumb={crumb}
          connector={trailConnector}
          isLast={isLast}
          onClick={() => onBreadcrumbClick?.(crumb)}
          onInfoClick={onInfoClick}
          onRename={onRename}
        />
      );
      // Add separator after unless it's the very last breadcrumb
      if (!isLast) {
        items.push(<BreadcrumbSeparator key={`sep-${crumb.id}`} />);
      }
    });

    // Render "..." dropdown for hidden crumbs
    if (hiddenCrumbs.length > 0) {
      items.push(
        <DropdownMenu.Root key="hidden-crumbs">
          <DropdownMenu.Trigger>
            <Box
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                padding: 'var(--space-1) var(--space-2)',
                borderRadius: 'var(--radius-2)',
                backgroundColor: 'var(--slate-a3)',
                flexShrink: 0,
              }}
            >
              <Text size="2" weight="medium" style={{ color: 'var(--slate-11)', lineHeight: 1 }}>
                ···
              </Text>
            </Box>
          </DropdownMenu.Trigger>
          <DropdownMenu.Content align="start">
            {hiddenCrumbs.map((crumb) => (
              <DropdownMenu.Item
                key={crumb.id}
                onClick={() => onBreadcrumbClick?.(crumb)}
              >
                <CrumbIcon crumb={crumb} connector={trailConnector} size={16} />
                {crumb.name}
              </DropdownMenu.Item>
            ))}
          </DropdownMenu.Content>
        </DropdownMenu.Root>
      );
      items.push(<BreadcrumbSeparator key="sep-hidden" />);
    }

    // Render visible after (last 3 items when truncated)
    visibleAfter.forEach((crumb, index) => {
      const isLast = index === visibleAfter.length - 1;
      items.push(
        <BreadcrumbItem
          key={crumb.id}
          crumb={crumb}
          connector={trailConnector}
          isLast={isLast}
          onClick={() => onBreadcrumbClick?.(crumb)}
          onInfoClick={onInfoClick}
          onRename={onRename}
        />
      );
      if (!isLast) {
        items.push(<BreadcrumbSeparator key={`sep-${crumb.id}`} />);
      }
    });

    return items;
  };

  return (
    <Flex
      align="center"
      justify="between"
      gap={isMobile ? '2' : '3'}
      style={{
        height: '40px',
        // Leave room on the left for the fixed mobile hamburger button so the
        // breadcrumb does not sit underneath it.
        padding: isMobile ? `0 var(--space-2) 0 ${MOBILE_HAMBURGER_GUTTER_PX}px` : '0 var(--space-3)',
        borderBottom: '1px solid var(--olive-3)',
        backdropFilter: 'var(--frost-filter-subtle)',
        backgroundColor: 'var(--effects-translucent)',
        flexShrink: 0,
      }}
    >
      {/* Left: Breadcrumbs — on mobile, scroll horizontally so every ancestor
          stays tappable instead of getting clipped by overflow: hidden. */}
      <Flex
        align="center"
        gap="2"
        wrap="nowrap"
        className={isMobile ? 'no-scrollbar' : undefined}
        style={{
          overflowX: isMobile ? 'auto' : 'hidden',
          overflowY: 'hidden',
          minWidth: isMobile ? 0 : 160,
          flex: 1,
        }}
      >
        {/* Restores a collapsed nav sidebar; without it the app-shell fallback floats over the breadcrumb */}
        <SidebarExpandButton placement="inline" />
        {isSearchActive ? (
          <Flex align="center" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('kb.searchResults')}
            </Text>
          </Flex>
        ) : (
          renderBreadcrumbs()
        )}
      </Flex>

      {/* Right: Actions */}
      <Flex align="center" gap={isMobile ? '1' : '4'} style={{ flexShrink: 0 }}>
        {/* Common actions - always shown. Icon-only on mobile to save room. */}
        <Flex align="center" gap={isMobile ? '1' : '2'}>
          {isMobile ? (
            <IconButton className="touch-hit" variant="ghost" size="3" color="gray" onClick={onFind} style={HEADER_ICON_BUTTON} aria-label={t('action.find')}>
              <MaterialIcon name="search" size={18} color="var(--slate-11)" />
            </IconButton>
          ) : (
            <Button variant="ghost" size="1" color="gray" onClick={onFind} style={HEADER_TEXT_BUTTON}>
              <MaterialIcon name="search" size={14} color="var(--neutral-11)" />
              {t('action.find')}
            </Button>
          )}
          {!foldActions && (
            <Button variant="ghost" size="1" color="gray" onClick={onRefresh} style={HEADER_TEXT_BUTTON}>
              <MaterialIcon name="refresh" size={14} color="var(--neutral-11)" />
              {t('action.refresh')}
            </Button>
          )}
          {!foldActions && showIndexingStatus && onIndexingStatusClick && (
            <Button
              variant="ghost"
              size="1"
              color="gray"
              onClick={onIndexingStatusClick}
              style={HEADER_TEXT_BUTTON}
            >
              <MaterialIcon name="analytics" size={14} color="var(--slate-11)" />
              {t('collections.stats.menuLabel', { defaultValue: 'Analytics' })}
            </Button>
          )}
          {!foldActions && onRecentlyDeletedClick && (
            <Button
              variant="ghost"
              size="1"
              color="gray"
              onClick={onRecentlyDeletedClick}
              style={HEADER_TEXT_BUTTON}
            >
              <MaterialIcon name="delete_outline" size={14} color="var(--slate-11)" />
              {t('collections.trash.menuLabel')}
            </Button>
          )}
        </Flex>

        {/* Collections mode only actions */}
        {isCollectionsMode && (
          <>
            {/* New button with dropdown — icon-only on mobile */}
            {tableData?.permissions?.canCreateFolders !== false &&
              (onCreateFolder || onUpload) && (
              <DropdownMenu.Root>
                <DropdownMenu.Trigger>
                  {isMobile ? (
                    <IconButton
                      className="touch-hit"
                      variant="solid"
                      size="3"
                      style={{ cursor: createPermissionDenied ? 'not-allowed' : 'pointer' }}
                      aria-label={t('action.new')}
                      data-testid="new-dropdown-trigger"
                      disabled={createPermissionDenied}
                    >
                      <MaterialIcon name="add" size={18} color="white" />
                      {createPermissionDenied && <PermissionLockIcon />}
                    </IconButton>
                  ) : (
                    <Button
                      variant="solid"
                      size="1"
                      style={{ cursor: createPermissionDenied ? 'not-allowed' : 'pointer' }}
                      data-testid="new-dropdown-trigger"
                      disabled={createPermissionDenied}
                    >
                      <MaterialIcon name="add" size={16} color="white" />
                      {t('action.new')}
                      {createPermissionDenied && <PermissionLockIcon />}
                    </Button>
                  )}
                </DropdownMenu.Trigger>
                <DropdownMenu.Content align="end">
                  {onCreateFolder && (
                    <DropdownMenu.Item
                      disabled={folderPermissionDenied}
                      onClick={() => {
                        if (folderPermissionDenied) return;
                        onCreateFolder();
                      }}
                    >
                      <Flex align="center" gap="2">
                        <MaterialIcon name="create_new_folder" size={16} color="var(--slate-11)" />
                        <Text size="2">{t('kb.newFolder')}</Text>
                        {folderPermissionDenied && <PermissionLockIcon />}
                      </Flex>
                    </DropdownMenu.Item>
                  )}
                  {onUpload && (
                    <DropdownMenu.Item
                      disabled={uploadPermissionDenied}
                      onClick={() => {
                        if (uploadPermissionDenied) return;
                        onUpload();
                      }}
                    >
                      <Flex align="center" gap="2">
                        <MaterialIcon name="file_upload" size={16} color="var(--slate-11)" />
                        <Text size="2">{t('dialog.uploadData')}</Text>
                        {uploadPermissionDenied && <PermissionLockIcon />}
                      </Flex>
                    </DropdownMenu.Item>
                  )}
                </DropdownMenu.Content>
              </DropdownMenu.Root>
            )}

            {/* Share: the full avatar group on desktop, a single icon button on phones */}
            {onShare && (isMobile ? (
              <Flex align="center" gap="1">
                <IconButton
                  className="touch-hit"
                  variant="ghost"
                  size="3"
                  color="gray"
                  onClick={sharePermissionDenied ? () => undefined : onShare}
                  aria-disabled={sharePermissionDenied || undefined}
                  style={HEADER_ICON_BUTTON}
                  aria-label={t('dialog.shareCollection')}
                >
                  <MaterialIcon name="person_add" size={18} color="var(--slate-11)" />
                </IconButton>
                {sharePermissionDenied && <PermissionLockIcon />}
              </Flex>
            ) : (
              <Flex align="center" gap="1">
                <ShareHeaderGroup
                  members={sharedMembers}
                  onShareClick={sharePermissionDenied ? () => undefined : onShare}
                />
                {sharePermissionDenied && <PermissionLockIcon />}
              </Flex>
            ))}
          </>
        )}

        {/* Below desktop: secondary actions and the view toggle live in one overflow menu so the title keeps room */}
        {foldActions ? (
          <HeaderOverflowMenu
            items={[
              { label: t('action.refresh'), icon: 'refresh', onSelect: onRefresh },
              ...(showIndexingStatus && onIndexingStatusClick
                ? [{ label: t('collections.stats.menuLabel', { defaultValue: 'Analytics' }), icon: 'analytics', onSelect: onIndexingStatusClick }]
                : []),
              ...(onRecentlyDeletedClick
                ? [{ label: t('collections.trash.menuLabel'), icon: 'delete_outline', onSelect: onRecentlyDeletedClick }]
                : []),
              viewMode === 'grid'
                ? { label: t('artifacts.listView'), icon: 'view_list', onSelect: () => setViewMode('list') }
                : { label: t('artifacts.gridView'), icon: 'grid_on', onSelect: () => setViewMode('grid') },
            ]}
          />
        ) : (
          <SegmentedControl.Root
            value={viewMode}
            onValueChange={(value) => setViewMode(value as ViewMode)}
            size="1"
            data-icon-only=""
          >
            <SegmentedControl.Item value="grid">
              <Flex align="center" justify="center">
                <MaterialIcon name="grid_on" size={16} />
              </Flex>
            </SegmentedControl.Item>
            <SegmentedControl.Item value="list">
              <Flex align="center" justify="center">
                <MaterialIcon name="view_list" size={16} />
              </Flex>
            </SegmentedControl.Item>
          </SegmentedControl.Root>
        )}
      </Flex>
    </Flex>
  );
}

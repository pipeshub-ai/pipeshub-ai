'use client';

import React, { useMemo, useState, useRef, useEffect } from 'react';
import { Flex, Text, Button, Box, DropdownMenu, SegmentedControl } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useKnowledgeBaseStore } from '../store';
import type { ViewMode, PageViewMode, Breadcrumb } from '../types';
import { FolderIcon } from '@/app/components/ui';
import { useTranslation } from 'react-i18next';
import { ShareHeaderGroup } from '@/app/components/share';
import type { SharedAvatarMember } from '@/app/components/share';

interface KBHeaderProps {
  pageViewMode: PageViewMode;
  breadcrumbs?: Breadcrumb[];
  currentTitle?: string;
  onBreadcrumbClick?: (breadcrumb: Breadcrumb) => void;
  onInfoClick?: () => void;

  // Common actions (always shown)
  onFind: () => void;
  onRefresh: () => void;

  // Collections mode only actions (hidden in all-records mode)
  onCreateFolder?: () => void;
  onUpload?: () => void;
  onShare?: () => void;
  sharedMembers?: SharedAvatarMember[];
  onRename?: (nodeId: string, nodeType: string, newName: string) => Promise<void>;
  isSearchActive?: boolean;
}

function BreadcrumbItem({
  crumb,
  isLast,
  onClick,
  onInfoClick,
  onRename,
}: {
  crumb: Breadcrumb;
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
      {crumb.nodeType === 'all-records' ? (
        <FolderIcon variant="default" size={18} color="var(--emerald-11)" />
      ) : (
        <FolderIcon variant="default" size={18} color="var(--emerald-11)" />
      )}
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
  onCreateFolder,
  onUpload,
  onShare,
  sharedMembers = [],
  onRename,
  isSearchActive,
}: KBHeaderProps) {
  const { t } = useTranslation();
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

    const items: React.ReactNode[] = [];

    // Render visible before (first item, or all items if <= 4)
    visibleBefore.forEach((crumb, index) => {
      const isLast = hiddenCrumbs.length === 0 && visibleAfter.length === 0 && index === visibleBefore.length - 1;
      items.push(
        <BreadcrumbItem
          key={crumb.id}
          crumb={crumb}
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
                <FolderIcon variant="default" size={16} color="var(--emerald-11)" />
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
      style={{
        height: '40px',
        padding: '0 var(--space-3)',
        borderBottom: '1px solid var(--olive-3)',
        backdropFilter: 'blur(8px)',
        backgroundColor: 'var(--effects-translucent)',
        flexShrink: 0,
      }}
    >
      {/* Left: Breadcrumbs */}
      <Flex align="center" gap="2" style={{ overflow: 'hidden', minWidth: 0 }}>
        {isSearchActive ? (
          <Flex align="center" gap="2">
            <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
              Results
            </Text>
          </Flex>
        ) : (
          renderBreadcrumbs()
        )}
      </Flex>

      {/* Right: Actions */}
      <Flex align="center" gap="4" style={{ flexShrink: 0 }}>
        {/* Common actions - always shown */}
         <Flex align="center" gap="5">
        <Button variant="ghost" size="1" color="gray" onClick={onFind} style={{ cursor: 'pointer', fontSize: "14px"}}>
          <MaterialIcon name="search" size={16} color="var(--slate-11)" />
          {t('action.find')}
        </Button>
        <Button variant="ghost" size="1" color="gray" onClick={onRefresh} style={{ cursor: 'pointer', fontSize: "14px" }}>
          <MaterialIcon name="refresh" size={16} color="var(--slate-11)" />
          {t('action.refresh')}
        </Button>
        </Flex>

        {/* Collections mode only actions */}
        {isCollectionsMode && (
          <>
            {/* New button with dropdown */}
            {tableData?.permissions?.canCreateFolders !== false && (
              <DropdownMenu.Root>
                <DropdownMenu.Trigger>
                  <Button variant="solid" size="1" style={{ cursor: 'pointer' }}>
                    <MaterialIcon name="add" size={16} color="white" />
                    {t('action.new')}
                  </Button>
                </DropdownMenu.Trigger>
                <DropdownMenu.Content align="end">
                  <DropdownMenu.Item onClick={() => onCreateFolder?.()}>
                    <MaterialIcon name="create_new_folder" size={16} color="var(--slate-11)" />
                    {t('kb.newFolder')}
                  </DropdownMenu.Item>
                  <DropdownMenu.Item onClick={() => onUpload?.()}>
                    <MaterialIcon name="file_upload" size={16} color="var(--slate-11)" />
                    {t('dialog.uploadData')}
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Root>
            )}

            {onShare && (
              <ShareHeaderGroup
                members={sharedMembers}
                onShareClick={onShare}
              />
            )}
          </>
        )}

        {/* View toggle - shown in both modes */}
        <SegmentedControl.Root
          value={viewMode}
          onValueChange={(value) => setViewMode(value as ViewMode)}
          size="1"
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
      </Flex>
    </Flex>
  );
}

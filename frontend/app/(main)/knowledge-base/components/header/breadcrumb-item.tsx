'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Flex, Text, Box } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FolderIcon } from '@/app/components/ui';
import type { Breadcrumb } from '../../types';

export function BreadcrumbItem({
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
    <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
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

export function BreadcrumbSeparator() {
  return <MaterialIcon name="chevron_right" size={16} color="var(--slate-8)" style={{ flexShrink: 0 }} />;
}

'use client';

import React, { useState } from 'react';
import { Flex, Box, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';
import { useUploadStore } from '@/lib/store/upload-store';
import type { UploadItem } from '@/lib/store/upload-store';

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
  const [isHovered, setIsHovered] = useState(false);

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
        padding: '12px',
        background: isHovered ? 'var(--olive-4)' : 'var(--olive-2)',
        borderRadius: 'var(--radius-2)',
        border: '1px solid var(--olive-3)',
      }}
    >
      <Flex align="center" gap="3">
        <Box
          style={{
            width: '32px',
            height: '32px',
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
        <Flex direction="column" gap="0">
          <Text
            size="2"
            style={{
              color: 'var(--slate-12)',
              maxWidth: '180px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.name}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {formatSize(item.size)}
          </Text>
        </Flex>
      </Flex>
      {getStatusIcon()}
    </Flex>
  );
}

export function UploadProgressTracker() {
  const { items, isVisible, isCollapsed, totalSize, completedCount, totalCount, setCollapsed, clearAll } =
    useUploadStore();

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
        style={{
          position: 'fixed',
          bottom: '24px',
          right: '24px',
          width: '340px',
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
              {completedCount}/{totalCount} uploads complete
            </Text>
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              Total: {formatSize(totalSize)}
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

        {/* Content - collapsible */}
        {!isCollapsed && (
          <Box
            className="no-scrollbar"
            style={{
              maxHeight: '320px',
              overflowY: 'auto',
              paddingTop: 'var(--space-4)',
            }}
          >
            <Flex direction="column" gap="4">
              {items.map((item) => (
                <UploadItemRow key={item.id} item={item} />
              ))}
            </Flex>
          </Box>
        )}
      </Box>
    </>
  );
}

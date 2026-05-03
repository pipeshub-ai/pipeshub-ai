'use client';

import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Flex, Box, Text, IconButton, Tooltip } from '@radix-ui/themes';
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
  const { t } = useTranslation();
  const [isHovered, setIsHovered] = useState(false);

  const isFailed = item.status === 'failed';
  const failureTooltipText = isFailed
    ? item.error?.trim() || t('uploadProgress.genericFailureHint')
    : null;

  const statusLabel = t(`uploadProgress.status.${item.status}`);
  const rowAriaLabel = isFailed
    ? item.error?.trim()
      ? t('uploadProgress.ariaFailedWithDetail', { name: item.name, detail: item.error.trim() })
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

  const row = (
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
        cursor: isFailed ? 'help' : 'default',
      }}
      role="group"
      tabIndex={isFailed ? 0 : -1}
      aria-label={rowAriaLabel}
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
              color: item.status === 'failed' ? 'var(--red-11)' : 'var(--slate-12)',
              maxWidth: '180px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {item.name}
          </Text>
          <Text size="1" style={{ color: 'var(--slate-9)' }}>
            {item.status === 'failed'
              ? t('uploadProgress.sizeWithFailed', { size: formatSize(item.size) })
              : formatSize(item.size)}
          </Text>
        </Flex>
      </Flex>
      {getStatusIcon()}
    </Flex>
  );

  if (isFailed) {
    return (
      <Tooltip
        content={
          <Text
            as="span"
            size="1"
            style={{
              display: 'block',
              margin: 0,
              maxWidth: 300,
              whiteSpace: 'pre-wrap',
              lineHeight: 1.45,
            }}
          >
            {failureTooltipText}
          </Text>
        }
      >
        {row}
      </Tooltip>
    );
  }

  return row;
}

export function UploadProgressTracker() {
  const { t } = useTranslation();
  const {
    items,
    isVisible,
    isCollapsed,
    totalSize,
    completedCount,
    totalCount,
    clearedCompletedCount,
    sessionUploadFileCount,
    sessionUploadTotalSize,
    setCollapsed,
    clearAll,
  } = useUploadStore();

  const failedCount = items.reduce((n, i) => n + (i.status === 'failed' ? 1 : 0), 0);
  const completedIncludingCleared = completedCount + clearedCompletedCount;

  const sessionFileLabel =
    sessionUploadFileCount > 0 ? sessionUploadFileCount : totalCount;
  const sessionSizeLabel =
    sessionUploadFileCount > 0 ? sessionUploadTotalSize : totalSize;

  const hasActiveUploads = items.some(
    (i) => i.status === 'uploading' || i.status === 'pending'
  );

  React.useEffect(() => {
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
                ? t('uploadProgress.sessionFilesAndSize', {
                    count: sessionFileLabel,
                    size: formatSize(sessionSizeLabel),
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

'use client';

import { useState, useEffect, useCallback } from 'react';
import { Box, Flex, Text, IconButton } from '@radix-ui/themes';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { FileIcon } from '@/app/components/ui/file-icon';

import { ICON_SIZES } from '@/lib/constants/icon-sizes';
import { FilePreviewRenderer } from './renderers/file-preview-renderer';
import { CitationsPanel } from './citations-panel';
import { useCitationSync } from './use-citation-sync';
import { shouldShowPagination } from './utils';
import type { FilePreviewProps, PaginationControls } from './types';

export function FilePreviewFullscreen({
  source: _source,
  file,
  defaultTab: _defaultTab = 'preview',
  onClose,
  isLoading = false,
  recordDetails: _recordDetails,
  initialPage,
  highlightBox,
  citations,
}: FilePreviewProps) {
  const hasCitations = citations && citations.length > 0;
  const [currentPage, setCurrentPage] = useState(initialPage ?? 1);
  const [totalPages, setTotalPages] = useState<number | null>(null);

  // Calculate pagination visibility
  const paginationVisibility = shouldShowPagination(
    file.type,
    file.name,
    totalPages,
    isLoading,
    false
  );

  // Reset pagination when file changes
  useEffect(() => {
    setCurrentPage(initialPage ?? 1);
    setTotalPages(null);
  }, [file.id, file.url, initialPage]);

  // Handle page detection callback from renderer
  const handleTotalPagesDetected = useCallback((numPages: number) => {
    setTotalPages(numPages);
  }, []);

  const handlePrevPage = () => {
    setCurrentPage(prev => Math.max(1, prev - 1));
  };

  const handleNextPage = () => {
    if (totalPages !== null) {
      setCurrentPage(prev => Math.min(totalPages, prev + 1));
    }
  };

  const handlePageChange = (page: number) => {
    setCurrentPage(page);
  };

  // Bidirectional citation ↔ page sync
  const {
    activeCitationId,
    highlightBox: syncHighlightBox,
    highlightPage: syncHighlightPage,
    handleCitationClick,
  } = useCitationSync({
    citations,
    currentPage,
    onPageChange: handlePageChange,
    initialHighlightBox: highlightBox,
    initialPage,
  });

  // Create pagination controls object
  const paginationControls: PaginationControls = {
    currentPage,
    totalPages,
    onPageChange: handlePageChange,
    onTotalPagesDetected: handleTotalPagesDetected,
  };

  return (
    <Box
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'var(--color-background)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 100,
      }}
    >
      {/* Header */}
      <Flex
        align="center"
        justify="between"
        style={{
          padding: 'var(--space-2) var(--space-3)',
          borderBottom: '1px solid var(--slate-6)',
          backdropFilter: 'blur(8px)',
          backgroundColor: 'var(--color-panel-translucent)',
          height: '40px',
        }}
      >
        <Flex align="center" gap="2" style={{ flex: 1, minWidth: 0 }}>
          <FileIcon
            filename={file.name}
            mimeType={file.type}
            size={16}
            fallbackIcon="description"
          />
          <Text 
            size="2" 
            weight="medium"
            style={{
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {file.name}
          </Text>
        </Flex>

        <IconButton
          variant="ghost"
          color="gray"
          size="1"
          onClick={onClose}
          title="Close"
        >
          <MaterialIcon name="close" size={ICON_SIZES.HEADER} />
        </IconButton>
      </Flex>

      {/* Main Content Area */}
      <Flex style={{ flex: 1, overflow: 'hidden' }}>
        {/* Left Side - Document Preview */}
        <Box
          style={{
            flex: 1,
            position: 'relative',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 'var(--space-6)',
            overflow: 'auto',
            background: 'linear-gradient(180deg, var(--slate-2) 0%, var(--slate-1) 100%)',
            borderRight: hasCitations ? '1px solid var(--olive-3)' : undefined,
          }}
          className="no-scrollbar"
        >
          {isLoading ? (
            <Flex align="center" justify="center">
              <div className="loading-spinner" />
            </Flex>
          ) : (
            <FilePreviewRenderer
              fileUrl={file.url}
              fileName={file.name}
              fileType={file.type}
              pagination={paginationControls}
              highlightBox={hasCitations ? syncHighlightBox : highlightBox}
              highlightPage={hasCitations ? syncHighlightPage : undefined}
            />
          )}

          {/* Floating Pagination - Bottom Center */}
          {paginationVisibility.shouldShow && (
            <Flex
              align="center"
              justify="center"
              gap="2"
              style={{
                position: 'absolute',
                bottom: 'var(--space-6)',
                left: '50%',
                transform: 'translateX(-50%)',
                padding: 'var(--space-2)',
                backgroundColor: 'var(--color-panel-solid)',
                border: '1px solid var(--slate-3)',
                borderRadius: 'var(--radius-1)',
                boxShadow: '0px 20px 28px 0px rgba(0, 0, 0, 0.15)',
              }}
            >
              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={handlePrevPage}
                disabled={currentPage === 1}
                style={{
                  width: '24px',
                  height: '24px',
                  padding: 0,
                }}
              >
                <MaterialIcon name="chevron_left" size={ICON_SIZES.SECONDARY} />
              </IconButton>

              <Box
                style={{
                  backgroundColor: 'var(--slate-2)',
                  border: '1px solid var(--slate-3)',
                  borderRadius: 'var(--radius-1)',
                  padding: '4px 12px',
                }}
              >
                <Text size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
                  {totalPages === null ? `${currentPage}/?` : `${currentPage}/${totalPages}`}
                </Text>
              </Box>

              <IconButton
                variant="ghost"
                color="gray"
                size="1"
                onClick={handleNextPage}
                disabled={totalPages === null || currentPage === totalPages}
                style={{
                  width: '24px',
                  height: '24px',
                  padding: 0,
                }}
              >
                <MaterialIcon name="chevron_right" size={ICON_SIZES.SECONDARY} />
              </IconButton>
            </Flex>
          )}
        </Box>

        {/* Citations Panel — only when citations are provided */}
        {hasCitations && (
          <CitationsPanel
            citations={citations}
            activeCitationId={activeCitationId}
            onCitationClick={handleCitationClick}
          />
        )}


      </Flex>
    </Box>
  );
}
